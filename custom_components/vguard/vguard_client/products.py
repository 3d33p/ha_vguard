"""My-products API against smart20.vguard.in."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .auth import SMART_BASE, AuthError, DeviceIdentity, auth_headers


@dataclass
class Product:
    user_assets_id: int | None
    product_nick_name: str | None
    product_name: str | None
    serial_number: str | None
    device_code: str | None
    product_type: str | None  # JSON "type" — used in subscribe path / topic
    category_id: int | None
    category_name: str | None
    product_code: str | None
    key: str | None
    iv: str | None
    is_solar: bool
    is_wifi: bool
    mac_id: str | None
    raw: dict[str, Any]

    @property
    def has_crypto(self) -> bool:
        return bool(self.key and self.iv)

    @property
    def poll_interval_s(self) -> float:
        # Poll interval: 6s for inverter category (categoryId == 2), else 3s
        return 6.0 if self.category_id == 2 else 3.0


class ProductsError(RuntimeError):
    pass


def get_products(
    access_token: str,
    *,
    is_app_launch: bool = True,
    identity: DeviceIdentity | None = None,
    session: requests.Session | None = None,
) -> list[Product]:
    http = session or requests.Session()
    url = f"{SMART_BASE}v3/product/my-products?isAppLaunch={'true' if is_app_launch else 'false'}"
    resp = http.get(
        url, headers=auth_headers(access_token, identity=identity), timeout=30
    )
    try:
        payload: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise ProductsError(f"Products non-JSON ({resp.status_code}): {resp.text[:300]}") from exc
    if resp.status_code in (401, 403):
        raise AuthError(f"Products HTTP {resp.status_code}: {payload}")
    if resp.status_code >= 400:
        raise ProductsError(f"Products HTTP {resp.status_code}: {payload}")

    data = payload.get("data") or {}
    items = data.get("myProductsList") or []
    return [_parse_product(item) for item in items]


def _parse_product(item: dict[str, Any]) -> Product:
    return Product(
        user_assets_id=item.get("userAssetsId"),
        product_nick_name=item.get("productNickName"),
        product_name=item.get("productName"),
        serial_number=item.get("serialNumber"),
        device_code=str(item["deviceCode"]) if item.get("deviceCode") is not None else None,
        product_type=item.get("type"),
        category_id=item.get("categoryId"),
        category_name=item.get("categoryName"),
        product_code=item.get("productCode"),
        key=item.get("key"),
        iv=item.get("iv"),
        is_solar=_as_bool(item.get("isSolar")),
        is_wifi=_as_bool(item.get("isWifi")),
        mac_id=item.get("macId"),
        raw=item,
    )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def pick_product(
    products: list[Product],
    *,
    serial: str | None = None,
) -> Product:
    if not products:
        raise ProductsError("No products returned for this account")
    if serial:
        for p in products:
            if p.serial_number == serial:
                return p
        raise ProductsError(f"No product with serialNumber={serial!r}")

    # Prefer devices that look like inverters and have crypto keys
    def score(p: Product) -> tuple[int, int, int]:
        name = f"{p.product_name or ''} {p.category_name or ''} {p.product_type or ''}".lower()
        is_inv = 1 if any(x in name for x in ("inverter", "solar", "ups")) else 0
        return (1 if p.has_crypto else 0, is_inv, 1 if p.serial_number else 0)

    return max(products, key=score)
