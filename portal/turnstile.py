"""Cloudflare Turnstile server-side verification."""
from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileVerifier:
    def __init__(self, secret_key: str, session: Optional[requests.Session] = None):
        self._secret = secret_key
        self._session = session or requests.Session()

    def verify(self, token: str, remote_ip: str = "") -> bool:
        if not token or not self._secret:
            return False
        data = {"secret": self._secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        try:
            resp = self._session.post(SITEVERIFY_URL, data=data, timeout=10)
            result = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Turnstile verification error: %s", type(exc).__name__)
            return False
        if not result.get("success"):
            log.info("Turnstile rejected token: %s", result.get("error-codes"))
            return False
        return True
