"""Parse decrypted Nous payloads (VG029) into human-readable dashboard fields."""

from __future__ import annotations

import json
from typing import Any

from .mappings import (
    BASE_FIELD_SPECS,
    MAPPED_VG_KEYS,
    SOLAR_FIELD_SPECS,
    appliance_mode_block_reason,
    derive_device_mac_from_wifi,
    display_battery_percent,
    duration_hhmm,
    duration_hours,
    DEFAULT_INVERTER_EFFICIENCY,
    estimate_load_watts,
    extra_backup_block_reason,
    format_inverter_firmware,
    format_mac_id,
    is_backup_on_mains,
    is_extra_backup_eligible,
    is_solar_available,
    label_battery_type,
    label_charging_mode,
    label_power_mode,
    label_power_saver,
    load_alarm_percent,
    parse_csv_ints,
    build_power_cut_trends,
    performance_backup_slider_positions,
    power_cut_display_count,
    solar_status_label,
    status_load_label,
    status_mains_label,
    turbo_charging_block_reason,
    wifi_signal_label,
    wifi_signal_quality,
)


def extract_vg029(decrypted_json: str | dict[str, Any]) -> dict[str, Any] | None:
    """Return the VG029 object from a decrypted payload string or dict."""
    if isinstance(decrypted_json, str):
        text = decrypted_json.strip()
        if not text:
            return None
        data = json.loads(text)
    else:
        data = decrypted_json

    if not isinstance(data, dict):
        return None
    if "VG029" in data:
        vg = data["VG029"]
        return vg if isinstance(vg, dict) else None
    if any(k.startswith("VG") for k in data):
        return data
    return None


