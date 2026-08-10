"""Sensors for V-Guard Smart."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VGuardDataUpdateCoordinator
from .entity import VGuardEntity

@dataclass(frozen=True, kw_only=True)
class VGuardSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


# Primary → Sensors card. Diagnostic → Diagnostic card.
# Icons live in icons.json via translation_key (battery % uses HA device-class icons).
SENSORS: tuple[VGuardSensorDescription, ...] = (
    # --- Essential (Sensors) ---
    VGuardSensorDescription(
        key="battery_percentage",
        name="Battery Level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("battery_percentage"),
    ),
    VGuardSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        name="Battery Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("battery_voltage"),
    ),
    VGuardSensorDescription(
        key="mains_voltage",
        translation_key="mains_voltage",
        name="Mains Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("mains_voltage"),
    ),
    VGuardSensorDescription(
        key="output_voltage",
        translation_key="output_voltage",
        name="Output Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("output_voltage"),
    ),
    VGuardSensorDescription(
        key="load_percentage",
        translation_key="load_percentage",
        name="Load Percentage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("load_percentage"),
    ),
    VGuardSensorDescription(
        key="load_current",
        translation_key="load_current",
        name="Load Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("load_current"),
    ),
    VGuardSensorDescription(
        key="load_watts",
        translation_key="load_watts",
        name="Load Power (Approximate)",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("load_watts"),
    ),
    VGuardSensorDescription(
        key="charging_current",
        translation_key="charging_current",
        name="Charging Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("charging_current"),
    ),
    VGuardSensorDescription(
        key="mains_status_label",
        translation_key="mains_status_label",
        name="Mains Status",
        value_fn=lambda d: d.get("mains_status_label"),
    ),
    VGuardSensorDescription(
        key="load_status_label",
        translation_key="load_status_label",
        name="Load Status",
        value_fn=lambda d: d.get("load_status_label"),
    ),
    # --- Diagnostic ---
    VGuardSensorDescription(
        key="power_mode_label",
        translation_key="power_mode_label",
        name="Inverter Mode",
        device_class=SensorDeviceClass.ENUM,
        options=["Normal", "UPS", "Equipment"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("power_mode_label"),
    ),
    VGuardSensorDescription(
        key="charging_mode_label",
        translation_key="charging_mode_label",
        name="Charging Mode",
        device_class=SensorDeviceClass.ENUM,
        options=["Normal", "High", "Low"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("charging_mode_label"),
    ),
    VGuardSensorDescription(
        key="power_saver_mode_label",
        translation_key="power_saver_mode_label",
        name="Power Saver Mode",
        device_class=SensorDeviceClass.ENUM,
        options=["Normal", "Max"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("power_saver_mode_label"),
    ),
    VGuardSensorDescription(
        key="battery_type_label",
        translation_key="battery_type_label",
        name="Battery Type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("battery_type_label"),
    ),
    VGuardSensorDescription(
        key="inverter_efficiency",
        translation_key="inverter_efficiency",
        name="Inverter Efficiency (Approximate)",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("inverter_efficiency_percent"),
    ),
    VGuardSensorDescription(
        key="solar_status_label",
        translation_key="solar_status_label",
        name="Solar Status",
        device_class=SensorDeviceClass.ENUM,
        options=["Solar Available", "Solar Unavailable"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("solar_status_label"),
    ),
    VGuardSensorDescription(
        key="solar_current",
        translation_key="solar_current",
        name="Solar Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("solar_current"),
    ),
    VGuardSensorDescription(
        key="inverter_firmware_version",
        translation_key="inverter_firmware_version",
        name="Inverter Firmware Version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("inverter_firmware_version"),
    ),
    VGuardSensorDescription(
        key="wifi_firmware_version",
        translation_key="wifi_firmware_version",
        name="Wi-Fi Firmware Version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("wifi_firmware_version"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: VGuardDataUpdateCoordinator = data["coordinator"]
    client = data["client"]
    entities: list[SensorEntity] = [
        VGuardSensor(coordinator, entry, desc, client=client) for desc in SENSORS
    ]
    entities.append(WifiSignalStrengthSensor(coordinator, entry, client=client))
    entities.append(PowerCutTrendsSensor(coordinator, entry, client=client))
    async_add_entities(entities)


class VGuardSensor(VGuardEntity, SensorEntity):
    """Mapped dashboard field as a sensor."""

    entity_description: VGuardSensorDescription

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        entry: ConfigEntry,
        description: VGuardSensorDescription,
        *,
        client=None,
    ) -> None:
        super().__init__(coordinator, entry, client=client)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data or {})


class WifiSignalStrengthSensor(VGuardEntity, SensorEntity):
    """Wi-Fi RSSI with quality band, e.g. `-55 dBm (Good)`."""

    _attr_name = "Wi-Fi Signal Strength"
    _attr_translation_key = "wifi_signal_strength"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        entry: ConfigEntry,
        *,
        client=None,
    ) -> None:
        super().__init__(coordinator, entry, client=client)
        # Keep prior unique_id so existing entity_id stays stable.
        self._attr_unique_id = f"{entry.entry_id}_wifi_rssi"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        label = data.get("wifi_signal_label")
        if label is not None:
            return label
        rssi = data.get("wifi_rssi")
        quality = data.get("wifi_signal_quality")
        if rssi is None:
            return None
        if quality:
            return f"{rssi} dBm ({quality})"
        return f"{rssi} dBm"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {}
        if data.get("wifi_rssi") is not None:
            attrs["rssi"] = data["wifi_rssi"]
        if data.get("wifi_signal_quality") is not None:
            attrs["quality"] = data["wifi_signal_quality"]
        return attrs


class PowerCutTrendsSensor(VGuardEntity, SensorEntity):
    """Seven-day power-cut count/duration (app Usage chart).

    Device page shows a one-line summary; open the entity or use the Lovelace
    markdown card for the full Day / Cuts / Duration table.
    """

    _attr_name = "Power Cut Trends (Today)"
    _attr_translation_key = "power_cut_trends"

    def __init__(
        self,
        coordinator: VGuardDataUpdateCoordinator,
        entry: ConfigEntry,
        *,
        client=None,
    ) -> None:
        super().__init__(coordinator, entry, client=client)
        self._attr_unique_id = f"{entry.entry_id}_power_cut_trends"

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data or {}).get("power_cut_trends_summary")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {
            "cuts_today": data.get("power_cut_today_count"),
            "duration_today": data.get("power_cut_today_duration"),
            "markdown": data.get("power_cut_trends_markdown"),
            "table": data.get("power_cut_trends_table"),
            "days": data.get("power_cut_trends"),
        }
        for row in data.get("power_cut_trends") or []:
            idx = row.get("index")
            if idx is None:
                continue
            attrs[f"day{idx}_label"] = row.get("label")
            attrs[f"day{idx}_date"] = row.get("date")
            attrs[f"day{idx}_count"] = row.get("count")
            attrs[f"day{idx}_duration"] = row.get("duration")
            attrs[f"day{idx}_duration_minutes"] = row.get("duration_minutes")
        return attrs
