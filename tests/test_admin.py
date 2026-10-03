from __future__ import annotations

from portal.storage import build_record

from .conftest import GROUP_ID, principal_header

ADMIN_GROUP = "33333333-3333-3333-3333-333333333333"


def _seed(store):
    store.add(build_record(GROUP_ID, "ok@example.com", "Ok", "invited", "", "r1"))
    store.add(build_record(GROUP_ID, "bad@example.com", "=HYPERLINK(\"x\")", "failed", "captcha_failed"))
    store.add(build_record("other-group", "old@example.com", "", "invited"))


def test_admin_requires_principal(client):
    assert client.get("/admin").status_code == 403
    assert client.get("/admin/export.csv").status_code == 403


def test_admin_forbidden_for_non_admin(client):
    resp = client.get("/admin", headers={"X-MS-CLIENT-PRINCIPAL": principal_header("eve@contoso.com")})
    assert resp.status_code == 403


def test_admin_garbage_header_forbidden(client):
    assert client.get("/admin", headers={"X-MS-CLIENT-PRINCIPAL": "%%%not-base64"}).status_code == 403


def test_admin_allowed_by_upn(client, store):
    _seed(store)
    resp = client.get("/admin", headers={"X-MS-CLIENT-PRINCIPAL": principal_header("Admin@Contoso.com")})
    assert resp.status_code == 200
    body = resp.text
    assert "ok@example.com" in body
    assert "bad@example.com" in body
    assert "old@example.com" not in body  # other cohort hidden by default
    assert "AI Cohort 2026-Q4" in body
    assert GROUP_ID in body
    assert "AI-COHORT-Q4" not in body  # never show the access code
    assert "secret" not in body.lower()


def test_admin_allowed_by_group(make_client):
    client = make_client(admin_upns=[], admin_group_id=ADMIN_GROUP)
    hdr = principal_header("someone@contoso.com", groups=[ADMIN_GROUP])
    assert client.get("/admin", headers={"X-MS-CLIENT-PRINCIPAL": hdr}).status_code == 200
    hdr = principal_header("someone@contoso.com", groups=["44444444-0000-0000-0000-000000000000"])
    assert client.get("/admin", headers={"X-MS-CLIENT-PRINCIPAL": hdr}).status_code == 403


def test_admin_filter_and_scope(client, store):
    _seed(store)
    hdr = {"X-MS-CLIENT-PRINCIPAL": principal_header("admin@contoso.com")}
    body = client.get("/admin?outcome=failed", headers=hdr).text
    assert "bad@example.com" in body and "ok@example.com" not in body
    body = client.get("/admin?scope=all", headers=hdr).text
    assert "old@example.com" in body


def test_admin_rows_newest_first(client, store):
    _seed(store)
    hdr = {"X-MS-CLIENT-PRINCIPAL": principal_header("admin@contoso.com")}
    body = client.get("/admin", headers=hdr).text
    assert body.index("bad@example.com") < body.index("ok@example.com")


def test_csv_export(client, store):
    _seed(store)
    hdr = {"X-MS-CLIENT-PRINCIPAL": principal_header("admin@contoso.com")}
    resp = client.get("/admin/export.csv?outcome=invited", headers=hdr)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("timestamp,name,email,outcome")
    assert len(lines) == 2 and "ok@example.com" in lines[1]


def test_csv_export_neutralises_formulas(client, store):
    _seed(store)
    hdr = {"X-MS-CLIENT-PRINCIPAL": principal_header("admin@contoso.com")}
    text = client.get("/admin/export.csv", headers=hdr).text
    assert "'=HYPERLINK" in text


def test_local_dev_bypass(make_client):
    assert make_client(local_dev_admin=True).get("/admin").status_code == 200


def test_local_dev_bypass_ignored_in_app_service(make_client):
    client = make_client(local_dev_admin=True, running_in_app_service=True, easy_auth_enabled=True)
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302
    assert "/.auth/login/aad" in resp.headers["Location"]


def test_header_not_trusted_without_easy_auth_in_app_service(make_client):
    client = make_client(running_in_app_service=True, easy_auth_enabled=False)
    hdr = {"X-MS-CLIENT-PRINCIPAL": principal_header("admin@contoso.com")}
    assert client.get("/admin", headers=hdr).status_code == 403


def test_easy_auth_admin_in_app_service(make_client):
    client = make_client(running_in_app_service=True, easy_auth_enabled=True)
    hdr = {"X-MS-CLIENT-PRINCIPAL": principal_header("admin@contoso.com")}
    assert client.get("/admin", headers=hdr).status_code == 200
