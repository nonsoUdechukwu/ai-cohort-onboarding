"""Cloudflare Turnstile server-side verification."""
from __future__ import annotations

import logging
from typing import Iterable, Optional

import httpx

log = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 10
MAX_TOKEN_LENGTH = 2048
REGISTER_ACTION = "register"


class TurnstileVerifier:
    def __init__(
        self,
        secret_key: str,
        client: Optional[httpx.AsyncClient] = None,
        expected_hostnames: Iterable[str] = (),
        expected_action: str = REGISTER_ACTION,
    ):
        self._secret = secret_key
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        self._hostnames = {h.strip().lower() for h in expected_hostnames if h and h.strip()}
        self._action = expected_action

    async def verify(self, token: str, remote_ip: str = "") -> bool:
        if not token or len(token) > MAX_TOKEN_LENGTH or not self._secret:
            return False
        data = {"secret": self._secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        try:
            resp = await self._client.post(SITEVERIFY_URL, data=data)
            resp.raise_for_status()
            result = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Turnstile verification error: %s", type(exc).__name__)
            return False
        if not isinstance(result, dict) or result.get("success") is not True:
            log.info("Turnstile rejected token: %s", result.get("error-codes") if isinstance(result, dict) else None)
            return False
        # Cloudflare's public test secrets return no action and hostname "example.com";
        # a real secret never sets this flag, so production always gets the full checks.
        metadata = result.get("metadata")
        if isinstance(metadata, dict) and metadata.get("result_with_testing_key") is True:
            return True
        if result.get("action") != self._action:
            log.info("Turnstile action mismatch: %r", result.get("action"))
            return False
        if not self._hostnames:
            log.error("TURNSTILE_HOSTNAMES is not set; rejecting Turnstile token")
            return False
        if str(result.get("hostname", "")).lower() not in self._hostnames:
            log.info("Turnstile hostname mismatch: %r", result.get("hostname"))
            return False
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
