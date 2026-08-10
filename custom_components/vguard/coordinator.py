"""Data update coordinator."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from vguard_client import VGuardClient, VGuardError

from .const import (
    ACTIVE_POLL_HOLD_S,
    ACTIVE_POLL_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_STABLE_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Cloud subscribe often lags behind publishes; keep local overrides briefly.
_OPTIMISTIC_HOLD_S = 25.0
_POST_WRITE_REFRESH_DELAYS_S = (3.0, 8.0, 16.0)


def clamp_scan_interval(seconds: float | int) -> int:
    """Clamp poll interval to the allowed range."""
    try:
        value = int(round(float(seconds)))
    except (TypeError, ValueError):
        value = DEFAULT_SCAN_INTERVAL
    return max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, value))


class VGuardDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the cloud subscribe API and expose the mapped dashboard."""

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
        )
        self.client = client
        self._entry = entry
        self._last_stable_rate = idle
        self._display_interval = idle
        self._live_mode = False
        self._hold_task: asyncio.Task[None] | None = None
        self._optimistic: dict[str, Any] = {}
        self._optimistic_until = 0.0
        self._post_write_task: asyncio.Task[None] | None = None
        self._interval_listeners: list[Callable[[], None]] = []

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

    def async_add_interval_listener(self, listener: Callable[[], None]) -> None:
        self._interval_listeners.append(listener)

    def _notify_interval_listeners(self) -> None:
        for listener in list(self._interval_listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Poll interval listener failed")

    def _set_update_interval(self, seconds: int) -> None:
        seconds = clamp_scan_interval(seconds)
        self._display_interval = seconds
        self.update_interval = timedelta(seconds=seconds)
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

    def _cancel_hold_task(self) -> None:
        if self._hold_task and not self._hold_task.done():
            self._hold_task.cancel()
        self._hold_task = None

    def _schedule_revert_to_stable(self) -> None:
        self._cancel_hold_task()

        async def _revert() -> None:
            try:
                await asyncio.sleep(ACTIVE_POLL_HOLD_S)
            except asyncio.CancelledError:
                raise
            self._live_mode = False
            self._set_update_interval(self._last_stable_rate)
            _LOGGER.debug(
                "Poll interval reverted to stable %ss",
                self._last_stable_rate,
            )

        self._hold_task = self.hass.async_create_task(_revert())

    @callback
    def enter_live_polling(self) -> None:
        """Poll at 6s for the hold window, then return to last stable rate."""
        self._live_mode = True
        self._set_update_interval(ACTIVE_POLL_INTERVAL)
        self._schedule_revert_to_stable()
        _LOGGER.debug(
            "Live polling %ss for %ss (stable fallback %ss)",
            ACTIVE_POLL_INTERVAL,
            ACTIVE_POLL_HOLD_S,
            self._last_stable_rate,
        )

    @callback
    def set_poll_interval(self, seconds: float | int) -> None:
        """Apply a user-selected poll interval (stable if >= 30s, else temporary)."""
        seconds = clamp_scan_interval(seconds)
        if seconds >= MIN_STABLE_SCAN_INTERVAL:
            self._live_mode = False
            self._cancel_hold_task()
            self._persist_stable_rate(seconds)
            self._set_update_interval(seconds)
            _LOGGER.debug("Stable poll interval set to %ss", seconds)
            return

        # Temporary aggressive rate — never persist.
        self._live_mode = False
        self._set_update_interval(seconds)
        self._schedule_revert_to_stable()
        _LOGGER.debug(
            "Temporary poll interval %ss for %ss (stable fallback %ss)",
            seconds,
            ACTIVE_POLL_HOLD_S,
            self._last_stable_rate,
        )

    def apply_optimistic(self, updates: dict[str, Any]) -> None:
        """Patch dashboard fields immediately after a successful publish."""
        if not updates:
            return
        self._optimistic.update(updates)
        self._optimistic_until = time.monotonic() + _OPTIMISTIC_HOLD_S
        merged = {**(self.data or {}), **updates}
        _refresh_derived(merged)
        self.async_set_updated_data(merged)

    def async_schedule_refresh_after_write(self) -> None:
        """Refresh later so the cloud cache has time to catch up; enter live polling."""
        self.enter_live_polling()
        if self._post_write_task and not self._post_write_task.done():
            self._post_write_task.cancel()

        async def _run() -> None:
            elapsed = 0.0
            try:
                for delay in _POST_WRITE_REFRESH_DELAYS_S:
                    await asyncio.sleep(delay - elapsed)
                    elapsed = delay
                    await self.async_request_refresh()
                    if not self._optimistic:
                        return
            except asyncio.CancelledError:
                raise

        self._post_write_task = self.hass.async_create_task(_run())

    def _fetch_dashboard(self):
        # No long empty-cache retry loop — idle/live intervals already retry.
        return self.client.get_dashboard(wake=False, retries=0)

    def _keep_last_or_fail(self, exc: Exception) -> dict[str, Any]:
        """Avoid Unavailable flicker when a single subscribe poll fails."""
        if self.data is not None:
            _LOGGER.warning("V-Guard poll failed; keeping last state: %s", exc)
            return self.data
        raise UpdateFailed(str(exc)) from exc

    def _mark_offline_or_fail(self, exc: Exception) -> dict[str, Any]:
        """Empty subscribe payload: keep readings, clear online flag."""
        if self.data is not None:
            _LOGGER.warning("V-Guard device offline or empty cache: %s", exc)
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

        data = dict(snap.dashboard)
        data["is_online"] = True
        now = time.monotonic()
        if self._optimistic and now < self._optimistic_until:
            still: dict[str, Any] = {}
            for key, value in self._optimistic.items():
                if data.get(key) == value:
                    continue
                still[key] = value
                data[key] = value
            self._optimistic = still
            if not still:
                self._optimistic_until = 0.0
            else:
                _LOGGER.debug(
                    "Keeping optimistic overrides until cloud catches up: %s",
                    list(still),
                )
        else:
            if self._optimistic and now >= self._optimistic_until:
                _LOGGER.debug("Optimistic hold expired; trusting cloud state")
            self._optimistic.clear()
            self._optimistic_until = 0.0

        _refresh_derived(data)
        return data


def _refresh_derived(data: dict[str, Any]) -> None:
    """Keep derived dashboard fields consistent after patches."""
    data["is_turbo_charging_switch_on"] = bool(
        data.get("is_turbo_charging") and data.get("is_on_mains")
    )
