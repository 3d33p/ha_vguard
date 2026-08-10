"""Switches for V-Guard Smart settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from vguard_client import VGuardClient

from .const import DOMAIN
from .coordinator import VGuardDataUpdateCoordinator
from .entity import VGuardEntity


@dataclass(frozen=True, kw_only=True)
class VGuardSwitchDescription(SwitchEntityDescription):
    is_on_fn: Callable[[dict[str, Any]], bool | None]
    turn_on_fn: Callable[[VGuardClient], Any]
    turn_off_fn: Callable[[VGuardClient], Any]
    optimistic_fn: Callable[[bool], dict[str, Any]]
    available_fn: Callable[[dict[str, Any]], bool] | None = None
    extra_attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    turn_on_guard_fn: Callable[[dict[str, Any]], str | None] | None = None


def _gated_available(on_key: str, reason_key: str, data: dict[str, Any]) -> bool:
    # Keep available while ON so the user can turn the feature off.
    if data.get(on_key):
        return True
    return data.get(reason_key) is None


def _reason_attrs(
    data: dict[str, Any],
    *,
    reason_key: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = dict(extra or {})
    reason = data.get(reason_key)
    if reason:
        attrs["unavailable_reason"] = reason
    return attrs


def _turbo_available(data: dict[str, Any]) -> bool:
    return _gated_available("is_turbo_charging", "turbo_charging_block_reason", data)


def _turbo_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return _reason_attrs(
        data,
        reason_key="turbo_charging_block_reason",
        extra={
            "raw_turbo_flag": data.get("is_turbo_charging"),
            "battery_percentage": data.get("battery_percentage"),
        },
    )


def _turbo_turn_on_guard(data: dict[str, Any]) -> str | None:
    return data.get("turbo_charging_block_reason")


def _turbo_optimistic(enabled: bool) -> dict[str, Any]:
    updates: dict[str, Any] = {"is_turbo_charging": enabled}
    if enabled:
        updates["turbo_charging_block_reason"] = None
    return updates


def _appliance_available(data: dict[str, Any]) -> bool:
    return _gated_available(
        "is_appliance_mode_enabled", "appliance_mode_block_reason", data
    )


def _appliance_attrs(data: dict[str, Any]) -> dict[str, Any]:
    attrs = _reason_attrs(data, reason_key="appliance_mode_block_reason")
    if data.get("appliance_mode_ups_warning"):
        attrs["warning"] = "Not recommended in UPS mode"
    return attrs


def _appliance_turn_on_guard(data: dict[str, Any]) -> str | None:
    return data.get("appliance_mode_block_reason")


def _extra_backup_available(data: dict[str, Any]) -> bool:
    return _gated_available(
        "is_extra_backup_enabled", "extra_backup_block_reason", data
    )


def _extra_backup_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return _reason_attrs(
        data,
        reason_key="extra_backup_block_reason",
        extra={"eligible_after_low_battery": data.get("is_extra_backup_eligible")},
    )


def _extra_backup_turn_on_guard(data: dict[str, Any]) -> str | None:
    return data.get("extra_backup_block_reason")


SWITCHES: tuple[VGuardSwitchDescription, ...] = (
    VGuardSwitchDescription(
        key="power",
        translation_key="power",
        name="Power Switch",
        is_on_fn=lambda d: d.get("is_power_on"),
        turn_on_fn=lambda c: c.set_power(True),
        turn_off_fn=lambda c: c.set_power(False),
        optimistic_fn=lambda on: {"is_power_on": on},
    ),
    VGuardSwitchDescription(
        key="holiday_mode",
        translation_key="holiday_mode",
        name="Holiday Mode",
        is_on_fn=lambda d: d.get("is_holiday_mode_enabled"),
        turn_on_fn=lambda c: c.set_holiday_mode(True),
        turn_off_fn=lambda c: c.set_holiday_mode(False),
        optimistic_fn=lambda on: {"is_holiday_mode_enabled": on},
    ),
    VGuardSwitchDescription(
        key="turbo_charging",
        translation_key="turbo_charging",
        name="Turbo Charging",
        # Official app shows turbo ON only while on mains (VG099 && on_mains).
        is_on_fn=lambda d: d.get("is_turbo_charging_switch_on")
        if d.get("is_turbo_charging_switch_on") is not None
        else bool(d.get("is_turbo_charging") and d.get("is_on_mains")),
        turn_on_fn=lambda c: c.set_turbo_charging(True),
        turn_off_fn=lambda c: c.set_turbo_charging(False),
        optimistic_fn=_turbo_optimistic,
        available_fn=_turbo_available,
        extra_attrs_fn=_turbo_attrs,
        turn_on_guard_fn=_turbo_turn_on_guard,
    ),
    VGuardSwitchDescription(
        key="appliance_mode",
        translation_key="appliance_mode",
        name="Appliance Mode",
        is_on_fn=lambda d: d.get("is_appliance_mode_enabled"),
        turn_on_fn=lambda c: c.set_appliance_mode(True),
        turn_off_fn=lambda c: c.set_appliance_mode(False),
        optimistic_fn=lambda on: {"is_appliance_mode_enabled": on},
        available_fn=_appliance_available,
        extra_attrs_fn=_appliance_attrs,
        turn_on_guard_fn=_appliance_turn_on_guard,
    ),
    VGuardSwitchDescription(
        key="extra_backup",
        translation_key="extra_backup",
        name="Extra Backup",
        is_on_fn=lambda d: d.get("is_extra_backup_enabled"),
        turn_on_fn=lambda c: c.set_extra_backup(True),
        turn_off_fn=lambda c: c.set_extra_backup(False),
        optimistic_fn=lambda on: {"is_extra_backup_enabled": on},
        available_fn=_extra_backup_available,
        extra_attrs_fn=_extra_backup_attrs,
        turn_on_guard_fn=_extra_backup_turn_on_guard,
    ),
    VGuardSwitchDescription(
        key="advance_battery_low_alarm",
        translation_key="advance_battery_low_alarm",
        name="Advanced Low Battery Alarm",
        is_on_fn=lambda d: d.get(
            "is_advance_battery_low_alarm_enabled", d.get("low_battery_alarm")
        ),
        turn_on_fn=lambda c: c.set_advance_battery_low_alarm(True),
        turn_off_fn=lambda c: c.set_advance_battery_low_alarm(False),
        optimistic_fn=lambda on: {
            "is_advance_battery_low_alarm_enabled": on,
            "low_battery_alarm": on,
        },
    ),
    VGuardSwitchDescription(
        key="mains_changeover_buzzer",
        translation_key="mains_changeover_buzzer",
        name="Changeover Buzzer",
        is_on_fn=lambda d: d.get("mains_changeover_buzzer_on"),
        turn_on_fn=lambda c: c.set_mains_changeover_buzzer(True),
        turn_off_fn=lambda c: c.set_mains_changeover_buzzer(False),
        optimistic_fn=lambda on: {"mains_changeover_buzzer_on": on},
    ),
    VGuardSwitchDescription(
        key="day_time_load_usage",
        translation_key="day_time_load_usage",
        name="Daytime Load",
        is_on_fn=lambda d: d.get("is_day_time_load_usage_enabled"),
        turn_on_fn=lambda c: c.set_day_time_load_usage(True),
        turn_off_fn=lambda c: c.set_day_time_load_usage(False),
        optimistic_fn=lambda on: {"is_day_time_load_usage_enabled": on},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: VGuardDataUpdateCoordinator = data["coordinator"]
    client: VGuardClient = data["client"]
    async_add_entities(
        VGuardSwitch(coordinator, client, entry, desc) for desc in SWITCHES
    )


class VGuardSwitch(VGuardEntity, SwitchEntity):
    """Publish-backed Smart setting switch."""

    entity_description: VGuardSwitchDescription

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        client: VGuardClient,
        entry: ConfigEntry,
        description: VGuardSwitchDescription,
    ) -> None:
        super().__init__(coordinator, entry, client=client)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        fn = self.entity_description.available_fn
        if fn is None:
            return True
        return bool(fn(self.coordinator.data or {}))

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.extra_attrs_fn
        if fn is None:
            return None
        return fn(self.coordinator.data or {})

    async def async_turn_on(self, **kwargs: Any) -> None:
        guard = self.entity_description.turn_on_guard_fn
        if guard is not None:
            reason = guard(self.coordinator.data or {})
            if reason:
                raise HomeAssistantError(
                    f"Cannot enable {self.entity_description.name}: {reason}"
                )
        await self.hass.async_add_executor_job(self.entity_description.turn_on_fn, self._client)
        self.coordinator.apply_optimistic(self.entity_description.optimistic_fn(True))
        self.coordinator.async_schedule_refresh_after_write()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self.entity_description.turn_off_fn, self._client)
        self.coordinator.apply_optimistic(self.entity_description.optimistic_fn(False))
        self.coordinator.async_schedule_refresh_after_write()
