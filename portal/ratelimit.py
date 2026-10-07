"""Small in-memory sliding-window rate limiter keyed by client IP.

In-memory state is fine: F1 runs a single instance and gunicorn runs one worker process.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Tuple

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
_RATE_RE = re.compile(r"^\s*(\d+)\s*(?:per|/)\s*(\d+)?\s*(second|minute|hour|day)s?\s*$", re.IGNORECASE)


def parse_rate(rate: str) -> Tuple[int, float]:
    """Parse ``"5 per 10 minutes"`` / ``"5/minute"`` into ``(limit, window_seconds)``."""
    match = _RATE_RE.match(rate or "")
    if not match:
        raise ValueError(f"Invalid rate limit: {rate!r}")
    limit, multiplier, unit = match.groups()
    return int(limit), int(multiplier or 1) * _UNITS[unit.lower()]


class RateLimiter:
    def __init__(self, rate: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window = parse_rate(rate)
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record a request for ``key``; return False if it exceeds the limit."""
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory: drop keys with no recent hits
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                    del self._hits[k]
            return True
