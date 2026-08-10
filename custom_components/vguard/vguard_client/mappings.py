"""VG029 wire fields → human-readable dashboard values.

Mappings were built by observing live cloud payloads and correlating them with
on-device / companion-app UI labels (not by copying proprietary source).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

# App battery gauge caps SOC above this at 97 until the "charged" state forces 100.
# Firmware also tends to clear turbo near full; HA uses this as a preemptive gate.
TURBO_NEAR_FULL_BATTERY_PERCENT = 97

# Assumed DC→AC conversion efficiency for estimated load watts
# (battery_voltage × load_current × efficiency). Not exposed by the device.
DEFAULT_INVERTER_EFFICIENCY = 0.90


def estimate_load_watts(
    battery_voltage: float | None,
    load_current: float | None,
    *,
    efficiency: float = DEFAULT_INVERTER_EFFICIENCY,
) -> float | None:
    """Estimate AC load power from DC voltage/current and inverter efficiency."""
    if battery_voltage is None or load_current is None:
        return None
    try:
        volts = float(battery_voltage)
        amps = float(load_current)
    except (TypeError, ValueError):
        return None
    if volts < 0 or amps < 0 or efficiency <= 0:
        return None
    return round(volts * amps * float(efficiency), 1)


def _safe_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _eq_int(value: Any, expected: int) -> bool:
    parsed = _safe_int(value)
    return parsed is not None and parsed == expected


def duration_hours(raw: int) -> float:
    """Power-cut duration hours from device raw tick counter (capped at 24)."""
    hours = round((raw * 2.0) / 3600.0, 2)
    return min(hours, 24.0)


def duration_hhmm(raw: int) -> str:
    """Format power-cut duration as H:MM."""
    hours = duration_hours(raw)
    total_minutes = int(round(hours * 60))
    h, m = divmod(total_minutes, 60)
    return f"{h}:{m:02d}"


def parse_csv_ints(value: Any, expected: int = 7) -> list[int] | None:
    if value is None or value == "":
        return None
    parts = [p.strip() for p in str(value).split(",")]
    if len(parts) < expected:
        return None
    out: list[int] = []
    for part in parts[:expected]:
        n = _safe_int(part)
        if n is None:
            return None
        out.append(n)
    return out


def battery_soc_percent(remaining_raw: int | None, capacity_raw: int | None) -> int | None:
    """Energy-ratio SOC: remaining/capacity as percent, clamped 0..100."""
    if remaining_raw is None or capacity_raw is None or capacity_raw <= 0:
        return None
    pct = int((remaining_raw * 100.0) / capacity_raw)
    return max(0, min(100, pct))


def is_backup_on_mains(backup_mode: int | None) -> bool:
    """True when status bitfield indicates load served from mains."""
    return backup_mode is not None and (backup_mode & 8) == 8


def is_alarm_mains_ok(alarm_data: int | None) -> bool:
    """True when alarm bitfield indicates mains present / healthy."""
    return alarm_data is not None and (alarm_data & 2048) == 2048


def is_extra_backup_eligible(alarm_data: int | None) -> bool:
    """True when alarm bit 2 is set — eligible after low battery."""
    return alarm_data is not None and (int(alarm_data) & 4) == 4


def is_solar_available(solar_alarm: int | None) -> bool:
    """True when solar_alarm bit 5 is set → “Solar Available”."""
    return solar_alarm is not None and (int(solar_alarm) & 32) == 32


def solar_status_label(solar_alarm: int | None) -> str:
    """Dashboard solar tile text from the official app."""
    return "Solar Available" if is_solar_available(solar_alarm) else "Solar Unavailable"


def display_battery_percent(
    *,
    energy_raw: int | None,
    capacity_raw: int | None,
    alarm_data: int | None,
    backup_mode: int | None,
    battery_voltage: float | None,
    load_percentage: int | None,
    number_of_batteries: int | None,
    charging_current: float | None,
    solar_current: float | None,
    is_solar: bool,
) -> int | None:
    """Battery gauge percent shown on the status screen.

    Observed behaviour: values above 97 are capped unless the unit is in a
    "charged" state (then 100). A special alarm code uses a voltage formula.
    """
    alarm = int(alarm_data) if alarm_data is not None else 0
    on_mains = is_backup_on_mains(backup_mode)
    mains_ok = is_alarm_mains_ok(alarm_data)
    n_batt = number_of_batteries if number_of_batteries and number_of_batteries > 0 else 1
    load_pct = float(load_percentage or 0)
    batt_v = float(battery_voltage or 0.0)
    _ = (charging_current, solar_current)  # reserved for charge-state labels

    if alarm == 8192:
        if on_mains:
            full_v = (14.4 * n_batt) - (load_pct * 0.007)
            empty_offset = batt_v - (10.5 * n_batt)
            span = full_v - (n_batt * 12.0)
            pct = int((empty_offset / span) * 100.0) if span else 0
            if not mains_ok and pct > 97:
                pct = 97
        else:
            full_v = (14.2 * n_batt) - (load_pct * 0.007)
            empty_offset = batt_v - (10.4 * n_batt)
            span = full_v - (n_batt * 12.0)
            pct = int((empty_offset / span) * 100.0) if span else 0
            if not mains_ok:
                pct = 97
    else:
        pct = 100 if mains_ok else (battery_soc_percent(energy_raw, capacity_raw) or 0)
        if pct > 97 and mains_ok:
            pct = 97

    pct = max(0, min(100, pct))
    if pct > 97:
        pct = 97

    charged = False
    if alarm == 8192:
        if on_mains and mains_ok:
            charged = True
    elif not on_mains:
        if is_solar and mains_ok:
            charged = True
    elif mains_ok:
        charged = True

    if charged:
        return 100
    return pct


def turbo_charging_block_reason(
    *,
    is_on_mains: bool | None,
    is_mains_force_cut_enabled: bool | None,
    is_holiday_mode_enabled: bool | None,
    battery_percentage: int | None,
    near_full_percent: int = TURBO_NEAR_FULL_BATTERY_PERCENT,
) -> str | None:
    """Why turbo should not be enabled (app gates + near-full heuristic).

    Official app blocks enable when holiday mode is on, or mains is unavailable /
    force-cut. There is no app-side battery gate; near-full is a client heuristic
    matching observed firmware auto-clear of VG099.
    """
    if is_holiday_mode_enabled:
        return "Holiday mode is active"
    if not is_on_mains or is_mains_force_cut_enabled:
        return "Mains not available"
    if (
        isinstance(battery_percentage, (int, float))
        and battery_percentage >= near_full_percent
    ):
        return f"Battery nearly full ({int(battery_percentage)}%)"
    return None


def appliance_mode_block_reason(
    *,
    is_holiday_mode_enabled: bool | None,
    is_on_mains: bool | None,
    is_power_on: bool | None,
) -> str | None:
    """Why appliance mode should not be enabled (official app gates).

    App blocks when holiday is on, mains is available, or inverter power is off.
    UPS mode only shows a soft warning (not a hard block).
    """
    if is_holiday_mode_enabled:
        return "Holiday mode is active"
    if is_on_mains:
        return "Mains already available"
    if not is_power_on:
        return "Inverter switch is OFF"
    return None


def extra_backup_block_reason(
    *,
    is_holiday_mode_enabled: bool | None,
    alarm_data: int | None,
) -> str | None:
    """Why extra backup should not be enabled (official app gates).

    App: “Extra backup available only after low battery.” (alarm bit 2).
    """
    if is_holiday_mode_enabled:
        return "Holiday mode is active"
    if not is_extra_backup_eligible(alarm_data):
        return "Extra backup available only after low battery"
    return None


def status_mains_label(
    *,
    backup_mode: int | None,
    is_mains_force_cut_enabled: bool | None,
    solar_alarm: int | None,
    is_solar: bool,
) -> str:
    """Left status tile: mains available / force cut / fail."""
    if is_backup_on_mains(backup_mode):
        return "Mains Available"
    if is_mains_force_cut_enabled:
        return "Mains Force Cut"
    if is_solar and solar_alarm is not None and (solar_alarm & 1) == 1:
        return "Auto Mains Force Cut"
    return "Mains Fail"


def status_load_label(
    *,
    backup_mode: int | None,
    alarm_data: int | None,
    is_power_on: bool | None,
) -> str:
    """Right status tile: load on mains / inverter / standby."""
    if is_backup_on_mains(backup_mode):
        alarm = int(alarm_data or 0)
        if (alarm & 64) == 64:
            return ""
        return "Load On Mains"
    if is_power_on:
        return "Load On Inverter"
    return "Standby Mode"


def power_cut_display_count(raw: int) -> int:
    """Usage chart count: device raw minus one when raw > 0."""
    return raw - 1 if raw > 0 else 0


def build_power_cut_trends(
    *,
    counts: list[int] | None,
    duration_raws: list[int] | None,
    today: date | None = None,
) -> dict[str, Any]:
    """Build a readable 7-day power-cut trends table (day1 = today).

    Matches the official app Usage → Power Cut Trends series from VG070/VG069.
    """
    today = today or date.today()
    days: list[dict[str, Any]] = []
    n = 7
    for i in range(1, n + 1):
        day_date = today - timedelta(days=i - 1)
        count = counts[i - 1] if counts and len(counts) >= i else None
        raw = duration_raws[i - 1] if duration_raws and len(duration_raws) >= i else None
        duration = duration_hhmm(raw) if isinstance(raw, int) else None
        minutes = None
        if isinstance(raw, int):
            minutes = int(round(duration_hours(raw) * 60))
        days.append(
            {
                "index": i,
                "date": day_date.isoformat(),
                "label": day_date.strftime("%d %b"),
                "count": count,
                "duration": duration,
                "duration_minutes": minutes,
            }
        )

    # Newest → oldest in the app chart (left = older). Present oldest first for tables.
    table_days = list(reversed(days))
    lines = [
        "| Day | Cuts | Duration (HH:MM) |",
        "| :--- | ---: | ---: |",
    ]
    for row in table_days:
        cuts = "—" if row["count"] is None else str(row["count"])
        dur = row["duration"] or "—"
        lines.append(f"| {row['label']} | {cuts} | {dur} |")

    today_row = days[0] if days else None
    if today_row and today_row["count"] is not None:
        summary = (
            f"Cuts: {today_row['count']}, "
            f"Duration: {today_row['duration'] or '—'}"
        )
    else:
        summary = "No power-cut trend data"

    markdown = (
        "### Power Cut Trends (HH:MM)\n\n"
        + "\n".join(lines)
        + "\n\n*Count & duration from the inverter (last 7 days).*"
    )
    return {
        "power_cut_trends": days,
        "power_cut_trends_table": "\n".join(lines),
        "power_cut_trends_markdown": markdown,
        "power_cut_trends_summary": summary,
    }


def label_power_mode(value: int | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "Normal"
    if value == 1:
        return "UPS"
    return "Equipment"


def label_charging_mode(value: int | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "Normal"
    if value == 1:
        return "High"
    if value == 2:
        return "Low"
    return str(value)


def label_battery_type(value: int | None) -> str | None:
    if value is None:
        return None
    return {
        0: "Flat plate",
        1: "Tubular",
        2: "Local",
        3: "SMF",
        4: "Li-ion",
        5: "Mion",
    }.get(value, str(value))


def label_power_saver(value: int | None) -> str | None:
    if value is None:
        return None
    return "Max" if value == 1 else "Normal"


def format_inverter_firmware(value: int | None) -> str | None:
    """Inverter firmware string from VG010 (e.g. 17 → '1.7')."""
    if value is None:
        return None
    return f"{value / 10:g}"


def format_mac_id(raw: str | None) -> str | None:
    """Format aabbccddeeff → AA:BB:CC:DD:EE:FF."""
    if not raw:
        return None
    hex_str = "".join(c for c in str(raw) if c.isalnum()).upper()
    if len(hex_str) % 2:
        hex_str = "0" + hex_str
    if len(hex_str) < 2:
        return hex_str or None
    return ":".join(hex_str[i : i + 2] for i in range(0, len(hex_str), 2))


def derive_device_mac_from_wifi(wifi_mac_raw: str | None) -> str | None:
    """Device MAC shown in product info = Wi‑Fi MAC integer value + 2."""
    if not wifi_mac_raw:
        return None
    hex_str = "".join(c for c in str(wifi_mac_raw) if c.isalnum())
    try:
        value = int(hex_str, 16) + 2
    except ValueError:
        return None
    return format_mac_id(f"{value:x}")


def wifi_signal_quality(rssi: int | None) -> str | None:
    """Map RSSI (dBm) to Excellent / Good / Fair / Poor."""
    if rssi is None:
        return None
    if rssi >= -50:
        return "Excellent"
    if rssi >= -60:
        return "Good"
    if rssi >= -70:
        return "Fair"
    return "Poor"


def wifi_signal_label(rssi: int | None) -> str | None:
    """RSSI with a simple quality band (Poor / Fair / Good / Excellent)."""
    quality = wifi_signal_quality(rssi)
    if rssi is None or quality is None:
        return None
    return f"{rssi} dBm ({quality})"


def performance_backup_slider_positions(level: int | None, power_mode: int | None) -> dict[str, int | None]:
    """Linked performance/backup sliders: positions sum to 8; 0 = mode defaults."""
    if level is None:
        return {"performance_slider": None, "backup_slider": None}
    if level == 0:
        # Unset: UPS mode defaults to 6/2, otherwise 5/3
        perf = 6 if power_mode == 1 else 5
    else:
        perf = level
    return {"performance_slider": perf, "backup_slider": 8 - perf}


def load_alarm_percent(value: int | None) -> int:
    """Overload alarm threshold percent; device uses 500 as a 100% sentinel."""
    if value == 500:
        return 100
    if value is None or not (50 <= value <= 100):
        return 100
    return value


FieldTransform = Callable[[Any], Any]

BASE_FIELD_SPECS: list[tuple[str, str, FieldTransform]] = [
    ("VG042", "model_no", lambda v: _safe_int(v)),
    ("VG094", "is_power_on", lambda v: _eq_int(v, 1)),
    ("VG095", "backup_mode", lambda v: _safe_int(v)),
    ("VG014", "mains_voltage", lambda v: _safe_float(v)),
    ("VG015", "output_voltage", lambda v: _safe_float(v)),
    ("VG038", "is_mains_force_cut_enabled", lambda v: _eq_int(v, 1)),
    ("VG096", "load_percentage", lambda v: _safe_int(v)),
    ("VG098", "battery_energy_raw", lambda v: _safe_int(v)),
    ("VG033", "alarm_data", lambda v: _safe_int(v)),
    ("VG099", "is_turbo_charging", lambda v: _eq_int(v, 1)),
    ("VG018", "charging_current", lambda v: _safe_float(v)),
    ("VG017", "load_current", lambda v: None if _safe_float(v) is None else _safe_float(v) / 10.0),
    ("VG097", "battery_remaining", lambda v: _safe_int(v)),
    ("VG104", "battery_capacity", lambda v: _safe_int(v)),
    ("VG135", "number_of_batteries", lambda v: _safe_int(v)),
    ("VG016", "battery_voltage", lambda v: _safe_float(v)),
    ("VG022", "battery_type", lambda v: _safe_int(v)),
    ("VG036", "is_appliance_mode_enabled", lambda v: _eq_int(v, 1)),
    ("VG100", "is_holiday_mode_enabled", lambda v: _eq_int(v, 1)),
    ("VG072", "is_extra_backup_enabled", lambda v: _eq_int(v, 1000)),
    ("VG037", "main_force_cut_time", lambda v: _safe_int(v)),
    ("VG035", "performance_backup_level", lambda v: _safe_int(v)),
    ("VG035", "high_bit", lambda v: _safe_int(v)),
    ("VG021", "power_mode", lambda v: _safe_int(v)),
    ("VG023", "charging_mode", lambda v: _safe_int(v)),
    ("VG073", "battery_lock_status", lambda v: _safe_int(v)),
    ("VG050", "load_alarm_percent_raw", lambda v: _safe_int(v)),
    ("VG071", "low_battery_alarm", lambda v: _eq_int(v, 1100)),
    # Device reports 1100=ON / 100=OFF; writes use VG071:1 / VG071:0
    ("VG071", "is_advance_battery_low_alarm_enabled", lambda v: _eq_int(v, 1100)),
    ("VG071", "advance_battery_low_alarm_raw", lambda v: _safe_int(v)),
    ("VG034", "buzzer_settings", lambda v: _safe_int(v)),
    ("VG010", "firmware_version", lambda v: _safe_int(v)),
    ("VG012", "wifi_firmware_version", lambda v: None if v in (None, "") else str(v)),
    ("VG136", "ssid_name", lambda v: None if v in (None, "") else str(v)),
    ("VG011", "wifi_rssi", lambda v: _safe_int(v) if _safe_int(v) is not None else (None if v in (None, "") else str(v))),
    ("VG132", "wifi_mac_id", lambda v: None if v in (None, "") else str(v)),
    ("VG003", "serial_number_suffix", lambda v: _safe_int(v)),
    ("VG186", "is_solar_cleaning", lambda v: _eq_int(v, 1)),
    ("VG019", "mains_frequency", lambda v: _safe_float(v)),
    ("VG109", "device_time", lambda v: None if v in (None, "") else str(v)),
    ("VG304", "timezone", lambda v: None if v in (None, "") else str(v)),
    ("VG024", "battery_soc_device", lambda v: _safe_int(v)),
    ("VG020", "vg020_raw", lambda v: _safe_int(v)),
]

SOLAR_FIELD_SPECS: list[tuple[str, str, FieldTransform]] = [
    ("VG184", "solar_alarm", lambda v: _safe_int(v)),
    ("VG141", "solar_current", lambda v: None if _safe_float(v) is None else _safe_float(v) / 100.0),
    ("VG185", "is_day_time_load_usage_enabled", lambda v: _eq_int(v, 0)),
    ("VG219", "night_forced_power_cut_time", lambda v: _safe_int(v)),
    ("VG174", "solar_savings_today", lambda v: _safe_int(v)),
    ("VG088", "solar_savings_yesterday", lambda v: _safe_int(v)),
    ("VG089", "solar_savings_total", lambda v: _safe_int(v)),
    ("VG183", "power_saver_mode", lambda v: _safe_int(v)),
    ("VG213", "solar_battery_capacity_override", lambda v: _safe_int(v)),
]

MAPPED_VG_KEYS: frozenset[str] = frozenset(
    key for key, _, _ in BASE_FIELD_SPECS + SOLAR_FIELD_SPECS
) | frozenset({"VG069", "VG070", "VG033"})
