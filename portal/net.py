"""Client IP resolution behind the App Service front end."""
from __future__ import annotations

from starlette.requests import Request


def normalize_ip(addr: str) -> str:
    """Strip the ':port' App Service may append to X-Forwarded-For entries."""
    addr = (addr or "").strip()
    if addr.startswith("[") and "]" in addr:  # [ipv6]:port
        return addr[1 : addr.index("]")]
    if addr.count(":") == 1:  # ipv4:port
        return addr.split(":", 1)[0]
    return addr


def client_ip(request: Request) -> str:
    """Equivalent of werkzeug ProxyFix(x_for=1): trust the last X-Forwarded-For hop.

    App Service's front end always appends the real client address as the last entry.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        addr = forwarded.split(",")[-1]
    else:
        addr = request.client.host if request.client else ""
    return normalize_ip(addr) or "unknown"
