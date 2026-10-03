"""HTTP routes: student form, registration API and admin pages."""
from __future__ import annotations

import csv
import hmac
import io
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request

from . import client_ip
from .auth import authorize_admin
from .graph import GraphError
from .storage import (
    INVITE_SENT_OUTCOMES,
    OUTCOME_ALREADY_MEMBER,
    OUTCOME_FAILED,
    OUTCOME_INVITED,
    OUTCOMES,
    build_record,
    start_of_utc_day,
)

log = logging.getLogger(__name__)

main_bp = Blueprint("main", __name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")

EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

MSG_GENERIC = "Something went wrong while sending your invitation. Please try again later or contact your instructor."
MSG_BAD_EMAIL = "Please enter a valid email address."
MSG_BAD_CODE = "The cohort access code is not valid."
MSG_BAD_CAPTCHA = "We could not verify that you are human. Please complete the check and try again."
MSG_CAP = "Registrations are temporarily paused. Please try again later or contact your instructor."
MSG_UNAVAILABLE = "Registration is not available right now. Please contact your instructor."


def _ctx() -> Dict[str, Any]:
    return current_app.extensions["portal"]


def valid_email(email: str) -> bool:
    return 3 <= len(email) <= 254 and bool(EMAIL_RE.match(email))


def clean_name(name: str) -> str:
    return CONTROL_RE.sub("", name).strip()[:100]


# ---------------------------------------------------------------- public pages


@main_bp.get("/")
def index():
    settings = _ctx()["settings"]
    return render_template("index.html", site_key=settings.turnstile_site_key)


@main_bp.get("/healthz")
def healthz():
    return jsonify(status="ok")


# ---------------------------------------------------------------- registration


def _payload() -> Dict[str, str]:
    data = request.get_json(silent=True) if request.is_json else request.form
    data = data or {}

    def get(key: str) -> str:
        value = data.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    return {
        "email": get("email"),
        "name": get("name"),
        "access_code": get("access_code"),
        "token": get("cf-turnstile-response") or get("turnstile_token"),
    }


def _log_attempt(email: str, name: str, outcome: str, error: str = "", request_id: str = "") -> None:
    ctx = _ctx()
    record = build_record(ctx["settings"].group_id, email, name, outcome, error, request_id)
    try:
        ctx["store"].add(record)
    except Exception:  # never fail the user flow because logging failed
        log.exception("Failed to write submission log for %s", email)


def _fail(status: int, message: str) -> Tuple[Response, int]:
    return jsonify(ok=False, error=message), status


@api_bp.post("/register")
def register():
    ctx = _ctx()
    settings = ctx["settings"]
    data = _payload()
    email, name = data["email"].lower(), clean_name(data["name"])
    ip = client_ip()

    if not valid_email(email):
        return _fail(400, MSG_BAD_EMAIL)

    missing = settings.missing_required()
    if missing:
        log.error("Registration rejected, missing configuration: %s", ", ".join(missing))
        return _fail(503, MSG_UNAVAILABLE)

    if not ctx["turnstile"].verify(data["token"], ip):
        log.info("Captcha failed for %s from %s", email, ip)
        _log_attempt(email, name, OUTCOME_FAILED, "captcha_failed")
        return _fail(400, MSG_BAD_CAPTCHA)

    if not hmac.compare_digest(
        data["access_code"].encode("utf-8"), settings.access_code.encode("utf-8")
    ):
        log.info("Invalid access code for %s from %s", email, ip)
        _log_attempt(email, name, OUTCOME_FAILED, "invalid_access_code")
        return _fail(403, MSG_BAD_CODE)

    if settings.daily_invite_cap:
        try:
            sent_today = ctx["store"].count_since(start_of_utc_day(), INVITE_SENT_OUTCOMES)
        except Exception:
            log.exception("Could not read daily invite count; refusing to invite")
            return _fail(503, MSG_GENERIC)
        if sent_today >= settings.daily_invite_cap:
            log.warning("Daily invite cap %s reached", settings.daily_invite_cap)
            _log_attempt(email, name, OUTCOME_FAILED, "daily_cap_reached")
            return _fail(429, MSG_CAP)

    graph = ctx["graph"]
    request_id = ""
    try:
        invitation = graph.invite(
            email, name, settings.invite_redirect_url, settings.invite_message
        )
        request_id = invitation.request_id
        membership = graph.add_group_member(settings.group_id, invitation.user_id)
        request_id = membership.request_id or request_id
    except GraphError as exc:
        log.error(
            "Graph failure for %s: %s (status=%s, request-id=%s)",
            email, exc.summary, exc.status, exc.request_id,
        )
        _log_attempt(email, name, OUTCOME_FAILED, exc.summary, exc.request_id or request_id)
        return _fail(502, MSG_GENERIC)
    except Exception as exc:
        log.exception("Unexpected error registering %s", email)
        _log_attempt(email, name, OUTCOME_FAILED, f"unexpected: {type(exc).__name__}", request_id)
        return _fail(500, MSG_GENERIC)

    outcome = OUTCOME_ALREADY_MEMBER if membership.already_member else OUTCOME_INVITED
    log.info("Registered %s outcome=%s request-id=%s", email, outcome, request_id)
    _log_attempt(email, name, outcome, "", request_id)
    return jsonify(
        ok=True,
        email=email,
        outcome=outcome,
        redeemUrl=invitation.redeem_url,
        message=(
            f"Invitation sent to {email}. Check your inbox (and spam) for an email "
            "from Microsoft and accept it."
        ),
    )


# ---------------------------------------------------------------- admin

_GROUP_NAME_TTL = 600.0


def _require_admin() -> Optional[Any]:
    decision = authorize_admin(request.headers, _ctx()["settings"])
    if decision.allowed:
        return None
    settings = _ctx()["settings"]
    if not decision.authenticated and settings.easy_auth_enabled:
        return redirect(f"/.auth/login/aad?post_login_redirect_uri={request.path}")
    abort(403)


def _group_name() -> str:
    ctx = _ctx()
    group_id = ctx["settings"].group_id
    cache = ctx.setdefault("group_name_cache", {})
    hit = cache.get(group_id)
    if hit and time.monotonic() - hit[1] < _GROUP_NAME_TTL:
        return hit[0]
    try:
        name = ctx["graph"].get_group_name(group_id) if group_id else ""
    except Exception as exc:
        log.warning("Could not resolve group name: %s", exc)
        name = ""
    cache[group_id] = (name, time.monotonic())
    return name


def _query_rows():
    settings = _ctx()["settings"]
    outcome = request.args.get("outcome") or None
    if outcome not in OUTCOMES:
        outcome = None
    all_cohorts = request.args.get("scope") == "all"
    partition = None if all_cohorts else (settings.group_id or "unconfigured")
    rows = _ctx()["store"].list(partition=partition, outcome=outcome, limit=2000)
    return rows, outcome, all_cohorts


def _fmt_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value or "")


