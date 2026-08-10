"""Shared entity helpers for V-Guard Smart."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from vguard_client import VGuardClient

from .const import CONF_SERIAL, DOMAIN
from .coordinator import VGuardDataUpdateCoordinator


def build_device_info(
    entry: ConfigEntry,
    coordinator: VGuardDataUpdateCoordinator,
    client: VGuardClient | None = None,
) -> DeviceInfo:
    """Device info for the Sensors / Controls / Diagnostic device page."""
    data: dict[str, Any] = coordinator.data or {}
    product = None
    if client is not None:
        try:
            product = client.product
        except Exception:  # noqa: BLE001
            product = None

    model = None
    serial = entry.data.get(CONF_SERIAL) or None
    if product is not None:
        model = product.product_name or product.category_name
        serial = product.serial_number or serial

    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="V-Guard",
        model=model,
        serial_number=serial,
        sw_version=data.get("inverter_firmware_version"),
    )


class VGuardEntity(CoordinatorEntity[VGuardDataUpdateCoordinator]):
    """Base entity attached to the V-Guard device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        entry: ConfigEntry,
        *,
        client: VGuardClient | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = client

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self._entry, self.coordinator, self._client)
