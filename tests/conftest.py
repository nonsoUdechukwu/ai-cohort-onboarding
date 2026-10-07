from __future__ import annotations

import base64
import json
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient

from portal import create_app
from portal.config import Settings
from portal.graph import GraphError, Invitation, MembershipResult
from portal.storage import MemoryStore

ACCESS_CODE = "AI-COHORT-Q4"
GROUP_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "22222222-2222-2222-2222-222222222222"


class FakeGraph:
    def __init__(self) -> None:
        self.invites: List[dict] = []
        self.memberships: List[tuple] = []
        self.already_member = False
        self.invite_error: Optional[GraphError] = None
        self.member_error: Optional[GraphError] = None

    async def invite(self, email, display_name, redirect_url, message=""):
        self.invites.append(
            {"email": email, "name": display_name, "redirect": redirect_url, "message": message}
        )
        if self.invite_error:
            raise self.invite_error
        return Invitation(
            user_id="user-" + email,
            redeem_url="https://login.microsoftonline.com/redeem?x=1",
            status="PendingAcceptance",
            request_id="req-invite",
        )

    async def add_group_member(self, group_id, user_id):
        self.memberships.append((group_id, user_id))
        if self.member_error:
            raise self.member_error
        return MembershipResult(already_member=self.already_member, request_id="req-member")

    async def get_group_name(self, group_id):
        return "AI Cohort 2026-Q4"


class FakeTurnstile:
    def __init__(self) -> None:
        self.result = True
        self.calls: List[tuple] = []

    async def verify(self, token, remote_ip=""):
        self.calls.append((token, remote_ip))
        return self.result and bool(token)


def make_settings(**overrides) -> Settings:
    base = dict(
        tenant_id=TENANT_ID,
        client_id="cid",
        group_id=GROUP_ID,
        access_code=ACCESS_CODE,
        invite_redirect_url=f"https://portal.azure.com/{TENANT_ID}",
        invite_message="Welcome!",
        turnstile_site_key="site",
        turnstile_secret_key="secret",
        admin_upns=["admin@contoso.com"],
        admin_group_id="",
        daily_invite_cap=100,
        register_rate_limit="5 per 10 minutes",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def graph():
    return FakeGraph()


@pytest.fixture
def turnstile():
    return FakeTurnstile()


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def make_client(graph, turnstile, store):
    def _make(**overrides):
        app = create_app(make_settings(**overrides), graph=graph, store=store, turnstile=turnstile)
        return TestClient(app)

    return _make


@pytest.fixture
def client(make_client):
    return make_client()


def valid_form(**overrides):
    data = {
        "email": "Student@Example.com",
        "name": "Ada Lovelace",
        "access_code": ACCESS_CODE,
        "cf-turnstile-response": "token",
    }
    data.update(overrides)
    return data


def principal_header(upn: str = "", groups: Optional[List[str]] = None) -> str:
    claims = []
    if upn:
        claims.append({"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn", "val": upn})
    for g in groups or []:
        claims.append({"typ": "groups", "val": g})
    payload = {"auth_typ": "aad", "claims": claims, "name_typ": "name", "role_typ": "roles"}
    return base64.b64encode(json.dumps(payload).encode()).decode()
