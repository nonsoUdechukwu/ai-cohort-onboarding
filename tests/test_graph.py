from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from portal.config import Settings
from portal.graph import GraphClient, GraphError, build_credential
from portal.turnstile import TurnstileVerifier


class FakeResponse:
    def __init__(self, status, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")
        self.headers = headers or {"request-id": "rid"}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeCredential:
    def get_token(self, scope):
        assert scope == "https://graph.microsoft.com/.default"
        return SimpleNamespace(token="tok")


def test_invite_payload_and_parsing():
    session = FakeSession(
        [FakeResponse(201, {"invitedUser": {"id": "uid"}, "inviteRedeemUrl": "https://redeem", "status": "PendingAcceptance"})]
    )
    client = GraphClient(FakeCredential(), session)
    inv = client.invite("a@b.com", "Ada", "https://portal.azure.com/t", "Hi")
    assert inv.user_id == "uid" and inv.redeem_url == "https://redeem" and inv.request_id == "rid"
    method, url, kwargs = session.calls[0]
    assert url == "https://graph.microsoft.com/v1.0/invitations"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["json"] == {
        "invitedUserEmailAddress": "a@b.com",
        "inviteRedirectUrl": "https://portal.azure.com/t",
        "sendInvitationMessage": True,
        "invitedUserDisplayName": "Ada",
        "invitedUserMessageInfo": {"customizedMessageBody": "Hi"},
    }


def test_invite_error_raises():
    session = FakeSession([FakeResponse(403, {"error": {"code": "Denied", "message": "nope"}})])
    with pytest.raises(GraphError) as exc:
        GraphClient(FakeCredential(), session).invite("a@b.com", "", "https://x")
    assert exc.value.status == 403 and "Denied" in exc.value.summary


def test_add_member_success_and_already_exists():
    already = FakeResponse(
        400,
        {"error": {"code": "Request_BadRequest", "message": "One or more added object references already exist for the following modified properties: 'members'."}},
    )
    session = FakeSession([FakeResponse(204, text=""), already])
    client = GraphClient(FakeCredential(), session)
    assert client.add_group_member("g", "u").already_member is False
    assert client.add_group_member("g", "u").already_member is True
    _, url, kwargs = session.calls[0]
    assert url.endswith("/groups/g/members/$ref")
    assert kwargs["json"] == {"@odata.id": "https://graph.microsoft.com/v1.0/directoryObjects/u"}


def test_add_member_other_400_fails():
    session = FakeSession([FakeResponse(400, {"error": {"code": "Request_BadRequest", "message": "Invalid"}})])
    with pytest.raises(GraphError):
        GraphClient(FakeCredential(), session).add_group_member("g", "u")


def test_build_credential_selects_type():
    from azure.identity import ClientSecretCredential, ManagedIdentityCredential

    assert isinstance(build_credential("t", "c", "s"), ClientSecretCredential)
    assert isinstance(build_credential("t", "c", ""), ManagedIdentityCredential)


def test_turnstile_verify():
    ok = FakeSession([FakeResponse(200, {"success": True})])
    assert TurnstileVerifier("secret", ok).verify("tok", "1.2.3.4") is True
    assert ok.calls[0][2]["data"] == {"secret": "secret", "response": "tok", "remoteip": "1.2.3.4"}
    assert TurnstileVerifier("secret", FakeSession([FakeResponse(200, {"success": False})])).verify("tok") is False
    assert TurnstileVerifier("secret", FakeSession([requests.ConnectionError()])).verify("tok") is False
    assert TurnstileVerifier("secret", FakeSession([])).verify("") is False


def test_settings_from_env_defaults():
    s = Settings.from_env({"TENANT_ID": "tid", "ADMIN_UPNS": " A@x.com, b@y.com ,", "LOCAL_DEV_ADMIN": "true"})
    assert s.invite_redirect_url == "https://portal.azure.com/tid"
    assert s.table_name == "submissions"
    assert s.admin_upns == ["a@x.com", "b@y.com"]
    assert s.local_dev_admin is True
    assert s.daily_invite_cap == 100
    assert "ACCESS_CODE" in s.missing_required()
    assert "secret" not in repr(Settings(client_secret="secret", access_code="secret"))
