from datetime import datetime, timezone

from azure.data.tables._serialize import _parameter_filter_substitution

from portal.storage import TableStore


class _CapturingTable:
    def __init__(self):
        self.calls = []

    def query_entities(self, query, parameters=None, **kwargs):
        self.calls.append((query, parameters))
        # Run the SDK's real parameter substitution so malformed filters fail like in Azure.
        _parameter_filter_substitution(parameters, query)
        return iter([{"PartitionKey": "g"}, {"PartitionKey": "g"}])

    def list_entities(self, **kwargs):
        return iter([])


def _store_with(table):
    store = TableStore.__new__(TableStore)
    store._table = table
    return store


def test_count_since_builds_filter_the_sdk_can_substitute():
    table = _CapturingTable()
    store = _store_with(table)
    since = datetime(2026, 10, 7, tzinfo=timezone.utc)

    assert store.count_since(since, ["invited", "already_member"]) == 2

    query, params = table.calls[0]
    resolved = _parameter_filter_substitution(params, query)
    assert "Outcome eq 'invited'" in resolved
    assert "Outcome eq 'already_member'" in resolved
    assert "@" not in resolved


def test_count_since_single_outcome():
    table = _CapturingTable()
    store = _store_with(table)
    assert store.count_since(datetime(2026, 10, 7, tzinfo=timezone.utc), ["invited"]) == 2
