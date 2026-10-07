"""Pydantic models for the registration API contract used by static/app.js."""
from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """Form (multipart/urlencoded) or JSON body of ``POST /api/register``."""

    email: str = ""
    name: str = ""
    access_code: str = Field(default="", repr=False)
    token: str = Field(default="", repr=False)

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> "RegisterRequest":
        def get(key: str) -> str:
            value = data.get(key, "")
            return value.strip() if isinstance(value, str) else ""

        return cls(
            email=get("email"),
            name=get("name"),
            access_code=get("access_code"),
            token=get("cf-turnstile-response") or get("turnstile_token"),
        )


class RegisterSuccess(BaseModel):
    ok: Literal[True] = True
    email: str
    outcome: Literal["invited", "already_member"]
    redeemUrl: str = ""
    message: str


class RegisterError(BaseModel):
    ok: Literal[False] = False
    error: str

