"""Admin-token gate for writes to the knowledge base.

Reading the knowledge base is open to anyone who can reach the app; uploading
and deleting consultant material is not. Deploy behind SSO if you need per-user
identity - this is a shared-secret gate, not an identity system.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from .config import get_settings

DEFAULT_TOKEN = "change-me-before-deploying"


def require_admin(x_admin_token: str = Header(default="")) -> None:
    expected = get_settings().admin_token
    if not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(
            status_code=401,
            detail="Admin token required. Set ADMIN_TOKEN in .env and enter it in the app.",
        )


def admin_token_is_default() -> bool:
    return get_settings().admin_token == DEFAULT_TOKEN
