"""Number entities for V-Guard Smart."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from vguard_client import VGuardClient

from .const import (
    ACTIVE_POLL_HOLD_S,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_STABLE_SCAN_INTERVAL,
)
from .coordinator import VGuardDataUpdateCoordinator
from .entity import VGuardEntity


def _performance_level(data: dict[str, Any]) -> int | None:
    level = data.get("performance_backup_level")
    if level in (None, 0):
        level = data.get("performance_slider")
    if isinstance(level, int) and 1 <= level <= 7:
        return level
    return None


def _backup_level(data: dict[str, Any]) -> int | None:
    backup = data.get("backup_slider")
    if isinstance(backup, int) and 1 <= backup <= 7:
        return backup
    perf = _performance_level(data)
    if perf is not None:
        return 8 - perf
    return None


def _linked_optimistic(performance: int) -> dict[str, Any]:
    return {
        "performance_backup_level": performance,
        "performance_slider": performance,
        "backup_slider": 8 - performance,
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: VGuardDataUpdateCoordinator = data["coordinator"]
    client: VGuardClient = data["client"]
    async_add_entities(
        [
            PerformanceLevelNumber(coordinator, client, entry),
            BatteryBackupNumber(coordinator, client, entry),
            LoadAlarmNumber(coordinator, client, entry),
            PollIntervalNumber(coordinator, entry),
        ]
    )


class _LinkedPerformanceBackupNumber(VGuardEntity, NumberEntity):
    """Base for the inverse-linked performance / battery-backup sliders."""

    _attr_native_min_value = 1
    _attr_native_max_value = 7
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    async def _async_set_performance(self, performance: int) -> None:
        await self.hass.async_add_executor_job(
            self._client.set_performance_backup_level, performance
        )
        self.coordinator.apply_optimistic(_linked_optimistic(performance))
        self.coordinator.async_schedule_refresh_after_write()


class PerformanceLevelNumber(_LinkedPerformanceBackupNumber):
    """Performance side of the linked pair (1 = max backup, 7 = max performance)."""

    _attr_name = "Performance Level"
    _attr_translation_key = "performance_level"

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        client: VGuardClient,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry, client=client)
        self._attr_unique_id = f"{entry.entry_id}_performance_level"

    @property
    def native_value(self) -> float | None:
        return _performance_level(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        backup = _backup_level(self.coordinator.data or {})
        attrs: dict[str, Any] = {
            "note": "Linked with Battery Backup Level (sum to 8)",
        }
        if backup is not None:
            attrs["battery_backup"] = backup
        return attrs

    async def async_set_native_value(self, value: float) -> None:
        await self._async_set_performance(int(value))


class BatteryBackupNumber(_LinkedPerformanceBackupNumber):
    """Battery-backup side of the linked pair (moves inversely with Performance)."""

    _attr_name = "Battery Backup Level"
    _attr_translation_key = "battery_backup"

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        client: VGuardClient,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry, client=client)
        self._attr_unique_id = f"{entry.entry_id}_battery_backup"

    @property
    def native_value(self) -> float | None:
        return _backup_level(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        perf = _performance_level(self.coordinator.data or {})
        attrs: dict[str, Any] = {
            "note": "Linked with Performance Level (sum to 8)",
        }
        if perf is not None:
            attrs["performance_level"] = perf
        return attrs

    async def async_set_native_value(self, value: float) -> None:
        backup = int(value)
        await self._async_set_performance(8 - backup)


class LoadAlarmNumber(VGuardEntity, NumberEntity):
    """Overload alarm threshold percent."""

    _attr_name = "Overload Alarm Threshold"
    _attr_translation_key = "load_alarm"
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 10
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        client: VGuardClient,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry, client=client)
        self._attr_unique_id = f"{entry.entry_id}_load_alarm"

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("load_alarm_percent")

    async def async_set_native_value(self, value: float) -> None:
        percent = int(value)
        await self.hass.async_add_executor_job(
            self._client.set_load_alarm_percent, percent
        )
        self.coordinator.apply_optimistic({"load_alarm_percent": percent})
        self.coordinator.async_schedule_refresh_after_write()


class PollIntervalNumber(VGuardEntity, NumberEntity):
    """Cloud poll interval; values under 30s are temporary (hold window)."""

    _attr_name = "Poll Interval"
    _attr_translation_key = "poll_interval"
    _attr_native_min_value = MIN_SCAN_INTERVAL
    _attr_native_max_value = MAX_SCAN_INTERVAL
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_poll_interval"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _interval_changed() -> None:
            self.async_write_ha_state()

        self.coordinator.async_add_interval_listener(_interval_changed)

    @property
    def native_value(self) -> float | None:
        return float(self.coordinator.poll_interval_seconds)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "stable_interval": self.coordinator.last_stable_rate,
            "live_polling": self.coordinator.is_live_polling,
            "note": (
                f"Values under {MIN_STABLE_SCAN_INTERVAL}s last "
                f"{ACTIVE_POLL_HOLD_S}s, then revert to the stable interval. "
                "Use Live Updates for 6s bursts without changing this."
            ),
        }

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_poll_interval(value)

