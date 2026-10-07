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

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from .auth import authorize_admin
from .graph import GraphError
from .middleware import BodyTooLarge
from .net import client_ip
from .schemas import RegisterError, RegisterRequest, RegisterSuccess
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

router = APIRouter()

EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

MSG_GENERIC = "Something went wrong while sending your invitation. Please try again later or contact your instructor."
MSG_BAD_EMAIL = "Please enter a valid email address."
MSG_BAD_CODE = "The cohort access code is not valid."
MSG_BAD_CAPTCHA = "We could not verify that you are human. Please complete the check and try again."
MSG_CAP = "Registrations are temporarily paused. Please try again later or contact your instructor."
MSG_UNAVAILABLE = "Registration is not available right now. Please contact your instructor."
MSG_RATE_LIMITED = "Too many attempts. Please wait a few minutes and try again."

FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


def _ctx(request: Request) -> Dict[str, Any]:
    return request.app.state.portal


def valid_email(email: str) -> bool:
    return 3 <= len(email) <= 254 and bool(EMAIL_RE.match(email))


def clean_name(name: str) -> str:
    return CONTROL_RE.sub("", name).strip()[:100]


# ---------------------------------------------------------------- public pages


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    settings = _ctx(request)["settings"]
    return request.app.state.templates.TemplateResponse(
        request, "index.html", {"site_key": settings.turnstile_site_key if settings.turnstile_enabled else ""}
    )


@router.get("/healthz")
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------- registration


async def _payload(request: Request) -> RegisterRequest:
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    data: Any = {}
    try:
        if ctype == "application/json" or ctype.endswith("+json"):
            data = await request.json()
        elif ctype in FORM_TYPES:
            data = await request.form()
    except BodyTooLarge:
        raise
    except Exception:  # malformed JSON / multipart: treat as empty
        data = {}
    if not hasattr(data, "get"):
        data = {}
    return RegisterRequest.from_payload(data)


async def _log_attempt(
    request: Request, email: str, name: str, outcome: str, error: str = "", request_id: str = ""
) -> None:
    ctx = _ctx(request)
    record = build_record(ctx["settings"].group_id, email, name, outcome, error, request_id)
    try:
        await run_in_threadpool(ctx["store"].add, record)
    except Exception:  # never fail the user flow because logging failed
        log.exception("Failed to write submission log for %s", email)


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(RegisterError(error=message).model_dump(), status_code=status)


