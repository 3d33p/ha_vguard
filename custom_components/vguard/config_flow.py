"""Config flow for V-Guard Smart."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from . import _pathsetup

_pathsetup.ensure_library_path()

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_BATTERY_CAPACITY_AH,
    CONF_EMAIL,
    CONF_FCM_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL,
    DEFAULT_BATTERY_CAPACITY_AH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import clamp_battery_capacity_ah, clamp_scan_interval


def _number_box(
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    unit: str | None = None,
    step: float = 1,
) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=min_value,
            max=max_value,
            step=step,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


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

    def __init__(self) -> None:
        self._title: str | None = None
        self._entry_data: dict[str, Any] = {}

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
                self._title = title
                self._entry_data = {
                    CONF_EMAIL: email,
                    CONF_PASSWORD: password,
                    CONF_SERIAL: serial or "",
                    **tokens,
                }
                return await self.async_step_battery()

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_SERIAL, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for battery bank capacity after a successful login."""
        if user_input is not None:
            capacity_ah = clamp_battery_capacity_ah(
                user_input.get(CONF_BATTERY_CAPACITY_AH, DEFAULT_BATTERY_CAPACITY_AH)
            )
            return self.async_create_entry(
                title=self._title or "V-Guard",
                data=self._entry_data,
                options={
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    CONF_BATTERY_CAPACITY_AH: capacity_ah,
                },
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BATTERY_CAPACITY_AH, default=DEFAULT_BATTERY_CAPACITY_AH
                ): _number_box(unit="Ah"),
            }
        )
        return self.async_show_form(step_id="battery", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return VGuardOptionsFlow()


class VGuardOptionsFlow(config_entries.OptionsFlow):
    """Options: poll interval and battery bank capacity (Ah)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            seconds = clamp_scan_interval(user_input[CONF_SCAN_INTERVAL])
            capacity_ah = clamp_battery_capacity_ah(
                user_input[CONF_BATTERY_CAPACITY_AH]
            )
            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    CONF_SCAN_INTERVAL: seconds,
                    CONF_BATTERY_CAPACITY_AH: capacity_ah,
                },
            )

        current = clamp_scan_interval(
            self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        current_ah = clamp_battery_capacity_ah(
            self.config_entry.options.get(
                CONF_BATTERY_CAPACITY_AH, DEFAULT_BATTERY_CAPACITY_AH
            )
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): _number_box(
                    min_value=MIN_SCAN_INTERVAL,
                    max_value=MAX_SCAN_INTERVAL,
                    unit="s",
                ),
                vol.Required(CONF_BATTERY_CAPACITY_AH, default=current_ah): _number_box(
                    unit="Ah",
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
