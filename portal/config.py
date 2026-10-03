"""Configuration loaded entirely from environment variables (App Service app settings)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Mapping, Optional

TRUTHY = {"1", "true", "yes", "on"}


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key) or default).strip()


def _csv(value: str) -> List[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    managed_identity_client_id: str = ""
    group_id: str = ""
    access_code: str = field(default="", repr=False)
    invite_redirect_url: str = ""
    invite_message: str = ""
    turnstile_site_key: str = ""
    turnstile_secret_key: str = field(default="", repr=False)
    storage_connection_string: str = field(default="", repr=False)
    table_name: str = "submissions"
    admin_upns: List[str] = field(default_factory=list)
    admin_group_id: str = ""
    daily_invite_cap: int = 100
    register_rate_limit: str = "5 per 10 minutes"
    local_dev_admin: bool = False
    # App Service sets WEBSITE_AUTH_ENABLED=True when Easy Auth is on and WEBSITE_SITE_NAME always.
    easy_auth_enabled: bool = False
    running_in_app_service: bool = False

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        tenant_id = _get(env, "TENANT_ID")
        redirect = _get(env, "INVITE_REDIRECT_URL") or (
            f"https://portal.azure.com/{tenant_id}" if tenant_id else "https://portal.azure.com/"
        )
        try:
            cap = int(_get(env, "DAILY_INVITE_CAP", "100"))
        except ValueError:
            cap = 100
        return cls(
            tenant_id=tenant_id,
            client_id=_get(env, "CLIENT_ID"),
            client_secret=_get(env, "CLIENT_SECRET"),
            managed_identity_client_id=_get(env, "MANAGED_IDENTITY_CLIENT_ID"),
            group_id=_get(env, "GROUP_ID"),
            access_code=_get(env, "ACCESS_CODE"),
            invite_redirect_url=redirect,
            invite_message=_get(env, "INVITE_MESSAGE"),
            turnstile_site_key=_get(env, "TURNSTILE_SITE_KEY"),
            turnstile_secret_key=_get(env, "TURNSTILE_SECRET_KEY"),
            storage_connection_string=_get(env, "STORAGE_CONNECTION_STRING"),
            table_name=_get(env, "TABLE_NAME", "submissions"),
            admin_upns=_csv(_get(env, "ADMIN_UPNS")),
            admin_group_id=_get(env, "ADMIN_GROUP_ID").lower(),
            daily_invite_cap=max(cap, 0),
            register_rate_limit=_get(env, "REGISTER_RATE_LIMIT", "5 per 10 minutes"),
            local_dev_admin=_get(env, "LOCAL_DEV_ADMIN").lower() in TRUTHY,
            easy_auth_enabled=_get(env, "WEBSITE_AUTH_ENABLED").lower() in TRUTHY,
            running_in_app_service=bool(_get(env, "WEBSITE_SITE_NAME")),
        )

    def missing_required(self) -> List[str]:
        """Names of settings needed to process registrations that are not set."""
        required = {
            "TENANT_ID": self.tenant_id,
            "GROUP_ID": self.group_id,
            "ACCESS_CODE": self.access_code,
            "TURNSTILE_SECRET_KEY": self.turnstile_secret_key,
        }
        if self.client_secret:
            required["CLIENT_ID"] = self.client_id
        return [name for name, value in required.items() if not value]
