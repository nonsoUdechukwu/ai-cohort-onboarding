"""Minimal async Microsoft Graph client for guest invitations and group membership."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

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
    """Async ClientSecretCredential when a secret is configured, otherwise managed identity."""
    if client_secret:
        from azure.identity.aio import ClientSecretCredential

        return ClientSecretCredential(tenant_id, client_id, client_secret)
    from azure.identity.aio import ManagedIdentityCredential

    if mi_client_id:
        return ManagedIdentityCredential(client_id=mi_client_id)
    return ManagedIdentityCredential()


def _request_id(resp: httpx.Response) -> str:
    return resp.headers.get("request-id") or resp.headers.get("client-request-id") or ""


def _error_summary(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        code = err.get("code", "")
        message = err.get("message", "")
    except (ValueError, AttributeError):
        code, message = "", resp.text[:200]
    return f"HTTP {resp.status_code} {code}: {message}".strip()[:500]


class GraphClient:
    def __init__(self, credential: Any, client: Optional[httpx.AsyncClient] = None):
        self._credential = credential
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def _headers(self) -> Dict[str, str]:
        # azure-identity credentials cache tokens until shortly before expiry.
        token = (await self._credential.get_token(GRAPH_SCOPE)).token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            headers = await self._headers()
        except Exception as exc:  # token acquisition failures (azure.core exceptions)
            raise GraphError(f"Graph auth error: {type(exc).__name__}") from exc
        try:
            return await self._client.request(method, f"{GRAPH_BASE}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise GraphError(f"Network error calling Graph: {type(exc).__name__}") from exc

    async def invite(
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
        resp = await self._call("POST", "/invitations", json=body)
        rid = _request_id(resp)
        if resp.status_code not in (200, 201):
            raise GraphError(_error_summary(resp), resp.status_code, rid)
        try:
            data = resp.json()
        except ValueError as exc:
            raise GraphError("Invitation response was not JSON", resp.status_code, rid) from exc
        user_id = (data.get("invitedUser") or {}).get("id", "")
        if not user_id:
            raise GraphError("Invitation response missing invitedUser.id", resp.status_code, rid)
        return Invitation(
            user_id=user_id,
            redeem_url=data.get("inviteRedeemUrl", ""),
            status=data.get("status", ""),
            request_id=rid,
        )

    async def add_group_member(self, group_id: str, user_id: str) -> MembershipResult:
        body = {"@odata.id": f"{GRAPH_BASE}/directoryObjects/{user_id}"}
        resp = await self._call("POST", f"/groups/{group_id}/members/$ref", json=body)
        rid = _request_id(resp)
        if resp.status_code == 204:
            return MembershipResult(already_member=False, request_id=rid)
        if resp.status_code == 400 and "already exist" in resp.text.lower():
            return MembershipResult(already_member=True, request_id=rid)
        raise GraphError(_error_summary(resp), resp.status_code, rid)

    async def get_group_name(self, group_id: str) -> str:
        resp = await self._call("GET", f"/groups/{group_id}", params={"$select": "displayName"})
        if resp.status_code != 200:
            raise GraphError(_error_summary(resp), resp.status_code, _request_id(resp))
        return resp.json().get("displayName", "")

    async def aclose(self) -> None:
        await self._client.aclose()
        close = getattr(self._credential, "close", None)
        if close is not None:
            await close()
