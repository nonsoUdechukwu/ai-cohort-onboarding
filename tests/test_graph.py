from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from portal.config import Settings
from portal.graph import GRAPH_BASE, GraphClient, GraphError, build_credential
from portal.turnstile import SITEVERIFY_URL, TurnstileVerifier

pytestmark = pytest.mark.anyio


class FakeCredential:
    def __init__(self, error: Exception = None):
        self.error = error
        self.closed = False

    async def get_token(self, scope):
        assert scope == "https://graph.microsoft.com/.default"
        if self.error:
            raise self.error
        return SimpleNamespace(token="tok")

    async def close(self):
        self.closed = True


def _graph(credential=None) -> GraphClient:
    return GraphClient(credential or FakeCredential(), httpx.AsyncClient())


@respx.mock
async def test_invite_payload_and_parsing():
    route = respx.post(f"{GRAPH_BASE}/invitations").respond(
        201,
        json={"invitedUser": {"id": "uid"}, "inviteRedeemUrl": "https://redeem", "status": "PendingAcceptance"},
        headers={"request-id": "rid"},
    )
    inv = await _graph().invite("a@b.com", "Ada", "https://portal.azure.com/t", "Hi")
    assert inv.user_id == "uid" and inv.redeem_url == "https://redeem" and inv.request_id == "rid"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer tok"
    assert json.loads(request.content) == {
        "invitedUserEmailAddress": "a@b.com",
        "inviteRedirectUrl": "https://portal.azure.com/t",
        "sendInvitationMessage": True,
        "invitedUserDisplayName": "Ada",
        "invitedUserMessageInfo": {"customizedMessageBody": "Hi"},
    }


@respx.mock
async def test_invite_omits_optional_fields():
    route = respx.post(f"{GRAPH_BASE}/invitations").respond(201, json={"invitedUser": {"id": "uid"}})
    await _graph().invite("a@b.com", "", "https://x")
    body = json.loads(route.calls.last.request.content)
    assert "invitedUserDisplayName" not in body and "invitedUserMessageInfo" not in body


@respx.mock
async def test_invite_error_raises():
    respx.post(f"{GRAPH_BASE}/invitations").respond(
        403, json={"error": {"code": "Denied", "message": "nope"}}, headers={"request-id": "rid"}
    )
    with pytest.raises(GraphError) as exc:
        await _graph().invite("a@b.com", "", "https://x")
    assert exc.value.status == 403 and "Denied" in exc.value.summary and exc.value.request_id == "rid"


@respx.mock
async def test_invite_missing_user_id_raises():
    respx.post(f"{GRAPH_BASE}/invitations").respond(201, json={"status": "PendingAcceptance"})
    with pytest.raises(GraphError, match="invitedUser.id"):
        await _graph().invite("a@b.com", "", "https://x")


@respx.mock
async def test_add_member_success_and_already_exists():
    already = httpx.Response(
        400,
        json={"error": {"code": "Request_BadRequest", "message": "One or more added object references already exist for the following modified properties: 'members'."}},
    )
    route = respx.post(f"{GRAPH_BASE}/groups/g/members/$ref").mock(
        side_effect=[httpx.Response(204), already]
    )
    client = _graph()
    assert (await client.add_group_member("g", "u")).already_member is False
    assert (await client.add_group_member("g", "u")).already_member is True
    assert json.loads(route.calls[0].request.content) == {
        "@odata.id": "https://graph.microsoft.com/v1.0/directoryObjects/u"
    }


@respx.mock
async def test_add_member_other_400_fails():
    respx.post(f"{GRAPH_BASE}/groups/g/members/$ref").respond(
        400, json={"error": {"code": "Request_BadRequest", "message": "Invalid"}}
    )
    with pytest.raises(GraphError):
        await _graph().add_group_member("g", "u")


@respx.mock
async def test_network_and_auth_errors_become_graph_errors():
    respx.post(f"{GRAPH_BASE}/invitations").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(GraphError, match="Network error"):
        await _graph().invite("a@b.com", "", "https://x")
    with pytest.raises(GraphError, match="Graph auth error"):
        await _graph(FakeCredential(RuntimeError("no token"))).invite("a@b.com", "", "https://x")


@respx.mock
async def test_get_group_name_and_close():
    route = respx.get(f"{GRAPH_BASE}/groups/g").respond(200, json={"displayName": "Cohort"})
    cred = FakeCredential()
    client = _graph(cred)
    assert await client.get_group_name("g") == "Cohort"
    assert route.calls.last.request.url.params["$select"] == "displayName"
    await client.aclose()
    assert cred.closed is True


async def test_build_credential_selects_type():
    from azure.identity.aio import ClientSecretCredential, ManagedIdentityCredential

    secret = build_credential("t", "c", "s")
    managed = build_credential("t", "c", "")
    user_assigned = build_credential("t", "c", "", "mi-client")
    try:
        assert isinstance(secret, ClientSecretCredential)
        assert isinstance(managed, ManagedIdentityCredential)
        assert isinstance(user_assigned, ManagedIdentityCredential)
    finally:
        for cred in (secret, managed, user_assigned):
            await cred.close()


