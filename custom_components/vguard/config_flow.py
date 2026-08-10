"""Config flow for V-Guard Smart."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from . import _pathsetup

_pathsetup.ensure_library_path()

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_FCM_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import clamp_scan_interval


async def _validate(
    hass: HomeAssistant, email: str, password: str, serial: str | None
) -> tuple[str, dict]:
    from vguard_client import VGuardClient
    from vguard_client.auth import generate_fcm_token

    fcm = generate_fcm_token()
    client = VGuardClient(email, password, serial=serial or None, fcm_token=fcm)

    def _connect() -> tuple[str, dict]:
        product = client.connect()
        title = product.serial_number or product.product_nick_name or "V-Guard"
        sess = client.session
        return title, {
            CONF_ACCESS_TOKEN: sess.access_token,
            CONF_REFRESH_TOKEN: sess.refresh_token,
            CONF_FCM_TOKEN: client.fcm_token,
        }

    return await hass.async_add_executor_job(_connect)


class VGuardConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]
            serial = (user_input.get(CONF_SERIAL) or "").strip() or None
            try:
                title, tokens = await _validate(self.hass, email, password, serial)
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(serial or email.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        CONF_SERIAL: serial or "",
                        **tokens,
                    },
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_SERIAL, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return VGuardOptionsFlow()


class VGuardOptionsFlow(config_entries.OptionsFlow):
    """Options: poll interval (stable >= 30s; under 30s is temporary)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            seconds = clamp_scan_interval(user_input[CONF_SCAN_INTERVAL])
            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    CONF_SCAN_INTERVAL: seconds,
                },
            )

        current = clamp_scan_interval(
            self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
