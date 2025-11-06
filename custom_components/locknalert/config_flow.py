"""Config flow for the LocknAlert integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_DEFAULT_MESSAGE,
    CONF_DEFAULT_ROOM_NAME,
    CONF_DEFAULT_SERIAL,
    CONF_DEFAULT_TITLE,
    CONF_SERIAL_NUMBER,
    CONF_TIMEOUT,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    DOMAIN,
)


def _entry_title(data: dict[str, Any]) -> str:
    """Return a title for the config entry."""
    return data.get(CONF_SERIAL_NUMBER) or data.get(CONF_DEFAULT_SERIAL) or data[CONF_BASE_URL]


def _sanitize_user_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize user input for storage."""
    data: dict[str, Any] = dict(user_input)
    data[CONF_BASE_URL] = data[CONF_BASE_URL].rstrip("/")
    data[CONF_TIMEOUT] = int(data[CONF_TIMEOUT])

    optional_keys = (
        CONF_API_KEY,
        CONF_SERIAL_NUMBER,
        CONF_DEFAULT_TITLE,
        CONF_DEFAULT_MESSAGE,
        CONF_DEFAULT_ROOM_NAME,
    )
    for key in optional_keys:
        if not data.get(key):
            data.pop(key, None)

    return data


def _build_data_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the schema used for both configuration and options flows."""
    return vol.Schema(
        {
            vol.Required(
                CONF_BASE_URL,
                default=defaults.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            ): cv.url,
            vol.Optional(
                CONF_API_KEY,
                default=defaults.get(CONF_API_KEY, ""),
            ): cv.string,
            vol.Optional(
                CONF_SERIAL_NUMBER,
                default=defaults.get(CONF_SERIAL_NUMBER, ""),
            ): cv.string,
            vol.Optional(
                CONF_DEFAULT_TITLE,
                default=defaults.get(CONF_DEFAULT_TITLE, ""),
            ): cv.string,
            vol.Optional(
                CONF_DEFAULT_MESSAGE,
                default=defaults.get(CONF_DEFAULT_MESSAGE, ""),
            ): cv.string,
            vol.Optional(
                CONF_DEFAULT_ROOM_NAME,
                default=defaults.get(CONF_DEFAULT_ROOM_NAME, ""),
            ): cv.string,
            vol.Required(
                CONF_TIMEOUT,
                default=defaults.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1)),
        }
    )


class LocknAlertConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LocknAlert."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step of the config flow."""
        if user_input is not None:
            cleaned = _sanitize_user_input(user_input)
            await self.async_set_unique_id(cleaned[CONF_BASE_URL])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_entry_title(cleaned),
                data=cleaned,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_data_schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow handler for this integration."""
        return LocknAlertOptionsFlowHandler(config_entry)


class LocknAlertOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle LocknAlert options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow handler."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Handle the first step of the options flow."""
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the main options step."""
        if user_input is not None:
            cleaned = _sanitize_user_input(user_input)
            return self.async_create_entry(data=cleaned)

        defaults: dict[str, Any] = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="user",
            data_schema=_build_data_schema(defaults),
        )