@main_bp.get("/admin")
def admin():
    denied = _require_admin()
    if denied is not None:
        return denied
    settings = _ctx()["settings"]
    rows, outcome, all_cohorts = _query_rows()
    config_view = {
        "Cohort group ID": settings.group_id or "(not set)",
        "Cohort group name": _group_name() or "(unavailable)",
        "Invite redirect URL": settings.invite_redirect_url,
        "Tenant ID": settings.tenant_id or "(not set)",
        "Graph auth": "Client secret" if settings.client_secret else "Managed identity",
        "Daily invite cap": str(settings.daily_invite_cap or "unlimited"),
        "Rate limit per IP": settings.register_rate_limit,
        "Submission table": settings.table_name if settings.storage_connection_string else "(in-memory)",
    }
    return render_template(
        "admin.html",
        rows=rows,
        outcome=outcome,
        outcomes=OUTCOMES,
        all_cohorts=all_cohorts,
        config=config_view,
        fmt_time=_fmt_time,
    )


def _csv_safe(value: Any) -> str:
    text = _fmt_time(value) if isinstance(value, datetime) else str(value or "")
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@main_bp.get("/admin/export.csv")
def admin_export():
    denied = _require_admin()
    if denied is not None:
        return denied
    rows, _outcome, _all = _query_rows()
    buf = io.StringIO()
    writer = csv.writer(buf)
    columns = ["CreatedAt", "Name", "Email", "Outcome", "ErrorSummary", "GroupId", "GraphRequestId"]
    writer.writerow(["timestamp", "name", "email", "outcome", "error_summary", "group_id", "graph_request_id"])
    for row in rows:
        writer.writerow([_csv_safe(row.get(c)) for c in columns])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=submissions.csv"},
    )
