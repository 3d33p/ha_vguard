"""Data update coordinator."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any, Callable, Coroutine

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from vguard_client import VGuardClient, VGuardError
from vguard_client import estimates

from .const import (
    ACTIVE_POLL_HOLD_S,
    ACTIVE_POLL_INTERVAL,
    BACKOFF_AFTER_FAILURES,
    CONF_BATTERY_CAPACITY_AH,
    CONF_SCAN_INTERVAL,
    DEFAULT_BATTERY_CAPACITY_AH,
    DEFAULT_SCAN_INTERVAL,
    MAX_BACKOFF_INTERVAL,
    MAX_BATTERY_CAPACITY_AH,
    MAX_SCAN_INTERVAL,
    MIN_BATTERY_CAPACITY_AH,
    MIN_SCAN_INTERVAL,
    MIN_STABLE_SCAN_INTERVAL,
    POST_WRITE_CONFIRM_GRACE_S,
)

_LOGGER = logging.getLogger(__name__)

# Cloud subscribe often lags behind publishes; keep local overrides briefly.
_OPTIMISTIC_HOLD_S = 25.0
# First confirmation poll after a write (cloud cache needs a moment).
_POST_WRITE_FIRST_REFRESH_S = 3.0

# Smoothing time constants for currents feeding the ETAs (seconds).
_LOAD_SMOOTHING_S = 90.0
_CHARGE_SMOOTHING_S = 180.0


def clamp_scan_interval(seconds: float | int) -> int:
    """Clamp poll interval to the allowed range."""
    try:
        value = int(round(float(seconds)))
    except (TypeError, ValueError):
        value = DEFAULT_SCAN_INTERVAL
    return max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, value))


def clamp_battery_capacity_ah(value: float | int | None) -> int:
    """Clamp configured battery bank capacity (Ah)."""
    try:
        ah = int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        ah = DEFAULT_BATTERY_CAPACITY_AH
    return max(MIN_BATTERY_CAPACITY_AH, min(MAX_BATTERY_CAPACITY_AH, ah))


class VGuardDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the cloud subscribe API and expose the mapped dashboard.

    Polling policy:

    * **Stable** rate (default 60s, user-set ≥30s, persisted).
    * **Temporary** rate (user-set <30s) for ``ACTIVE_POLL_HOLD_S``.
    * **Live** 6s bursts from the Live Updates button (full hold window) or
      after a write (ends a few seconds after the cloud confirms the change).
    * **Backoff**: after repeated failures / offline payloads the interval
      doubles up to ``MAX_BACKOFF_INTERVAL`` and resets on the next success.
    * Identical cloud payloads don't re-write entity states.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: VGuardClient,
        *,
        entry: ConfigEntry,
        idle_interval: int,
    ) -> None:
        idle = clamp_scan_interval(idle_interval)
        if idle < MIN_STABLE_SCAN_INTERVAL:
            idle = DEFAULT_SCAN_INTERVAL
        super().__init__(
            hass,
            _LOGGER,
            name="V-Guard Smart",
            update_interval=timedelta(seconds=idle),
            config_entry=entry,
            # Skip entity writes when a poll returns the same data.
            always_update=False,
        )
        self.client = client
        self._entry = entry
        self._last_stable_rate = idle
        self._display_interval = idle
        self._temp_interval: int | None = None
        self._live_mode = False
        self._live_from_write = False
        self._failures = 0
        self._hold_task: asyncio.Task[None] | None = None
        self._post_write_task: asyncio.Task[None] | None = None
        self._optimistic: dict[str, Any] = {}
        self._optimistic_until = 0.0
        self._interval_listeners: list[Callable[[], None]] = []
        self._last_payload: str | None = None
        self._last_on_mains: bool | None = None
        self._load_avg = estimates.CurrentSmoother(_LOAD_SMOOTHING_S)
        self._charge_avg = estimates.CurrentSmoother(_CHARGE_SMOOTHING_S)
        self._solar_avg = estimates.CurrentSmoother(_CHARGE_SMOOTHING_S)

    # ---- Poll interval ---------------------------------------------------

    @property
    def poll_interval_seconds(self) -> int:
        """Effective poll interval currently in use (for the Poll Interval entity)."""
        return self._display_interval

    @property
    def last_stable_rate(self) -> int:
        return self._last_stable_rate

    @property
    def is_live_polling(self) -> bool:
        return self._live_mode

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    @property
    def is_backing_off(self) -> bool:
        return self._failures >= BACKOFF_AFTER_FAILURES

    def async_add_interval_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._interval_listeners.append(listener)

        def _remove() -> None:
            if listener in self._interval_listeners:
                self._interval_listeners.remove(listener)

        return _remove

    def _notify_interval_listeners(self) -> None:
        for listener in list(self._interval_listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Poll interval listener failed")

    def _base_interval(self) -> int:
        if self._live_mode:
            return ACTIVE_POLL_INTERVAL
        if self._temp_interval is not None:
            return self._temp_interval
        return self._last_stable_rate

    def _effective_interval(self) -> int:
        base = self._base_interval()
        if self._failures >= BACKOFF_AFTER_FAILURES:
            # Fast polling is pointless while the cloud/device is failing.
            factor = 2 ** min(self._failures - BACKOFF_AFTER_FAILURES + 1, 6)
            return min(max(base, self._last_stable_rate) * factor, MAX_BACKOFF_INTERVAL)
        return base

    def _apply_interval(self) -> None:
        seconds = self._effective_interval()
        changed = seconds != self._display_interval
        self._display_interval = seconds
        self.update_interval = timedelta(seconds=seconds)
        if changed:
            self._notify_interval_listeners()

    def _persist_stable_rate(self, seconds: int) -> None:
        seconds = clamp_scan_interval(seconds)
        if seconds < MIN_STABLE_SCAN_INTERVAL:
            return
        self._last_stable_rate = seconds
        if self._entry.options.get(CONF_SCAN_INTERVAL) == seconds:
            return
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_SCAN_INTERVAL: seconds},
        )

    def _create_task(
        self, coro: Coroutine[Any, Any, None], name: str
    ) -> asyncio.Task[None]:
        # Background tasks don't block HA startup and are cancelled on unload.
        return self._entry.async_create_background_task(self.hass, coro, name)

    def _cancel_hold_task(self) -> None:
        if self._hold_task and not self._hold_task.done():
            self._hold_task.cancel()
        self._hold_task = None

    def _end_fast_polling(self) -> None:
        self._live_mode = False
        self._live_from_write = False
        self._temp_interval = None
        self._apply_interval()
        _LOGGER.debug("Poll interval back to stable %ss", self._last_stable_rate)

    def _schedule_revert_to_stable(self, delay: float = ACTIVE_POLL_HOLD_S) -> None:
        self._cancel_hold_task()

        async def _revert() -> None:
            await asyncio.sleep(delay)
            self._hold_task = None
            self._end_fast_polling()

        self._hold_task = self._create_task(_revert(), "vguard_poll_revert")

    def _speed_up_next_poll(self) -> None:
        """Re-arm the timer so a shorter interval takes effect now, not after
        the previously scheduled (possibly 60s+) tick."""
        if self._listeners and hasattr(self, "_schedule_refresh"):
            self._unschedule_refresh()
            self._schedule_refresh()

    @callback
    def enter_live_polling(self, *, from_write: bool = False) -> None:
        """Poll at 6s for the hold window, then return to last stable rate."""
        self._live_mode = True
        self._live_from_write = from_write
        self._apply_interval()
        self._speed_up_next_poll()
        self._schedule_revert_to_stable()
        _LOGGER.debug(
            "Live polling %ss for up to %ss (stable fallback %ss)",
            ACTIVE_POLL_INTERVAL,
            ACTIVE_POLL_HOLD_S,
            self._last_stable_rate,
        )

    @callback
    def set_poll_interval(self, seconds: float | int) -> None:
        """Apply a user-selected poll interval (stable if >= 30s, else temporary)."""
        seconds = clamp_scan_interval(seconds)
        previous = self._display_interval
        if seconds >= MIN_STABLE_SCAN_INTERVAL:
            self._cancel_hold_task()
            self._live_mode = False
            self._live_from_write = False
            self._temp_interval = None
            self._persist_stable_rate(seconds)
            self._apply_interval()
            _LOGGER.debug("Stable poll interval set to %ss", seconds)
        else:
            # Temporary aggressive rate — never persisted.
            self._live_mode = False
            self._live_from_write = False
            self._temp_interval = seconds
            self._apply_interval()
            self._schedule_revert_to_stable()
            _LOGGER.debug(
                "Temporary poll interval %ss for %ss (stable fallback %ss)",
                seconds,
                ACTIVE_POLL_HOLD_S,
                self._last_stable_rate,
            )
        if self._display_interval < previous:
            self._speed_up_next_poll()

    # ---- Writes / optimistic state ----------------------------------------

    def apply_optimistic(self, updates: dict[str, Any]) -> None:
        """Patch dashboard fields immediately after a successful publish."""
        if not updates:
            return
        self._optimistic.update(updates)
        self._optimistic_until = time.monotonic() + _OPTIMISTIC_HOLD_S
        merged = {**(self.data or {}), **updates}
        self._refresh_derived(merged)
        self.async_set_updated_data(merged)

    def async_schedule_refresh_after_write(self) -> None:
        """Live-poll until the cloud confirms the write (max hold window)."""
        if self._post_write_task and not self._post_write_task.done():
            self._post_write_task.cancel()
        self.enter_live_polling(from_write=True)

        async def _first_confirm() -> None:
            await asyncio.sleep(_POST_WRITE_FIRST_REFRESH_S)
            await self.async_request_refresh()

        self._post_write_task = self._create_task(
            _first_confirm(), "vguard_post_write_refresh"
        )

    def _merge_optimistic(self, data: dict[str, Any]) -> None:
        now = time.monotonic()
        if not self._optimistic:
            pass
        elif now >= self._optimistic_until:
            _LOGGER.debug("Optimistic hold expired; trusting cloud state")
            self._optimistic.clear()
            self._optimistic_until = 0.0
        else:
            still: dict[str, Any] = {}
            for key, value in self._optimistic.items():
                if data.get(key) == value:
                    continue
                still[key] = value
                data[key] = value
            self._optimistic = still
            if still:
                _LOGGER.debug(
                    "Keeping optimistic overrides until cloud catches up: %s",
                    list(still),
                )
            else:
                self._optimistic_until = 0.0

        if not self._optimistic and self._live_from_write and self._live_mode:
            # Confirmed (or given up) — one or two more polls, then go idle.
            self._live_from_write = False
            self._schedule_revert_to_stable(POST_WRITE_CONFIRM_GRACE_S)

    # ---- Polling ----------------------------------------------------------

    def _fetch_dashboard(self):
        # No long empty-cache retry loop — the poll interval already retries.
        return self.client.get_dashboard(wake=False, retries=0)

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures == BACKOFF_AFTER_FAILURES:
            _LOGGER.info(
                "V-Guard cloud failing repeatedly; backing off polling (max %ss)",
                MAX_BACKOFF_INTERVAL,
            )
        self._apply_interval()

    def _record_success(self) -> None:
        if self._failures >= BACKOFF_AFTER_FAILURES:
            _LOGGER.info("V-Guard cloud recovered; resuming normal polling")
        self._failures = 0
        self._apply_interval()

    def _keep_last_or_fail(self, exc: Exception) -> dict[str, Any]:
        """Avoid Unavailable flicker when a single subscribe poll fails."""
        self._record_failure()
        if self.data is not None:
            log = _LOGGER.debug if self._failures > BACKOFF_AFTER_FAILURES else _LOGGER.warning
            log("V-Guard poll failed; keeping last state: %s", exc)
            return self.data
        raise UpdateFailed(str(exc)) from exc

    def _mark_offline_or_fail(self, exc: Exception) -> dict[str, Any]:
        """Empty subscribe payload: keep readings, clear online flag."""
        self._record_failure()
        self._last_payload = None
        if self.data is not None:
            log = _LOGGER.debug if self._failures > BACKOFF_AFTER_FAILURES else _LOGGER.warning
            log("V-Guard device offline or empty cache: %s", exc)
            return {**self.data, "is_online": False}
        raise UpdateFailed(str(exc)) from exc

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            snap = await self.hass.async_add_executor_job(self._fetch_dashboard)
        except VGuardError as exc:
            message = str(exc).lower()
            if "no device payload" in message or "offline" in message:
                return self._mark_offline_or_fail(exc)
            return self._keep_last_or_fail(exc)
        except Exception as exc:  # noqa: BLE001
            return self._keep_last_or_fail(exc)

        self._record_success()
        data = dict(snap.dashboard)
        data["is_online"] = True

        # Only feed the smoothers fresh packets: the cloud often serves the
        # same cached packet to several polls in a row.
        fresh = snap.decrypted != self._last_payload
        self._last_payload = snap.decrypted
        if fresh:
            self._update_smoothers(data)
        self._carry_smoothed(data)

        self._merge_optimistic(data)
        self._refresh_derived(data)
        return data

    def _update_smoothers(self, data: dict[str, Any]) -> None:
        now = time.monotonic()
        on_mains = data.get("is_on_mains")
        if self._last_on_mains is not None and on_mains != self._last_on_mains:
            # Mains cut/restore changes what the currents mean — start over.
            self._load_avg.reset()
            self._charge_avg.reset()
            self._solar_avg.reset()
        self._last_on_mains = on_mains
        self._load_avg.update(data.get("load_current"), now)
        self._charge_avg.update(data.get("charging_current"), now)
        self._solar_avg.update(data.get("solar_current"), now)

    def _carry_smoothed(self, data: dict[str, Any]) -> None:
        data["load_current_avg"] = _round(self._load_avg.value)
        data["charging_current_avg"] = _round(self._charge_avg.value)
        data["solar_current_avg"] = _round(self._solar_avg.value)

    # ---- Derived fields ---------------------------------------------------

    def _configured_capacity_ah(self) -> int:
        return clamp_battery_capacity_ah(
            self._entry.options.get(
                CONF_BATTERY_CAPACITY_AH, DEFAULT_BATTERY_CAPACITY_AH
            )
        )

    @callback
    def refresh_time_estimates(self) -> None:
        """Recompute backup/charging ETAs after options (Ah) change."""
        if self.data is None:
            return
        data = dict(self.data)
        self._refresh_derived(data)
        self.async_set_updated_data(data)

    def _refresh_derived(self, data: dict[str, Any]) -> None:
        """Keep derived dashboard fields consistent after patches."""
        data["is_turbo_charging_switch_on"] = bool(
            data.get("is_turbo_charging") and data.get("is_on_mains")
        )
        capacity_ah = self._configured_capacity_ah()
        data["battery_capacity_ah"] = capacity_ah
        profile = estimates.profile_for_battery_type(data.get("battery_type"))
        soc = estimates.precise_soc_percent(
            data.get("battery_energy_raw"),
            data.get("battery_capacity"),
            data.get("battery_percentage"),
        )
        load = _pick(data, "load_current_avg", "load_current")
        charge = _pick(data, "charging_current_avg", "charging_current")
        solar = _pick(data, "solar_current_avg", "solar_current")

        backup = estimates.estimate_backup(
            capacity_ah,
            soc,
            load,
            is_on_mains=data.get("is_on_mains"),
            solar_current=solar,
            profile=profile,
        )
        charging = estimates.estimate_charging(
            capacity_ah,
            soc,
            charge,
            is_on_mains=data.get("is_on_mains"),
            solar_current=solar,
            load_current=load,
            display_percent=data.get("battery_percentage"),
            profile=profile,
        )
        data["battery_profile"] = profile.key
        data["estimated_backup_hours"] = backup.state
        data["estimated_backup_details"] = backup.attributes()
        data["estimated_charging_hours"] = charging.state
        data["estimated_charging_details"] = charging.attributes()


def _pick(data: dict[str, Any], preferred: str, fallback: str) -> Any:
    value = data.get(preferred)
    return data.get(fallback) if value is None else value


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)
