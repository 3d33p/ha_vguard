"""V-Guard Smart Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from . import _pathsetup

_pathsetup.ensure_library_path()

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_FCM_TOKEN,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_STABLE_SCAN_INTERVAL,
)
from .coordinator import VGuardDataUpdateCoordinator, clamp_scan_interval

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
]

# Removed when force-cut became a select dropdown (and clear became Off).
_STALE_FORCE_CUT_ENTITIES: tuple[tuple[str, str], ...] = (
    ("switch", "force_power_cut"),
    ("button", "clear_force_power_cut"),
    # Consolidated into the single "Power cut trends" sensor.
    ("sensor", "power_cut_today_count"),
    ("sensor", "power_cut_today_duration"),
    # Inverter Mode moved from diagnostic sensor → select control.
    ("sensor", "power_mode_label"),
)


def _remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    registry = er.async_get(hass)
    for domain, key in _STALE_FORCE_CUT_ENTITIES:
        unique_id = f"{entry.entry_id}_{key}"
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        if entity_id:
            _LOGGER.info("Removing obsolete entity %s (%s)", entity_id, unique_id)
            registry.async_remove(entity_id)


def _persist_session(hass: HomeAssistant, entry: ConfigEntry, client) -> None:
    """Save tokens + stable FCM id into the config entry."""
    session = client.session
    new_data = {
        **entry.data,
        CONF_ACCESS_TOKEN: session.access_token,
        CONF_REFRESH_TOKEN: session.refresh_token,
        CONF_FCM_TOKEN: client.fcm_token,
    }
    if new_data == dict(entry.data):
        return
    hass.config_entries.async_update_entry(entry, data=new_data)
    _LOGGER.debug("Persisted V-Guard session tokens for %s", entry.title)


def _idle_poll_seconds(entry: ConfigEntry) -> int:
    raw = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    idle = clamp_scan_interval(raw)
    if idle < MIN_STABLE_SCAN_INTERVAL:
        return DEFAULT_SCAN_INTERVAL
    return idle


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up V-Guard from a config entry."""
    from vguard_client import VGuardClient
    from vguard_client.auth import generate_fcm_token

    fcm = entry.data.get(CONF_FCM_TOKEN) or generate_fcm_token()

    def _on_session_update(session) -> None:  # noqa: ANN001
        # Called from worker threads after login/refresh.
        new_data = {
            **entry.data,
            CONF_ACCESS_TOKEN: session.access_token,
            CONF_REFRESH_TOKEN: session.refresh_token,
            CONF_FCM_TOKEN: fcm,
        }
        hass.loop.call_soon_threadsafe(
            lambda: hass.config_entries.async_update_entry(entry, data=new_data)
        )

    client = VGuardClient(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        serial=entry.data.get(CONF_SERIAL) or None,
        fcm_token=fcm,
        access_token=entry.data.get(CONF_ACCESS_TOKEN) or None,
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN) or None,
        on_session_update=_on_session_update,
    )

    try:
        await hass.async_add_executor_job(client.connect)
    except Exception as exc:  # noqa: BLE001
        raise ConfigEntryNotReady(f"Unable to connect to V-Guard cloud: {exc}") from exc

    # Ensure FCM + tokens are stored even when connect reused an already-valid access token.
    _persist_session(hass, entry, client)

    idle_s = _idle_poll_seconds(entry)
    # Ensure options carry a stable default for the UI / Poll Interval entity.
    if entry.options.get(CONF_SCAN_INTERVAL) != idle_s:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_SCAN_INTERVAL: idle_s}
        )

    coordinator = VGuardDataUpdateCoordinator(
        hass,
        client,
        entry=entry,
        idle_interval=idle_s,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _remove_stale_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply options changes (e.g. poll interval) without full reconnect when possible."""
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not stored:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    coordinator: VGuardDataUpdateCoordinator = stored["coordinator"]
    requested = clamp_scan_interval(
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    # Ignore echo from coordinator persisting the same stable rate.
    if (
        requested >= MIN_STABLE_SCAN_INTERVAL
        and not coordinator.is_live_polling
        and coordinator.last_stable_rate == requested
        and coordinator.poll_interval_seconds == requested
    ):
        return
    coordinator.set_poll_interval(requested)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
