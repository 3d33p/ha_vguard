"""Battery backup-time and charge-time estimation.

All inputs come from the cloud dashboard plus the user-configured bank
capacity (Ah). The inverter does not report time remaining, so these are
physics-informed approximations, not measurements.

Backup (off mains, discharging)
-------------------------------
* Only capacity above a chemistry-specific SOC floor is usable
  (20% for lead-acid, 10% for lithium).
* Net discharge current = load current − solar current (solar offsets the
  draw on solar models).
* Peukert's law: lead-acid capacity is rated at the 20-hour rate (C/20).
  Drawing more current than that yields less usable capacity::

      C_eff = C × (I20 / I) ^ (k − 1)      for I > I20 = C / 20

* hours = C_eff × (SOC − floor) / 100 / I

Charging (CC/CV)
----------------
* Bulk phase: constant current up to the absorption SOC (80% lead-acid,
  95% lithium). Ah needed is divided by the charge (coulombic) efficiency,
  because lead-acid needs ~15% more Ah in than it stores.
* Absorption phase: voltage is held and current tapers roughly
  exponentially down to a tail current (~2% of C for lead-acid)::

      τ = Q_remaining / I ;  t = τ × ln(1 + I / I_tail)

  This replaces the old "remaining Ah ÷ current" model, which badly
  under-estimated time to full once the charger starts tapering.

Currents are noisy poll to poll, so the coordinator feeds them through
:class:`CurrentSmoother` (time-based EMA that snaps on real step changes)
before calling these functions.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

# Ignore near-zero currents (inverter idle / sensor noise).
CURRENT_FLOOR_A = 0.1
# Cap absurd ETAs from tiny currents (1 week).
MAX_ETA_HOURS = 168.0
# Lead-acid capacity rating convention (C20).
RATED_DISCHARGE_HOURS = 20.0
# SOC considered full regardless of the charger state.
FULL_SOC_PERCENT = 99.5

LABEL_MAINS_AVAILABLE = "Mains Available"
LABEL_MAINS_UNAVAILABLE = "Mains Unavailable"
LABEL_FULLY_CHARGED = "Fully Charged"
LABEL_NOT_CHARGING = "Not Charging"
LABEL_RUNNING_ON_SOLAR = "Running on Solar"


@dataclass(frozen=True)
class BatteryProfile:
    """Chemistry parameters used by the estimators."""

    key: str
    soc_floor_percent: float
    peukert_exponent: float
    charge_efficiency: float
    absorption_soc_percent: float
    tail_current_c_rate: float


FLAT_PLATE = BatteryProfile("flat_plate", 20.0, 1.25, 0.85, 80.0, 0.02)
TUBULAR = BatteryProfile("tubular", 20.0, 1.15, 0.85, 80.0, 0.02)
SEALED = BatteryProfile("smf", 20.0, 1.15, 0.88, 80.0, 0.015)
LITHIUM = BatteryProfile("lithium", 10.0, 1.05, 0.97, 95.0, 0.03)

# VG022 battery type → profile (see mappings.label_battery_type).
_TYPE_PROFILES: dict[int, BatteryProfile] = {
    0: FLAT_PLATE,
    1: TUBULAR,
    2: FLAT_PLATE,  # "Local"
    3: SEALED,
    4: LITHIUM,
    5: LITHIUM,  # "Mion"
}


def profile_for_battery_type(battery_type: Any) -> BatteryProfile:
    """Pick a chemistry profile; tubular is the common default for home inverters."""
    try:
        return _TYPE_PROFILES.get(int(battery_type), TUBULAR)
    except (TypeError, ValueError):
        return TUBULAR


def _f(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def precise_soc_percent(
    energy_raw: Any,
    capacity_raw: Any,
    display_percent: Any = None,
) -> float | None:
    """Un-truncated SOC for estimation.

    The gauge value (``battery_percentage``) is an integer capped at 97 and
    forced to 100 in some states, which makes ETAs jump. Prefer the raw
    energy ratio (VG098 / VG104) and fall back to the gauge.
    """
    energy = _f(energy_raw)
    capacity = _f(capacity_raw)
    if energy is not None and capacity is not None and capacity > 0 and energy >= 0:
        return round(max(0.0, min(100.0, energy * 100.0 / capacity)), 1)
    shown = _f(display_percent)
    if shown is None:
        return None
    return max(0.0, min(100.0, shown))


def _clamp_hours(hours: float) -> float:
    if hours <= 0:
        return 0.0
    return round(min(hours, MAX_ETA_HOURS), 2)


def format_hhmm(hours: float) -> str:
    """Format hours as H:MM (e.g. 1.7 → 1:42)."""
    total_minutes = max(0, int(round(float(hours) * 60)))
    h, m = divmod(total_minutes, 60)
    return f"{h}:{m:02d}"


@dataclass(frozen=True)
class EtaResult:
    """An ETA plus the inputs that produced it (exposed as attributes)."""

    state: str | None
    hours: float | None = None
    current_a: float | None = None
    soc_percent: float | None = None
    phase: str | None = None

    def attributes(self) -> dict[str, Any]:
        return {
            "hours": self.hours,
            "minutes": None if self.hours is None else int(round(self.hours * 60)),
            "current_used_a": None if self.current_a is None else round(self.current_a, 2),
            "soc_used_percent": self.soc_percent,
            "phase": self.phase,
        }


def backup_hours(
    capacity_ah: float,
    soc_percent: float,
    discharge_a: float,
    profile: BatteryProfile = TUBULAR,
) -> float | None:
    """Hours until the SOC floor at a constant net discharge current."""
    if capacity_ah <= 0 or discharge_a < CURRENT_FLOOR_A:
        return None
    usable_fraction = (soc_percent - profile.soc_floor_percent) / 100.0
    if usable_fraction <= 0:
        return 0.0
    rated_current = capacity_ah / RATED_DISCHARGE_HOURS
    effective_ah = capacity_ah
    if discharge_a > rated_current and profile.peukert_exponent > 1.0:
        effective_ah = capacity_ah * (rated_current / discharge_a) ** (
            profile.peukert_exponent - 1.0
        )
    return _clamp_hours(effective_ah * usable_fraction / discharge_a)


def charge_hours(
    capacity_ah: float,
    soc_percent: float,
    charge_a: float,
    profile: BatteryProfile = TUBULAR,
) -> tuple[float | None, str]:
    """Hours to full under a CC/CV charger, and the current phase."""
    if soc_percent >= FULL_SOC_PERCENT:
        return 0.0, "full"
    if capacity_ah <= 0 or charge_a < CURRENT_FLOOR_A:
        return None, "idle"
    eff = max(0.5, min(1.0, profile.charge_efficiency))
    absorb = profile.absorption_soc_percent
    tail_a = max(capacity_ah * profile.tail_current_c_rate, CURRENT_FLOOR_A)

    hours = 0.0
    if soc_percent < absorb:
        phase = "bulk"
        hours += capacity_ah * (absorb - soc_percent) / 100.0 / eff / charge_a
        remaining_ah = capacity_ah * (100.0 - absorb) / 100.0 / eff
    else:
        phase = "absorption"
        remaining_ah = capacity_ah * (100.0 - soc_percent) / 100.0 / eff
    tau = remaining_ah / charge_a
    hours += tau * math.log1p(charge_a / tail_a)
    return _clamp_hours(hours), phase


def estimate_backup(
    capacity_ah: Any,
    soc_percent: Any,
    load_current: Any,
    *,
    is_on_mains: bool | None,
    solar_current: Any = None,
    profile: BatteryProfile = TUBULAR,
) -> EtaResult:
    """Backup ETA sensor value while running on battery."""
    if is_on_mains:
        return EtaResult(LABEL_MAINS_AVAILABLE, phase="mains")
    ah = _f(capacity_ah)
    soc = _f(soc_percent)
    load = _f(load_current)
    if ah is None or soc is None or load is None or load < 0:
        return EtaResult(None)
    solar = max(_f(solar_current) or 0.0, 0.0)
    net = load - solar
    if net < CURRENT_FLOOR_A:
        if solar >= CURRENT_FLOOR_A:
            return EtaResult(LABEL_RUNNING_ON_SOLAR, current_a=net, soc_percent=soc, phase="solar")
        return EtaResult(None, current_a=net, soc_percent=soc, phase="idle")
    hours = backup_hours(ah, soc, net, profile)
    if hours is None:
        return EtaResult(None, current_a=net, soc_percent=soc, phase="discharging")
    return EtaResult(format_hhmm(hours), hours, net, soc, "discharging")


def estimate_charging(
    capacity_ah: Any,
    soc_percent: Any,
    charging_current: Any,
    *,
    is_on_mains: bool | None,
    solar_current: Any = None,
    load_current: Any = None,
    display_percent: Any = None,
    profile: BatteryProfile = TUBULAR,
) -> EtaResult:
    """Time-to-full sensor value.

    On mains: charger current + solar. Off mains (solar models): solar minus
    load, so a solar surplus still yields a charge ETA.
    """
    ah = _f(capacity_ah)
    soc = _f(soc_percent)
    solar = max(_f(solar_current) or 0.0, 0.0)
    if is_on_mains:
        charge = max(_f(charging_current) or 0.0, 0.0) + solar
    else:
        charge = solar - max(_f(load_current) or 0.0, 0.0)
        if charge < CURRENT_FLOOR_A:
            return EtaResult(LABEL_MAINS_UNAVAILABLE, phase="mains_unavailable")

    if soc is not None and soc >= FULL_SOC_PERCENT:
        return EtaResult(LABEL_FULLY_CHARGED, 0.0, charge, soc, "full")
    # Gauge shows 100 ("charged") and the charger has dropped to float/tail.
    shown = _f(display_percent)
    if shown is not None and shown >= 100 and ah is not None:
        if charge <= max(ah * profile.tail_current_c_rate, CURRENT_FLOOR_A):
            return EtaResult(LABEL_FULLY_CHARGED, 0.0, charge, soc, "full")
    if charge < CURRENT_FLOOR_A:
        return EtaResult(LABEL_NOT_CHARGING, current_a=charge, soc_percent=soc, phase="idle")
    if ah is None or soc is None:
        return EtaResult(None, current_a=charge, soc_percent=soc)
    hours, phase = charge_hours(ah, soc, charge, profile)
    if hours is None:
        return EtaResult(LABEL_NOT_CHARGING, current_a=charge, soc_percent=soc, phase=phase)
    if hours <= 0:
        return EtaResult(LABEL_FULLY_CHARGED, 0.0, charge, soc, "full")
    return EtaResult(format_hhmm(hours), hours, charge, soc, phase)


class CurrentSmoother:
    """Time-based exponential moving average for noisy current readings.

    * ``time_constant_s`` sets how quickly it follows (≈63% after one τ),
      independent of the poll interval.
    * Large step changes (an appliance switched on/off) are followed
      quickly instead of lagging for minutes.
    * Long gaps or a ``reset()`` (e.g. mains state flip) restart it.
    """

    def __init__(
        self,
        time_constant_s: float = 90.0,
        *,
        max_gap_s: float = 900.0,
        step_ratio: float = 0.4,
        step_min_a: float = 1.5,
    ) -> None:
        self.time_constant_s = max(1.0, float(time_constant_s))
        self.max_gap_s = max_gap_s
        self.step_ratio = step_ratio
        self.step_min_a = step_min_a
        self._value: float | None = None
        self._ts: float | None = None

    @property
    def value(self) -> float | None:
        return self._value

    def reset(self) -> None:
        self._value = None
        self._ts = None

    def update(self, sample: Any, now: float) -> float | None:
        x = _f(sample)
        if x is None:
            self.reset()
            return None
        if self._value is None or self._ts is None or now - self._ts > self.max_gap_s:
            self._value, self._ts = x, now
            return x
        dt = max(0.0, now - self._ts)
        alpha = 1.0 - math.exp(-dt / self.time_constant_s)
        delta = x - self._value
        if abs(delta) >= max(self.step_min_a, self.step_ratio * abs(self._value)):
            alpha = max(alpha, 0.7)
        self._value += alpha * delta
        self._ts = now
        return round(self._value, 3)
