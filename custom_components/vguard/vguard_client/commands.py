"""Device command strings published on the Nous topic."""

from __future__ import annotations


def cmd_power(enabled: bool) -> str:
    """Main inverter power: ON → VG094:1, OFF → VG094:0 (dashboard power button)."""
    return "VG094:1" if enabled else "VG094:0"


def cmd_holiday_mode(enabled: bool) -> str:
    return "VG100:1" if enabled else "VG100:0"


def cmd_turbo_charging(enabled: bool) -> str:
    return "VG099:1" if enabled else "VG099:0"


def cmd_appliance_mode(enabled: bool) -> str:
    return "VG036:1" if enabled else "VG036:0"


def cmd_extra_backup(enabled: bool) -> str:
    return "VG072:1" if enabled else "VG072:0"


def cmd_advance_battery_low_alarm(enabled: bool) -> str:
    """Advance Battery Low Alarm: ON → VG071:1, OFF → VG071:0."""
    return "VG071:1" if enabled else "VG071:0"


# Force-cut duration presets used by the official app UI.
# Clearing uses VG037:-1 then VG038:0.
FORCE_CUT_PRESETS_MINUTES: frozenset[int] = frozenset({30, 60, 120, 180, 240})
FORCE_CUT_CLEAR_MINUTES = -1
FORCE_CUT_MIN_MINUTES = 30  # shortest preset; kept for callers/docs


def cmd_force_cut_duration(minutes: int) -> str:
    minutes = int(minutes)
    if minutes == FORCE_CUT_CLEAR_MINUTES:
        return f"VG037:{FORCE_CUT_CLEAR_MINUTES}"
    if minutes not in FORCE_CUT_PRESETS_MINUTES:
        allowed = ", ".join(str(m) for m in sorted(FORCE_CUT_PRESETS_MINUTES))
        raise ValueError(
            f"force-cut minutes must be one of {{{allowed}}} "
            f"(official app presets), or {FORCE_CUT_CLEAR_MINUTES} to clear; got {minutes}"
        )
    return f"VG037:{minutes}"


def cmd_force_cut_enabled(enabled: bool) -> str:
    return "VG038:1" if enabled else "VG038:0"


def cmd_performance_backup_level(level: int) -> str:
    """Performance/backup linked slider: 1..7 (backup = 8 - level)."""
    if not 1 <= int(level) <= 7:
        raise ValueError("performance_backup_level must be 1..7")
    return f"VG035:{int(level)}"


# Overload-alarm percent presets used by the official app UI.
LOAD_ALARM_PRESETS_PERCENT: frozenset[int] = frozenset({50, 60, 70, 80, 90, 100})


def cmd_load_alarm_percent(percent: int) -> str:
    percent = int(percent)
    if percent not in LOAD_ALARM_PRESETS_PERCENT:
        allowed = ", ".join(str(p) for p in sorted(LOAD_ALARM_PRESETS_PERCENT))
        raise ValueError(
            f"load_alarm_percent must be one of {{{allowed}}} "
            f"(official app presets); got {percent}"
        )
    return f"VG050:{percent}"


def cmd_mains_changeover_buzzer(enabled: bool) -> str:
    """ON → VG034:0, OFF → VG034:2."""
    return "VG034:0" if enabled else "VG034:2"


def cmd_day_time_load_usage(enabled: bool) -> str:
    """ON → VG185:0, OFF → VG185:1."""
    return "VG185:0" if enabled else "VG185:1"


def cmd_unlock_battery_type() -> str:
    """Unlock rear battery-type slider for ~30 minutes (app uses VG105:1 over Wi‑Fi)."""
    return "VG105:1"


def cmd_wake() -> str:
    """Deprecated alias — VG105:1 unlocks battery type; it is not a generic wake."""
    return cmd_unlock_battery_type()


# Not exposed as writers: VG021 (inverter/power mode), VG023 (charging mode),
# VG183 (power saver), VG022 (battery type) — rear hardware switches only.
