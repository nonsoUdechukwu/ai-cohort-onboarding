"""Cloudflare Turnstile server-side verification."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 10


class TurnstileVerifier:
    def __init__(self, secret_key: str, client: Optional[httpx.AsyncClient] = None):
        self._secret = secret_key
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def verify(self, token: str, remote_ip: str = "") -> bool:
        if not token or not self._secret:
            return False
        data = {"secret": self._secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        try:
            resp = await self._client.post(SITEVERIFY_URL, data=data)
            result = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Turnstile verification error: %s", type(exc).__name__)
            return False
        if not isinstance(result, dict) or not result.get("success"):
            log.info("Turnstile rejected token: %s", result.get("error-codes") if isinstance(result, dict) else None)
            return False
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
