from __future__ import annotations

from portal.graph import GraphError
from portal.storage import build_record

from .conftest import ACCESS_CODE, GROUP_ID, TENANT_ID, valid_form


def test_index_renders_form(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="email"' in body
    assert 'name="access_code"' in body
    assert 'data-sitekey="site"' in body
    assert "Content-Security-Policy" in resp.headers


def test_success_invites_and_adds_to_group(client, graph, store):
    resp = client.post("/api/register", data=valid_form())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["outcome"] == "invited"
    assert data["redeemUrl"].startswith("https://")
    assert "student@example.com" in data["message"]

    assert graph.invites == [
        {
            "email": "student@example.com",
            "name": "Ada Lovelace",
            "redirect": f"https://portal.azure.com/{TENANT_ID}",
            "message": "Welcome!",
        }
    ]
    assert graph.memberships == [(GROUP_ID, "user-student@example.com")]

    rows = store.list()
    assert len(rows) == 1
    row = rows[0]
    assert row["PartitionKey"] == GROUP_ID
    assert row["Outcome"] == "invited"
    assert row["GraphRequestId"] == "req-member"
    assert row["Email"] == "student@example.com"


def test_json_body_supported(client):
    resp = client.post("/api/register", json=valid_form())
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_already_member_is_success(client, graph, store):
    graph.already_member = True
    resp = client.post("/api/register", data=valid_form())
    assert resp.status_code == 200
    assert resp.get_json()["outcome"] == "already_member"
    assert store.list()[0]["Outcome"] == "already_member"


def test_resubmission_is_idempotent(client, graph, store):
    assert client.post("/api/register", data=valid_form()).status_code == 200
    graph.already_member = True
    resp = client.post("/api/register", data=valid_form())
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert [r["Outcome"] for r in store.list()] == ["already_member", "invited"]


def test_bad_access_code_rejected(client, graph, store):
    resp = client.post("/api/register", data=valid_form(access_code="wrong"))
    assert resp.status_code == 403
    assert resp.get_json()["ok"] is False
    assert graph.invites == []
    row = store.list()[0]
    assert row["Outcome"] == "failed"
    assert row["ErrorSummary"] == "invalid_access_code"
    assert "wrong" not in str(row)
    assert ACCESS_CODE not in str(row)


def test_bad_captcha_rejected(client, graph, turnstile, store):
    turnstile.result = False
    resp = client.post("/api/register", data=valid_form())
    assert resp.status_code == 400
    assert "verify" in resp.get_json()["error"]
    assert graph.invites == []
    assert store.list()[0]["ErrorSummary"] == "captcha_failed"


def test_missing_captcha_token_rejected(client, graph):
    resp = client.post("/api/register", data=valid_form(**{"cf-turnstile-response": ""}))
    assert resp.status_code == 400
    assert graph.invites == []


def test_invalid_email_rejected(client, graph, store):
    resp = client.post("/api/register", data=valid_form(email="not-an-email"))
    assert resp.status_code == 400
    assert graph.invites == []
    assert store.list() == []


def test_rate_limit_per_ip(client):
    for _ in range(5):
        assert client.post("/api/register", data=valid_form(access_code="x")).status_code == 403
    resp = client.post("/api/register", data=valid_form())
    assert resp.status_code == 429
    assert resp.get_json()["ok"] is False


def test_rate_limit_is_per_ip(client):
    for _ in range(5):
        client.post("/api/register", data=valid_form(), environ_base={"REMOTE_ADDR": "10.0.0.1"})
    blocked = client.post("/api/register", data=valid_form(), environ_base={"REMOTE_ADDR": "10.0.0.1"})
    other = client.post("/api/register", data=valid_form(), environ_base={"REMOTE_ADDR": "10.0.0.2"})
    assert blocked.status_code == 429
    assert other.status_code == 200


def test_forwarded_ip_with_port_is_used(client, turnstile):
    client.post(
        "/api/register", data=valid_form(), headers={"X-Forwarded-For": "203.0.113.7:51234"}
    )
    assert turnstile.calls[-1][1] == "203.0.113.7"


def test_daily_cap_blocks_invites(make_client, graph, store):
    client = make_client(daily_invite_cap=2)
    store.add(build_record(GROUP_ID, "a@example.com", "", "invited"))
    store.add(build_record(GROUP_ID, "b@example.com", "", "already_member"))
    resp = client.post("/api/register", data=valid_form())
    assert resp.status_code == 429
    assert graph.invites == []
    assert store.list()[0]["ErrorSummary"] == "daily_cap_reached"


def test_failed_attempts_do_not_count_toward_cap(make_client, store):
    client = make_client(daily_invite_cap=1)
    store.add(build_record(GROUP_ID, "a@example.com", "", "failed", "captcha_failed"))
    assert client.post("/api/register", data=valid_form()).status_code == 200


def test_graph_invite_failure_is_generic(client, graph, store):
    graph.invite_error = GraphError("HTTP 403 Authorization_RequestDenied: secret detail", 403, "rid-1")
    resp = client.post("/api/register", data=valid_form())
    assert resp.status_code == 502
    assert "secret detail" not in resp.get_data(as_text=True)
    row = store.list()[0]
    assert row["Outcome"] == "failed"
    assert "Authorization_RequestDenied" in row["ErrorSummary"]
    assert row["GraphRequestId"] == "rid-1"


def test_group_add_failure_is_failed(client, graph, store):
    graph.member_error = GraphError("HTTP 404 Request_ResourceNotFound", 404, "rid-2")
    resp = client.post("/api/register", data=valid_form())
    assert resp.status_code == 502
    assert store.list()[0]["Outcome"] == "failed"


def test_missing_config_returns_503(make_client, graph):
    client = make_client(group_id="")
    resp = client.post("/api/register", data=valid_form())
    assert resp.status_code == 503
    assert graph.invites == []


def test_storage_failure_does_not_break_registration(make_client, store, monkeypatch):
    client = make_client(daily_invite_cap=0)

    def boom(_record):
        raise RuntimeError("table down")

    monkeypatch.setattr(store, "add", boom)
    assert client.post("/api/register", data=valid_form()).status_code == 200
