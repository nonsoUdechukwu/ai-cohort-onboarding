from __future__ import annotations

import pytest

from portal.ratelimit import RateLimiter, parse_rate


def test_parse_rate():
    assert parse_rate("5 per 10 minutes") == (5, 600)
    assert parse_rate("5/minute") == (5, 60)
    assert parse_rate("100 per day") == (100, 86400)
    with pytest.raises(ValueError):
        parse_rate("lots")


def test_sliding_window():
    now = [0.0]
    limiter = RateLimiter("2 per 10 seconds", clock=lambda: now[0])
    assert limiter.hit("a") and limiter.hit("a")
    assert not limiter.hit("a")
    assert limiter.hit("b")
    now[0] = 10.5
    assert limiter.hit("a")
