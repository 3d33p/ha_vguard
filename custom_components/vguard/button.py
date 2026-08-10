"""Button entities for V-Guard Smart."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    async_add_entities([LiveUpdatesButton(coordinator, entry)])


class LiveUpdatesButton(VGuardEntity, ButtonEntity):
    """Temporarily poll at 6s without publishing inverter commands."""

    _attr_name = "Live Updates"
    _attr_translation_key = "live_updates"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_live_updates"

    async def async_press(self) -> None:
        self.coordinator.enter_live_polling()
        await self.coordinator.async_request_refresh()
