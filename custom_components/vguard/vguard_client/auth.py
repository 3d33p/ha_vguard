"""Login and session refresh against smart20.vguard.in."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

import requests

SMART_BASE = "https://smart20.vguard.in/"
APP_VERSION = "2.0.17"

# Match official Android app headers (platform=1 = Android).
DEFAULT_PLATFORM = "1"
DEFAULT_PLATFORM_VERSION = "34"  # Android 14 SDK
DEFAULT_PLATFORM_MODEL = "Google-Pixel 8"


@dataclass
class Session:
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class DeviceIdentity:
    """HTTP / login identity presented to the cloud (phone-like)."""

    fcm_token: str
    platform: str = DEFAULT_PLATFORM
    platform_version: str = DEFAULT_PLATFORM_VERSION
    platform_model: str = DEFAULT_PLATFORM_MODEL
    app_version: str = APP_VERSION


def generate_fcm_token() -> str:
    """Stable-looking Android FCM registration token lookalike.

    Not a real Google token — only used so login payloads resemble the app.
    Persist this string so the same “device” is reused across restarts.
    """
    prefix = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:11]
    body = secrets.token_urlsafe(160).replace("-", "").replace("_", "")
    return f"{prefix}:APA91b{body}"


def _base_headers(identity: DeviceIdentity | None = None) -> dict[str, str]:
    ident = identity or DeviceIdentity(fcm_token="")
    return {
        "content-type": "application/json",
        "app-version": ident.app_version,
        "platform": ident.platform,
        "platform-version": ident.platform_version,
        "platform-model": ident.platform_model,
        "accept": "application/json",
    }


def auth_headers(
    access_token: str,
    *,
    identity: DeviceIdentity | None = None,
) -> dict[str, str]:
    headers = _base_headers(identity)
    headers["authorization"] = access_token
    return headers


class AuthError(RuntimeError):
    pass


def login(
    email: str,
    password: str,
    *,
    identity: DeviceIdentity | None = None,
    fcm_token: str | None = None,
    session: requests.Session | None = None,
) -> Session:
    """Email/password login (loginType=2)."""
    http = session or requests.Session()
    if identity is None:
        identity = DeviceIdentity(fcm_token=fcm_token or generate_fcm_token())
    body = {
        "email": email,
        "loginType": 2,
        "countryCode": None,
        "mobile": None,
        "otp": None,
        "fcmToken": identity.fcm_token,
        "password": password,
    }
    resp = http.post(
        f"{SMART_BASE}v1/user/login",
        json=body,
        headers=_base_headers(identity),
        timeout=30,
    )
    return _parse_login_response(resp)


def refresh_token(
    refresh: str,
    *,
    identity: DeviceIdentity | None = None,
    session: requests.Session | None = None,
) -> str:
    """PUT v1/user/access-token with {\"token\": refreshToken} → new accessToken."""
    http = session or requests.Session()
    resp = http.put(
        f"{SMART_BASE}v1/user/access-token",
        json={"token": refresh},
        headers=_base_headers(identity),
        timeout=30,
    )
    try:
        payload: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise AuthError(f"Refresh non-JSON ({resp.status_code}): {resp.text[:300]}") from exc
    if resp.status_code >= 400:
        raise AuthError(f"Refresh HTTP {resp.status_code}: {payload}")
    data = payload.get("data") or {}
    access = data.get("accessToken")
    if not access:
        raise AuthError(f"Refresh missing accessToken: {payload}")
    return access


def _parse_login_response(resp: requests.Response) -> Session:
    try:
        payload: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise AuthError(f"Login non-JSON ({resp.status_code}): {resp.text[:300]}") from exc

    if resp.status_code >= 400:
        raise AuthError(f"Login HTTP {resp.status_code}: {payload}")

    status = payload.get("status")
    data = payload.get("data") or {}
    error = data.get("error")
    if error:
        raise AuthError(f"Login error: {error}")
    access = data.get("accessToken")
    refresh = data.get("refreshToken")
    if not access or not refresh:
        raise AuthError(
            f"Login missing tokens (status={status}, message={payload.get('message')}): {payload}"
        )
    return Session(access_token=access, refresh_token=refresh)