@router.post(
    "/api/register",
    response_model=RegisterSuccess,
    responses={code: {"model": RegisterError} for code in (400, 403, 413, 429, 500, 502, 503)},
)
async def register(request: Request):
    ctx = _ctx(request)
    settings = ctx["settings"]
    ip = client_ip(request)

    if not ctx["limiter"].hit(ip):
        log.info("Rate limit exceeded for %s", ip)
        return _fail(429, MSG_RATE_LIMITED)

    data = await _payload(request)
    email, name = data.email.lower(), clean_name(data.name)

    if not valid_email(email):
        return _fail(400, MSG_BAD_EMAIL)

    missing = settings.missing_required()
    if missing:
        log.error("Registration rejected, missing configuration: %s", ", ".join(missing))
        return _fail(503, MSG_UNAVAILABLE)

    if settings.turnstile_enabled and not await ctx["turnstile"].verify(data.token, ip):
        log.info("Captcha failed for %s from %s", email, ip)
        await _log_attempt(request, email, name, OUTCOME_FAILED, "captcha_failed")
        return _fail(400, MSG_BAD_CAPTCHA)

    if not hmac.compare_digest(data.access_code.encode("utf-8"), settings.access_code.encode("utf-8")):
        log.info("Invalid access code for %s from %s", email, ip)
        await _log_attempt(request, email, name, OUTCOME_FAILED, "invalid_access_code")
        return _fail(403, MSG_BAD_CODE)

    if settings.daily_invite_cap:
        try:
            sent_today = await run_in_threadpool(
                ctx["store"].count_since, start_of_utc_day(), INVITE_SENT_OUTCOMES
            )
        except Exception:
            log.exception("Could not read daily invite count; refusing to invite")
            return _fail(503, MSG_GENERIC)
        if sent_today >= settings.daily_invite_cap:
            log.warning("Daily invite cap %s reached", settings.daily_invite_cap)
            await _log_attempt(request, email, name, OUTCOME_FAILED, "daily_cap_reached")
            return _fail(429, MSG_CAP)

    graph = ctx["graph"]
    request_id = ""
    try:
        invitation = await graph.invite(email, name, settings.invite_redirect_url, settings.invite_message)
        request_id = invitation.request_id
        membership = await graph.add_group_member(settings.group_id, invitation.user_id)
        request_id = membership.request_id or request_id
    except GraphError as exc:
        log.error(
            "Graph failure for %s: %s (status=%s, request-id=%s)",
            email, exc.summary, exc.status, exc.request_id,
        )
        await _log_attempt(request, email, name, OUTCOME_FAILED, exc.summary, exc.request_id or request_id)
        return _fail(502, MSG_GENERIC)
    except Exception as exc:
        log.exception("Unexpected error registering %s", email)
        await _log_attempt(request, email, name, OUTCOME_FAILED, f"unexpected: {type(exc).__name__}", request_id)
        return _fail(500, MSG_GENERIC)

    outcome = OUTCOME_ALREADY_MEMBER if membership.already_member else OUTCOME_INVITED
    log.info("Registered %s outcome=%s request-id=%s", email, outcome, request_id)
    await _log_attempt(request, email, name, outcome, "", request_id)
    return RegisterSuccess(
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


def _require_admin(request: Request) -> Optional[Response]:
    """None if the caller is an admin, otherwise the response to return."""
    settings = _ctx(request)["settings"]
    decision = authorize_admin(request.headers, settings)
    if decision.allowed:
        return None
    if not decision.authenticated and settings.easy_auth_enabled:
        return RedirectResponse(
            f"/.auth/login/aad?post_login_redirect_uri={request.url.path}", status_code=302
        )
    return PlainTextResponse("Forbidden", status_code=403)


async def _group_name(request: Request) -> str:
    ctx = _ctx(request)
    group_id = ctx["settings"].group_id
    cache = ctx.setdefault("group_name_cache", {})
    hit = cache.get(group_id)
    if hit and time.monotonic() - hit[1] < _GROUP_NAME_TTL:
        return hit[0]
    try:
        name = await ctx["graph"].get_group_name(group_id) if group_id else ""
    except Exception as exc:
        log.warning("Could not resolve group name: %s", exc)
        name = ""
    cache[group_id] = (name, time.monotonic())
    return name


async def _query_rows(request: Request) -> Tuple[list, Optional[str], bool]:
    ctx = _ctx(request)
    settings = ctx["settings"]
    outcome: Optional[str] = request.query_params.get("outcome") or None
    if outcome not in OUTCOMES:
        outcome = None
    all_cohorts = request.query_params.get("scope") == "all"
    partition = None if all_cohorts else (settings.group_id or "unconfigured")
    rows = await run_in_threadpool(ctx["store"].list, partition=partition, outcome=outcome, limit=2000)
    return rows, outcome, all_cohorts


def _fmt_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value or "")


@router.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    denied = _require_admin(request)
    if denied is not None:
        return denied
    settings = _ctx(request)["settings"]
    rows, outcome, all_cohorts = await _query_rows(request)
    config_view = {
        "Cohort group ID": settings.group_id or "(not set)",
        "Cohort group name": await _group_name(request) or "(unavailable)",
        "Invite redirect URL": settings.invite_redirect_url,
        "Tenant ID": settings.tenant_id or "(not set)",
        "Graph auth": "Client secret" if settings.client_secret else "Managed identity",
        "Daily invite cap": str(settings.daily_invite_cap or "unlimited"),
        "Rate limit per IP": settings.register_rate_limit,
        "Submission table": settings.table_name if settings.storage_connection_string else "(in-memory)",
    }
    return request.app.state.templates.TemplateResponse(
        request,
        "admin.html",
        {
            "rows": rows,
            "outcome": outcome,
            "outcomes": OUTCOMES,
            "all_cohorts": all_cohorts,
            "config": config_view,
            "fmt_time": _fmt_time,
        },
    )


def _csv_safe(value: Any) -> str:
    text = _fmt_time(value) if isinstance(value, datetime) else str(value or "")
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@router.get("/admin/export.csv")
async def admin_export(request: Request):
    denied = _require_admin(request)
    if denied is not None:
        return denied
    rows, _outcome, _all = await _query_rows(request)
    buf = io.StringIO()
    writer = csv.writer(buf)
    columns = ["CreatedAt", "Name", "Email", "Outcome", "ErrorSummary", "GroupId", "GraphRequestId"]
    writer.writerow(["timestamp", "name", "email", "outcome", "error_summary", "group_id", "graph_request_id"])
    for row in rows:
        writer.writerow([_csv_safe(row.get(c)) for c in columns])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=submissions.csv"},
    )