@respx.mock
async def test_turnstile_verify():
    ok = {"success": True, "action": "register", "hostname": "aicohort.azurewebsites.net"}
    route = respx.post(SITEVERIFY_URL).mock(
        side_effect=[
            httpx.Response(200, json=ok),
            httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-response"]}),
            httpx.ConnectError("down"),
            httpx.Response(200, text="not json"),
            httpx.Response(500, json=ok),
            httpx.Response(200, json={**ok, "action": "login"}),
            httpx.Response(200, json={**ok, "hostname": "evil.example"}),
        ]
    )
    verifier = TurnstileVerifier(
        "secret", httpx.AsyncClient(), expected_hostnames=["AICOHORT.azurewebsites.net"]
    )
    assert await verifier.verify("tok", "1.2.3.4") is True
    assert parse_qs(route.calls[0].request.content.decode()) == {
        "secret": ["secret"], "response": ["tok"], "remoteip": ["1.2.3.4"]
    }
    for _ in range(6):
        assert await verifier.verify("tok") is False
    assert await verifier.verify("") is False
    assert await verifier.verify("x" * 2049) is False
    assert await TurnstileVerifier("", httpx.AsyncClient()).verify("tok") is False
    assert route.call_count == 7  # no call for empty/oversized token or missing secret


@respx.mock
async def test_turnstile_requires_hostnames_for_real_keys():
    respx.post(SITEVERIFY_URL).mock(
        return_value=httpx.Response(200, json={"success": True, "action": "register", "hostname": "a.b"})
    )
    assert await TurnstileVerifier("secret", httpx.AsyncClient()).verify("tok") is False


@respx.mock
async def test_turnstile_testing_key_skips_action_and_hostname():
    respx.post(SITEVERIFY_URL).mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "hostname": "example.com", "metadata": {"result_with_testing_key": True}},
        )
    )
    assert await TurnstileVerifier("secret", httpx.AsyncClient()).verify("tok") is True


def test_settings_from_env_defaults():
    s = Settings.from_env({"TENANT_ID": "tid", "ADMIN_UPNS": " A@x.com, b@y.com ,", "LOCAL_DEV_ADMIN": "true"})
    assert s.invite_redirect_url == "https://portal.azure.com/tid"
    assert s.table_name == "submissions"
    assert s.admin_upns == ["a@x.com", "b@y.com"]
    assert s.local_dev_admin is True
    assert s.daily_invite_cap == 100
    assert s.register_rate_limit == "5 per 10 minutes"
    assert s.easy_auth_enabled is False and s.running_in_app_service is False
    assert "ACCESS_CODE" in s.missing_required()
    assert "secret" not in repr(Settings(client_secret="secret", access_code="secret"))


def test_settings_from_env_app_service_and_bad_values():
    s = Settings.from_env(
        {
            "WEBSITE_SITE_NAME": "my-app",
            "WEBSITE_AUTH_ENABLED": "True",
            "DAILY_INVITE_CAP": "not-a-number",
            "LOCAL_DEV_ADMIN": "maybe",
            "TABLE_NAME": "",
            "ADMIN_GROUP_ID": " ABC-DEF ",
            "CLIENT_SECRET": "s",
        }
    )
    assert s.running_in_app_service is True and s.easy_auth_enabled is True
    assert s.daily_invite_cap == 100
    assert s.local_dev_admin is False
    assert s.table_name == "submissions"
    assert s.admin_group_id == "abc-def"
    assert s.invite_redirect_url == "https://portal.azure.com/"
    assert "CLIENT_ID" in s.missing_required()
    assert Settings.from_env({"DAILY_INVITE_CAP": "-5"}).daily_invite_cap == 0


def test_settings_reads_process_environment(monkeypatch):
    monkeypatch.setenv("TENANT_ID", "env-tenant")
    monkeypatch.setenv("GROUP_ID", "env-group")
    monkeypatch.setenv("ADMIN_UPNS", "Boss@Contoso.com")
    monkeypatch.setenv("DAILY_INVITE_CAP", "7")
    monkeypatch.setenv("WEBSITE_SITE_NAME", "site")
    s = Settings.from_env()
    assert (s.tenant_id, s.group_id, s.daily_invite_cap) == ("env-tenant", "env-group", 7)
    assert s.admin_upns == ["boss@contoso.com"]
    assert s.running_in_app_service is True
    assert s.invite_redirect_url == "https://portal.azure.com/env-tenant"


def test_turnstile_hostnames_from_env():
    from portal.config import Settings

    s = Settings.from_env({"TURNSTILE_HOSTNAMES": " AiCohort.azurewebsites.net , ,localhost"})
    assert s.turnstile_hostnames == ["aicohort.azurewebsites.net", "localhost"]
