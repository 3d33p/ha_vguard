"""Select entities for V-Guard Smart."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from vguard_client import VGuardClient
from vguard_client.commands import FORCE_CUT_PRESETS_MINUTES

from .const import DOMAIN
from .coordinator import VGuardDataUpdateCoordinator
from .entity import VGuardEntity

# App list ends with “No forced power cut” (VG037:-1 then VG038:0).
_OPTION_OFF = "Off"
_FORCE_CUT_DURATION_OPTIONS: dict[str, int] = {
    f"{m} Min": m for m in sorted(FORCE_CUT_PRESETS_MINUTES)
}
_MINUTES_TO_OPTION = {m: label for label, m in _FORCE_CUT_DURATION_OPTIONS.items()}
_ALL_OPTIONS = [_OPTION_OFF, *_FORCE_CUT_DURATION_OPTIONS]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ForcePowerCutDurationSelect(
                data["coordinator"], data["client"], entry
            )
        ]
    )


class ForcePowerCutDurationSelect(VGuardEntity, SelectEntity):
    """Force power cut duration presets, including Off to clear."""

    _attr_name = "Forced Power Cut"
    _attr_translation_key = "forced_power_cut"
    _attr_options = list(_ALL_OPTIONS)

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        client: VGuardClient,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry, client=client)
        self._attr_unique_id = f"{entry.entry_id}_force_power_cut_duration"

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        if not data.get("is_mains_force_cut_enabled"):
            return _OPTION_OFF
        minutes = data.get("main_force_cut_time")
        if isinstance(minutes, int):
            return _MINUTES_TO_OPTION.get(minutes, _OPTION_OFF)
        return _OPTION_OFF

    async def async_select_option(self, option: str) -> None:
        if option == _OPTION_OFF:

            def _clear() -> None:
                self._client.set_force_power_cut(False)

            await self.hass.async_add_executor_job(_clear)
            self.coordinator.apply_optimistic(
                {
                    "is_mains_force_cut_enabled": False,
                    "main_force_cut_time": -1,
                }
            )
        else:
            minutes = _FORCE_CUT_DURATION_OPTIONS[option]

            def _enable() -> None:
                self._client.set_force_power_cut(True, minutes=minutes)

            await self.hass.async_add_executor_job(_enable)
            self.coordinator.apply_optimistic(
                {
                    "is_mains_force_cut_enabled": True,
                    "main_force_cut_time": minutes,
                }
            )
        self.coordinator.async_schedule_refresh_after_write()
