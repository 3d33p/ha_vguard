"""Nous device communication against nous20.vguard.in."""

from __future__ import annotations

from typing import Any

import requests

from . import auth, crypto
from .auth import APP_VERSION, DeviceIdentity, auth_headers
from .products import Product

NOUS_BASE = "https://nous20.vguard.in/"


class NousError(RuntimeError):
    pass


def _nous_headers(
    access_token: str,
    *,
    identity: DeviceIdentity | None = None,
) -> dict[str, str]:
    headers = auth_headers(access_token, identity=identity)
    headers["app-version"] = (identity.app_version if identity else APP_VERSION)
    return headers


# Battery-type unlock over Wi‑Fi (opens the rear slider for ~30 minutes).
# Not a generic "wake" — publishing this unlocks battery-type selection temporarily.
BATTERY_TYPE_UNLOCK_COMMAND = "VG105:1"
# Backward-compatible name (misleading; prefer BATTERY_TYPE_UNLOCK_COMMAND).
INVERTER_WAKE_COMMAND = BATTERY_TYPE_UNLOCK_COMMAND


def subscribe(
    access_token: str,
    product: Product,
    *,
    identity: DeviceIdentity | None = None,
    session: requests.Session | None = None,
    allow_empty: bool = True,
) -> dict[str, Any]:
    """GET device/v2/subscribe/{serialNumber}/{productType}/{deviceCode}.

    Cloud often returns HTTP 200 with JSON status=400 and message
    "No latest packets found" when nothing is cached yet.
    """
    if not product.serial_number or not product.product_type or not product.device_code:
        raise NousError(
            "Product missing serialNumber/type/deviceCode for subscribe: "
            f"serial={product.serial_number!r} type={product.product_type!r} "
            f"deviceCode={product.device_code!r}"
        )
    http = session or requests.Session()
    path = (
        f"device/v2/subscribe/{product.serial_number}/"
        f"{product.product_type}/{product.device_code}"
    )
    resp = http.get(
        f"{NOUS_BASE}{path}",
        headers=_nous_headers(access_token, identity=identity),
        timeout=30,
    )
    return _json_or_raise(resp, "subscribe", allow_empty=allow_empty)


def has_payload(subscribe_response: dict[str, Any]) -> bool:
    data = subscribe_response.get("data") or {}
    payload = data.get("payload")
    return bool(payload)

def info_status(
    access_token: str,
    serial_number: str,
    *,
    identity: DeviceIdentity | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    http = session or requests.Session()
    resp = http.get(
        f"{NOUS_BASE}device/info-status/{serial_number}",
        headers=_nous_headers(access_token, identity=identity),
        timeout=30,
    )
    return _json_or_raise(resp, "info-status")


def publish(
    access_token: str,
    product: Product,
    command: str,
    *,
    encrypt_payload: bool = False,
    identity: DeviceIdentity | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """POST nous/device/publish with topic + payload.

    Default is plaintext. The official Wi‑Fi/Nous path sets skipEncryption=true
    and sends raw ``VG…`` strings; AES is only for rare encrypted devices.
    """
    if not product.serial_number or not product.product_type or not product.device_code:
        raise NousError("Product missing fields for publish topic")
    topic = f"apps/{product.product_type}/{product.device_code}/{product.serial_number}"
    payload = command
    if encrypt_payload:
        if not product.key or not product.iv:
            raise NousError("Product missing key/iv for encrypted publish")
        payload = crypto.encrypt(product.key, product.iv, command)

    http = session or requests.Session()
    resp = http.post(
        f"{NOUS_BASE}nous/device/publish",
        json={"topic": topic, "payload": payload},
        headers=_nous_headers(access_token, identity=identity),
        timeout=30,
    )
    return _json_or_raise(resp, "publish")


def decrypt_subscribe_payload(
    product: Product,
    subscribe_response: dict[str, Any],
    *,
    force_decrypt: bool | None = None,
) -> str | None:
    """Extract data.payload; decrypt only when it looks encrypted.

    The API message may say the payload is encrypted even when the body is
    already plaintext JSON — detect JSON and skip AES in that case.
    """
    data = subscribe_response.get("data")
    if not data:
        return None
    encrypted = data.get("payload")
    if encrypted is None or encrypted == "":
        return None

    text = encrypted if isinstance(encrypted, str) else str(encrypted)
    stripped = text.strip()

    # Auto: if payload already looks like JSON / VG029, skip AES.
    looks_plain = stripped.startswith("{") or "VG029" in stripped[:64]
    should_decrypt = force_decrypt if force_decrypt is not None else not looks_plain

    if not should_decrypt:
        return stripped

    if not product.key or not product.iv:
        raise NousError("Product missing key/iv for decrypt")
    try:
        return crypto.decrypt(product.key, product.iv, text)
    except Exception as exc:
        sample = text if len(text) < 120 else text[:120] + "…"
        raise NousError(
            f"Decrypt failed: {exc} | key_len={len(product.key)} iv_len={len(product.iv)} "
            f"payload_type={type(encrypted).__name__} payload_sample={sample!r}"
        ) from exc


def _json_or_raise(
    resp: requests.Response,
    label: str,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError as exc:
        raise NousError(f"{label} non-JSON ({resp.status_code}): {resp.text[:300]}") from exc
    if resp.status_code in (401, 403):
        raise auth.AuthError(f"{label} HTTP {resp.status_code}: {payload}")
    # "No latest packets found" may come as HTTP 400 with a JSON body — treat as empty cache.
    if allow_empty and resp.status_code == 400:
        message = str(payload.get("message") or "").lower()
        if "no latest" in message or "not found" in message:
            return payload if isinstance(payload, dict) else {"status": 400, "data": {}, "message": str(payload)}
    if resp.status_code >= 400:
        raise NousError(f"{label} HTTP {resp.status_code}: {payload}")
    return payload