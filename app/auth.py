"""Small static-token authentication seam; configure secrets outside the repository."""

import hmac
import json
import os
from typing import Literal

from fastapi import Header, HTTPException
from pydantic import TypeAdapter

from app.models import Model


class Principal(Model):
    owner_id: str
    actor: str
    role: Literal["user", "reviewer"] = "user"


def auth_dependency():
    raw = os.getenv("API_TOKENS_JSON")
    configured = (
        TypeAdapter(dict[str, Principal]).validate_python(json.loads(raw))
        if raw
        else {}
    )
    if raw and (not configured or any(len(token) < 24 for token in configured)):
        raise ValueError("API tokens must be at least 24 characters")

    async def authenticate(
        authorization: str | None = Header(default=None),
    ) -> Principal:
        if not configured:
            return Principal(owner_id="local", actor="local", role="user")
        candidate = (
            authorization.removeprefix("Bearer ")
            if authorization and authorization.startswith("Bearer ")
            else ""
        )
        for token, principal in configured.items():
            if hmac.compare_digest(candidate.encode(), token.encode()):
                return principal
        raise HTTPException(
            401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}
        )

    return authenticate