def parse_vg029(
    vg029: dict[str, Any],
    *,
    is_solar: bool = False,
) -> dict[str, Any]:
    """Map VG029 wire keys to human-readable dashboard fields."""
    dashboard: dict[str, Any] = {
        "solar_current": 0.0,
        "solar_alarm": 0,
        "solar_gauge_status": 0,
    }

    for vg_key, name, transform in BASE_FIELD_SPECS:
        if vg_key not in vg029:
            continue
        dashboard[name] = transform(vg029.get(vg_key))

    if "alarm_data" in dashboard and dashboard["alarm_data"] is not None:
        alarm = int(dashboard["alarm_data"])
        dashboard["error_alarm_code"] = alarm
        dashboard["alarm_bit_mains_ok"] = (alarm & 2048) == 2048
        dashboard["alarm_bit_flag4"] = (alarm & 4) == 4

    if "charging_current" in dashboard:
        dashboard["mains_charging_current"] = dashboard["charging_current"]

    dashboard["load_alarm_percent"] = load_alarm_percent(dashboard.get("load_alarm_percent_raw"))
    buzzer = dashboard.get("buzzer_settings")
    dashboard["mains_changeover_buzzer_on"] = buzzer == 0 if buzzer is not None else None

    dashboard["inverter_firmware_version"] = format_inverter_firmware(
        dashboard.get("firmware_version")
    )
    dashboard["wifi_mac_id_formatted"] = format_mac_id(
        dashboard.get("wifi_mac_id") if isinstance(dashboard.get("wifi_mac_id"), str) else None
    )
    dashboard["device_mac_id"] = derive_device_mac_from_wifi(
        dashboard.get("wifi_mac_id") if isinstance(dashboard.get("wifi_mac_id"), str) else None
    )
    rssi = dashboard.get("wifi_rssi")
    rssi_int = rssi if isinstance(rssi, int) else None
    dashboard["wifi_signal_quality"] = wifi_signal_quality(rssi_int)
    dashboard["wifi_signal_label"] = wifi_signal_label(rssi_int)

    dashboard["power_mode_label"] = label_power_mode(dashboard.get("power_mode"))
    dashboard["charging_mode_label"] = label_charging_mode(dashboard.get("charging_mode"))
    dashboard["battery_type_label"] = label_battery_type(dashboard.get("battery_type"))
    lock = dashboard.get("battery_lock_status")
    # VG073: >=30 (and not 100) = locked; <30 = unlocked, will re-lock in (30-n) minutes;
    # 100 = remote battery-type change allowed in app UI.
    if lock is None:
        dashboard["battery_type_change_locked"] = None
        dashboard["battery_type_unlock_minutes_remaining"] = None
        dashboard["battery_type_app_change_enabled"] = None
    else:
        dashboard["battery_type_change_locked"] = lock >= 30 and lock != 100
        dashboard["battery_type_unlock_minutes_remaining"] = (30 - lock) if lock < 30 else 0
        dashboard["battery_type_app_change_enabled"] = lock == 100
        # Backward-compatible alias (units are minutes, not seconds).
        dashboard["battery_type_unlock_seconds_remaining"] = dashboard[
            "battery_type_unlock_minutes_remaining"
        ]

    counts_raw = parse_csv_ints(vg029.get("VG070"))
    display_counts: list[int] | None = None
    if counts_raw:
        display_counts = []
        for i, value in enumerate(counts_raw, start=1):
            dashboard[f"day{i}_count_raw"] = value
            shown = power_cut_display_count(value)
            dashboard[f"day{i}_count"] = shown
            display_counts.append(shown)

    durations_raw = parse_csv_ints(vg029.get("VG069"))
    if durations_raw:
        for i, value in enumerate(durations_raw, start=1):
            dashboard[f"day{i}_duration_raw"] = value
            dashboard[f"day{i}_duration_hours"] = duration_hours(value)
            dashboard[f"day{i}_duration"] = duration_hhmm(value)

    if "day1_count" in dashboard:
        dashboard["power_cut_today_count"] = dashboard["day1_count"]
    if "day1_duration" in dashboard:
        dashboard["power_cut_today_duration"] = dashboard["day1_duration"]

    dashboard.update(
        build_power_cut_trends(counts=display_counts, duration_raws=durations_raw)
    )

    capacity_for_soc = dashboard.get("battery_capacity")
    dashboard["is_solar_product"] = is_solar

    # Map solar VG keys whenever present (not only when the product flag is set),
    # so plugging panels in still surfaces current / availability.
    for vg_key, name, transform in SOLAR_FIELD_SPECS:
        if vg_key not in vg029:
            continue
        dashboard[name] = transform(vg029.get(vg_key))
    if is_solar:
        override = dashboard.get("solar_battery_capacity_override")
        if isinstance(override, int) and override > 0:
            dashboard["battery_capacity"] = override
            capacity_for_soc = override

    dashboard["is_solar_available"] = is_solar_available(dashboard.get("solar_alarm"))
    dashboard["solar_status_label"] = solar_status_label(dashboard.get("solar_alarm"))
    dashboard["power_saver_mode_label"] = label_power_saver(dashboard.get("power_saver_mode"))

    dashboard["is_on_mains"] = is_backup_on_mains(dashboard.get("backup_mode"))
    dashboard["mains_status_label"] = status_mains_label(
        backup_mode=dashboard.get("backup_mode"),
        is_mains_force_cut_enabled=dashboard.get("is_mains_force_cut_enabled"),
        solar_alarm=dashboard.get("solar_alarm"),
        is_solar=is_solar,
    )
    dashboard["load_status_label"] = status_load_label(
        backup_mode=dashboard.get("backup_mode"),
        alarm_data=dashboard.get("alarm_data"),
        is_power_on=dashboard.get("is_power_on"),
    )
    dashboard["load_watts"] = estimate_load_watts(
        dashboard.get("battery_voltage"),
        dashboard.get("load_current"),
    )
    dashboard["inverter_efficiency"] = DEFAULT_INVERTER_EFFICIENCY
    dashboard["inverter_efficiency_percent"] = round(
        DEFAULT_INVERTER_EFFICIENCY * 100.0, 1
    )
    # Turbo UI appears on only while on mains, even if VG099 remains set
    dashboard["is_turbo_charging_switch_on"] = bool(
        dashboard.get("is_turbo_charging") and dashboard.get("is_on_mains")
    )
    dashboard.update(
        performance_backup_slider_positions(
            dashboard.get("performance_backup_level"),
            dashboard.get("power_mode"),
        )
    )

    dashboard["battery_percentage"] = display_battery_percent(
        energy_raw=dashboard.get("battery_energy_raw"),
        capacity_raw=capacity_for_soc if isinstance(capacity_for_soc, int) else None,
        alarm_data=dashboard.get("alarm_data"),
        backup_mode=dashboard.get("backup_mode"),
        battery_voltage=dashboard.get("battery_voltage"),
        load_percentage=dashboard.get("load_percentage"),
        number_of_batteries=dashboard.get("number_of_batteries"),
        charging_current=dashboard.get("charging_current"),
        solar_current=dashboard.get("solar_current"),
        is_solar=is_solar,
    )
    dashboard["battery_soc_percent"] = dashboard["battery_percentage"]
    dashboard["turbo_charging_block_reason"] = turbo_charging_block_reason(
        is_on_mains=dashboard.get("is_on_mains"),
        is_mains_force_cut_enabled=dashboard.get("is_mains_force_cut_enabled"),
        is_holiday_mode_enabled=dashboard.get("is_holiday_mode_enabled"),
        battery_percentage=dashboard.get("battery_percentage"),
    )
    # Controllable for enable: no block reason. Always allow turn-off when on.
    dashboard["is_turbo_charging_enable_allowed"] = (
        dashboard["turbo_charging_block_reason"] is None
    )

    dashboard["appliance_mode_block_reason"] = appliance_mode_block_reason(
        is_holiday_mode_enabled=dashboard.get("is_holiday_mode_enabled"),
        is_on_mains=dashboard.get("is_on_mains"),
        is_power_on=dashboard.get("is_power_on"),
    )
    dashboard["is_appliance_mode_enable_allowed"] = (
        dashboard["appliance_mode_block_reason"] is None
    )
    # Soft warning only (app still allows enable after toast).
    dashboard["appliance_mode_ups_warning"] = (
        dashboard.get("power_mode") == 1
        and not dashboard.get("is_appliance_mode_enabled")
    )

    dashboard["is_extra_backup_eligible"] = is_extra_backup_eligible(
        dashboard.get("alarm_data")
    )
    dashboard["extra_backup_block_reason"] = extra_backup_block_reason(
        is_holiday_mode_enabled=dashboard.get("is_holiday_mode_enabled"),
        alarm_data=dashboard.get("alarm_data"),
    )
    dashboard["is_extra_backup_enable_allowed"] = (
        dashboard["extra_backup_block_reason"] is None
    )

    return dashboard


def unmapped_vg_keys(vg029: dict[str, Any]) -> list[str]:
    keys = [k for k in vg029 if isinstance(k, str) and k.startswith("VG")]
    return sorted(k for k in keys if k not in MAPPED_VG_KEYS)


def parse_decrypted_payload(
    decrypted: str,
    *,
    is_solar: bool = False,
) -> dict[str, Any]:
    """Full parse: decrypted JSON string → dashboard + metadata."""
    raw = json.loads(decrypted)
    vg029 = extract_vg029(raw)
    if vg029 is None:
        return {
            "ok": False,
            "error": "No VG029 object in decrypted payload",
            "raw_keys": list(raw.keys()) if isinstance(raw, dict) else None,
        }
    dashboard = parse_vg029(vg029, is_solar=is_solar)
    return {
        "ok": True,
        "dashboard": dashboard,
        "unmapped_vg_keys": unmapped_vg_keys(vg029),
        "vg029": vg029,
    }
