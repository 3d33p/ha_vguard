"""Binary sensors for V-Guard Smart."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VGuardDataUpdateCoordinator
from .entity import VGuardEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: VGuardDataUpdateCoordinator = data["coordinator"]
    async_add_entities([OnlineBinarySensor(coordinator, entry)])


class OnlineBinarySensor(VGuardEntity, BinarySensorEntity):
    """Cloud online status from subscribe payload presence."""

    _attr_name = "Online"
    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_online"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        value = data.get("is_online")
        if value is None:
            # Successful coordinator data without the flag predates this entity.
            return True
        return bool(value)
