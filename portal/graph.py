"""Minimal Microsoft Graph client for guest invitations and group membership."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TIMEOUT_SECONDS = 15


class GraphError(Exception):
    def __init__(self, summary: str, status: Optional[int] = None, request_id: str = ""):
        super().__init__(summary)
        self.summary = summary
        self.status = status
        self.request_id = request_id


@dataclass
class Invitation:
    user_id: str
    redeem_url: str
    status: str
    request_id: str


@dataclass
class MembershipResult:
    already_member: bool
    request_id: str


def build_credential(tenant_id: str, client_id: str, client_secret: str, mi_client_id: str = ""):
    """ClientSecretCredential when a secret is configured, otherwise managed identity."""
    if client_secret:
        from azure.identity import ClientSecretCredential

        return ClientSecretCredential(tenant_id, client_id, client_secret)
    from azure.identity import ManagedIdentityCredential

    if mi_client_id:
        return ManagedIdentityCredential(client_id=mi_client_id)
    return ManagedIdentityCredential()


def _request_id(resp: requests.Response) -> str:
    return resp.headers.get("request-id") or resp.headers.get("client-request-id") or ""


def _error_summary(resp: requests.Response) -> str:
    try:
        err = resp.json().get("error", {})
        code = err.get("code", "")
        message = err.get("message", "")
    except ValueError:
        code, message = "", resp.text[:200]
    return f"HTTP {resp.status_code} {code}: {message}".strip()[:500]


class GraphClient:
    def __init__(self, credential: Any, session: Optional[requests.Session] = None):
        self._credential = credential
        self._session = session or requests.Session()

    def _headers(self) -> Dict[str, str]:
        # azure-identity credentials cache tokens until shortly before expiry.
        token = self._credential.get_token(GRAPH_SCOPE).token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _call(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            return self._session.request(
                method, f"{GRAPH_BASE}{path}", headers=self._headers(), timeout=TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as exc:
            raise GraphError(f"Network error calling Graph: {type(exc).__name__}") from exc
        except Exception as exc:  # token acquisition failures (azure.core exceptions)
            raise GraphError(f"Graph auth error: {type(exc).__name__}") from exc

    def invite(
        self, email: str, display_name: str, redirect_url: str, message: str = ""
    ) -> Invitation:
        body: Dict[str, Any] = {
            "invitedUserEmailAddress": email,
            "inviteRedirectUrl": redirect_url,
            "sendInvitationMessage": True,
        }
        if display_name:
            body["invitedUserDisplayName"] = display_name
        if message:
            body["invitedUserMessageInfo"] = {"customizedMessageBody": message}
        resp = self._call("POST", "/invitations", json=body)
        rid = _request_id(resp)
        if resp.status_code not in (200, 201):
            raise GraphError(_error_summary(resp), resp.status_code, rid)
        data = resp.json()
        user_id = (data.get("invitedUser") or {}).get("id", "")
        if not user_id:
            raise GraphError("Invitation response missing invitedUser.id", resp.status_code, rid)
        return Invitation(
            user_id=user_id,
            redeem_url=data.get("inviteRedeemUrl", ""),
            status=data.get("status", ""),
            request_id=rid,
        )

    def add_group_member(self, group_id: str, user_id: str) -> MembershipResult:
        body = {"@odata.id": f"{GRAPH_BASE}/directoryObjects/{user_id}"}
        resp = self._call("POST", f"/groups/{group_id}/members/$ref", json=body)
        rid = _request_id(resp)
        if resp.status_code == 204:
            return MembershipResult(already_member=False, request_id=rid)
        if resp.status_code == 400 and "already exist" in resp.text.lower():
            return MembershipResult(already_member=True, request_id=rid)
        raise GraphError(_error_summary(resp), resp.status_code, rid)

    def get_group_name(self, group_id: str) -> str:
        resp = self._call("GET", f"/groups/{group_id}", params={"$select": "displayName"})
        if resp.status_code != 200:
            raise GraphError(_error_summary(resp), resp.status_code, _request_id(resp))
        return resp.json().get("displayName", "")
