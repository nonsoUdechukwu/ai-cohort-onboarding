"""Admin authorization based on App Service Authentication (Easy Auth)."""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .config import Settings

log = logging.getLogger(__name__)

PRINCIPAL_HEADER = "X-MS-CLIENT-PRINCIPAL"

UPN_CLAIM_TYPES = (
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
    "upn",
    "preferred_username",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "email",
)
GROUP_CLAIM_TYPES = ("groups", "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups")


@dataclass
class Principal:
    names: List[str] = field(default_factory=list)  # lower-cased UPN/email candidates
    groups: List[str] = field(default_factory=list)  # lower-cased group object IDs

    @property
    def display(self) -> str:
        return self.names[0] if self.names else "unknown"


def parse_principal(header_value: Optional[str]) -> Optional[Principal]:
    if not header_value:
        return None
    try:
        padded = header_value + "=" * (-len(header_value) % 4)
        data = json.loads(base64.b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        log.warning("Could not decode %s header", PRINCIPAL_HEADER)
        return None
    claims = data.get("claims") or []
    name_claim_type = data.get("name_typ")
    names: List[str] = []
    groups: List[str] = []
    for claim in claims:
        typ = (claim.get("typ") or "").strip()
        val = (claim.get("val") or "").strip().lower()
        if not val:
            continue
        if typ in UPN_CLAIM_TYPES or (name_claim_type and typ == name_claim_type):
            if val not in names:
                names.append(val)
        elif typ in GROUP_CLAIM_TYPES:
            groups.append(val)
    return Principal(names=names, groups=groups)


@dataclass
class AuthDecision:
    allowed: bool
    authenticated: bool
    who: str = ""


def authorize_admin(headers, settings: Settings) -> AuthDecision:
    if settings.local_dev_admin:
        if settings.running_in_app_service:
            log.error("LOCAL_DEV_ADMIN is ignored when running in App Service")
        else:
            return AuthDecision(allowed=True, authenticated=True, who="local-dev")

    # Easy Auth strips client-supplied X-MS-* headers, but only when it is enabled. Without it the
    # header could be forged, so refuse to trust it.
    if settings.running_in_app_service and not settings.easy_auth_enabled:
        log.error("Easy Auth is not enabled on this Web App; /admin is locked")
        return AuthDecision(allowed=False, authenticated=False)

    principal = parse_principal(headers.get(PRINCIPAL_HEADER))
    if principal is None:
        return AuthDecision(allowed=False, authenticated=False)

    if settings.admin_upns and any(n in settings.admin_upns for n in principal.names):
        return AuthDecision(allowed=True, authenticated=True, who=principal.display)
    if settings.admin_group_id and settings.admin_group_id in principal.groups:
        return AuthDecision(allowed=True, authenticated=True, who=principal.display)
    log.info("Admin access denied for %s", principal.display)
    return AuthDecision(allowed=False, authenticated=True, who=principal.display)
