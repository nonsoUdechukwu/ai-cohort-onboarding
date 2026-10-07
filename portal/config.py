"""Configuration loaded entirely from environment variables (App Service app settings)."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing_extensions import Annotated

TRUTHY = {"1", "true", "yes", "on"}
DEFAULT_TABLE_NAME = "submissions"
DEFAULT_DAILY_CAP = 100
DEFAULT_RATE_LIMIT = "5 per 10 minutes"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in TRUTHY


class Settings(BaseSettings):
    """App settings. Field ``foo_bar`` is read from env var ``FOO_BAR`` unless an alias says otherwise."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = Field(default="", repr=False)
    managed_identity_client_id: str = ""
    group_id: str = ""
    access_code: str = Field(default="", repr=False)
    invite_redirect_url: str = ""
    invite_message: str = ""
    turnstile_site_key: str = ""
    turnstile_secret_key: str = Field(default="", repr=False)
    turnstile_hostnames: Annotated[List[str], NoDecode] = Field(default_factory=list)
    storage_connection_string: str = Field(default="", repr=False)
    table_name: str = DEFAULT_TABLE_NAME
    admin_upns: Annotated[List[str], NoDecode] = Field(default_factory=list)
    admin_group_id: str = ""
    daily_invite_cap: int = DEFAULT_DAILY_CAP
    register_rate_limit: str = DEFAULT_RATE_LIMIT
    local_dev_admin: bool = False
    # App Service sets WEBSITE_AUTH_ENABLED=True when Easy Auth is on and WEBSITE_SITE_NAME always.
    easy_auth_enabled: bool = Field(
        default=False, validation_alias=AliasChoices("easy_auth_enabled", "WEBSITE_AUTH_ENABLED")
    )
    running_in_app_service: bool = Field(
        default=False, validation_alias=AliasChoices("running_in_app_service", "WEBSITE_SITE_NAME")
    )

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: Any) -> Any:
        if isinstance(data, dict) and not str(data.get("invite_redirect_url") or "").strip():
            tenant_id = str(data.get("tenant_id") or "").strip()
            data = dict(data)
            data["invite_redirect_url"] = (
                f"https://portal.azure.com/{tenant_id}" if tenant_id else "https://portal.azure.com/"
            )
        return data

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("table_name", "register_rate_limit", mode="before")
    @classmethod
    def _non_empty(cls, value: Any, info) -> Any:
        if not value:
            return DEFAULT_TABLE_NAME if info.field_name == "table_name" else DEFAULT_RATE_LIMIT
        return value

    @field_validator("admin_upns", "turnstile_hostnames", mode="before")
    @classmethod
    def _csv(cls, value: Any) -> List[str]:
        items = value.split(",") if isinstance(value, str) else list(value or [])
        return [str(item).strip().lower() for item in items if str(item).strip()]

    @field_validator("admin_group_id", mode="before")
    @classmethod
    def _lower(cls, value: Any) -> str:
        return str(value or "").strip().lower()

    @field_validator("daily_invite_cap", mode="before")
    @classmethod
    def _cap(cls, value: Any) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return DEFAULT_DAILY_CAP

    @field_validator("local_dev_admin", "easy_auth_enabled", mode="before")
    @classmethod
    def _bool(cls, value: Any) -> bool:
        return _truthy(value)

    @field_validator("running_in_app_service", mode="before")
    @classmethod
    def _site_name(cls, value: Any) -> bool:
        # WEBSITE_SITE_NAME holds the site name; any non-empty value means "on App Service".
        return value if isinstance(value, bool) else bool(str(value or "").strip())

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        """Load from ``os.environ`` (default) or from an explicit mapping (tests)."""
        if env is None:
            return cls()
        lowered = {k.lower(): v for k, v in env.items()}
        data: Dict[str, Any] = {}
        for name, field in cls.model_fields.items():
            keys = [name]
            alias = field.validation_alias
            if isinstance(alias, AliasChoices):
                keys = [str(c) for c in alias.choices if str(c) != name]
            value = next((lowered[k.lower()] for k in keys if lowered.get(k.lower(), "").strip()), None)
            if value is not None:
                data[name] = value
        # model_validate skips the env/dotenv sources, so only ``env`` is used.
        return cls.model_validate(data)

    @property
    def turnstile_enabled(self) -> bool:
        return bool(self.turnstile_site_key and self.turnstile_secret_key)

    def missing_required(self) -> List[str]:
        """Names of settings needed to process registrations that are not set."""
        required = {
            "TENANT_ID": self.tenant_id,
            "GROUP_ID": self.group_id,
            "ACCESS_CODE": self.access_code,
        }
        # Turnstile is optional, but only one of its two keys being set is a misconfiguration.
        if self.turnstile_site_key or self.turnstile_secret_key:
            required["TURNSTILE_SITE_KEY"] = self.turnstile_site_key
            required["TURNSTILE_SECRET_KEY"] = self.turnstile_secret_key
        if self.client_secret:
            required["CLIENT_ID"] = self.client_id
        return [name for name, value in required.items() if not value]
