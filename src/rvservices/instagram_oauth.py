"""
OAuth helpers for Instagram API with Instagram Login.
"""
from __future__ import annotations

import logging
from typing import Any, Dict
from urllib.parse import urlencode

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def build_authorize_url(*, redirect_uri: str, state: str, scope: str | None = None) -> str:
    client_id = settings.INSTAGRAM_KEY
    scopes = scope or getattr(
        settings,
        "INSTAGRAM_OAUTH_SCOPES",
        "instagram_business_basic",
    )
    q = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "state": state,
        }
    )
    return f"https://www.instagram.com/oauth/authorize?{q}"


def exchange_code_for_short_lived_token(
    *, code: str, redirect_uri: str
) -> Dict[str, Any]:
    """
    POST https://api.instagram.com/oauth/access_token
    Returns first element of top-level list in JSON (access_token, user_id, permissions).
    """
    resp = requests.post(
        "https://api.instagram.com/oauth/access_token",
        data={
            "client_id": settings.INSTAGRAM_KEY,
            "client_secret": settings.INSTAGRAM_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=60,
    )
    data = resp.json()
    if not resp.ok:
        err = data.get("error_message", data)
        raise RuntimeError(f"Instagram OAuth token exchange failed: {err}")
    if isinstance(data, dict) and "access_token" in data:
        return data
    if isinstance(data, dict) and "data" in data:
        return data["data"][0]
    if isinstance(data, list) and data:
        return data[0]
    raise RuntimeError(f"Unexpected Instagram OAuth response shape: {data!r}")
