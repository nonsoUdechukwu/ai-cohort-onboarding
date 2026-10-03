"""Submission log: Azure Table Storage, with an in-memory fallback for local dev/tests."""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

OUTCOME_INVITED = "invited"
OUTCOME_ALREADY_MEMBER = "already_member"
OUTCOME_FAILED = "failed"
OUTCOMES = (OUTCOME_INVITED, OUTCOME_ALREADY_MEMBER, OUTCOME_FAILED)
# Outcomes where Graph actually sent an invitation email (counted towards the daily cap).
INVITE_SENT_OUTCOMES = (OUTCOME_INVITED, OUTCOME_ALREADY_MEMBER)

_MAX_TICKS = 10**19


def new_row_key(now: Optional[datetime] = None) -> str:
    """Inverted microsecond timestamp so ascending RowKey order == newest first."""
    now = now or datetime.now(timezone.utc)
    ticks = int(now.timestamp() * 1_000_000)
    return f"{_MAX_TICKS - ticks:019d}_{uuid.uuid4().hex[:8]}"


def build_record(
    group_id: str,
    email: str,
    name: str,
    outcome: str,
    error_summary: str = "",
    request_id: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    return {
        "PartitionKey": group_id or "unconfigured",
        "RowKey": new_row_key(now),
        "CreatedAt": now,
        "Email": email,
        "Name": name,
        "Outcome": outcome,
        "ErrorSummary": error_summary[:1000],
        "GraphRequestId": request_id,
        "GroupId": group_id,
    }


def start_of_utc_day(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class MemoryStore:
    def __init__(self) -> None:
        self._rows: List[Dict[str, object]] = []
        self._lock = threading.Lock()

    def add(self, record: Dict[str, object]) -> None:
        with self._lock:
            self._rows.append(dict(record))

    def list(
        self, partition: Optional[str] = None, outcome: Optional[str] = None, limit: int = 1000
    ) -> List[Dict[str, object]]:
        with self._lock:
            rows = [
                r
                for r in self._rows
                if (partition is None or r["PartitionKey"] == partition)
                and (outcome is None or r["Outcome"] == outcome)
            ]
        rows.sort(key=lambda r: r["CreatedAt"], reverse=True)
        return rows[:limit]

    def count_since(self, since: datetime, outcomes: Iterable[str]) -> int:
        wanted = set(outcomes)
        with self._lock:
            return sum(1 for r in self._rows if r["Outcome"] in wanted and r["CreatedAt"] >= since)


class TableStore:
    def __init__(self, connection_string: str, table_name: str) -> None:
        from azure.data.tables import TableServiceClient

        service = TableServiceClient.from_connection_string(connection_string)
        self._table = service.create_table_if_not_exists(table_name)

    def add(self, record: Dict[str, object]) -> None:
        self._table.create_entity(entity=record)

    def list(
        self, partition: Optional[str] = None, outcome: Optional[str] = None, limit: int = 1000
    ) -> List[Dict[str, object]]:
        clauses, params = [], {}
        if partition is not None:
            clauses.append("PartitionKey eq @pk")
            params["pk"] = partition
        if outcome is not None:
            clauses.append("Outcome eq @outcome")
            params["outcome"] = outcome
        if clauses:
            entities = self._table.query_entities(" and ".join(clauses), parameters=params)
        else:
            entities = self._table.list_entities()
        rows = []
        for entity in entities:
            rows.append(dict(entity))
            if partition is not None and len(rows) >= limit:
                break  # single partition is already returned newest first
        rows.sort(key=lambda r: r.get("CreatedAt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return rows[:limit]

    def count_since(self, since: datetime, outcomes: Iterable[str]) -> int:
        outcomes = list(outcomes)
        params: Dict[str, object] = {"since": since}
        ors = []
        for i, value in enumerate(outcomes):
            params[f"o{i}"] = value
            ors.append(f"Outcome eq @o{i}")
        query = f"CreatedAt ge @since and ({' or '.join(ors)})"
        entities = self._table.query_entities(query, parameters=params, select=["PartitionKey"])
        return sum(1 for _ in entities)


def create_store(connection_string: str, table_name: str):
    if connection_string:
        return TableStore(connection_string, table_name)
    log.warning("STORAGE_CONNECTION_STRING not set: using in-memory submission log (not persisted).")
    return MemoryStore()

