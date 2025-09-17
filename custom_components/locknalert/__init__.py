"""LocknAlert Home Assistant integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
import json
import logging
from typing import Any

import async_timeout
from aiohttp import ClientError, ContentTypeError
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client, config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_EVENT_TIME,
    ATTR_SERIAL,
    ATTR_SERIAL_NUMBER,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_DEFAULT_SERIAL,
    CONF_TIMEOUT,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    FALL_ENDPOINT,
    SERVICE_TRIGGER_FALL,
)

_LOGGER = logging.getLogger(__name__)


_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): cv.url,
        vol.Optional(CONF_API_KEY): cv.string,
        vol.Optional(CONF_DEFAULT_SERIAL): cv.string,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    }
)


def _validate_config(value: Any) -> list[dict[str, Any]]:
    """Validate configuration and normalize to a list of server entries."""
    if isinstance(value, Mapping):
        value = [value]

    entries = []
    for item in cv.ensure_list(value):
        entries.append(_ENTRY_SCHEMA(item))
    return entries


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: _validate_config,
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: Mapping[str, Any]) -> bool:
    """Set up the LocknAlert integration from configuration.yaml."""
    if DOMAIN not in config:
        return True

    hass.data.setdefault(DOMAIN, [])

    for entry in config[DOMAIN]:
        client = _LocknAlertClient(
            hass,
            base_url=entry[CONF_BASE_URL],
            api_key=entry.get(CONF_API_KEY),
            default_serial=entry.get(CONF_DEFAULT_SERIAL),
            timeout=entry[CONF_TIMEOUT],
        )
        hass.data[DOMAIN].append(client)

    if not hass.services.has_service(DOMAIN, SERVICE_TRIGGER_FALL):
        hass.services.async_register(
            DOMAIN,
            SERVICE_TRIGGER_FALL,
            _build_trigger_service_handler(hass),
            schema=_fall_service_schema(),
        )

    return True


def _fall_service_schema() -> vol.Schema:
    """Return the service schema for triggering a fall alert."""
    return vol.Schema(
        {
            vol.Optional(ATTR_SERIAL): cv.string,
            vol.Optional(ATTR_SERIAL_NUMBER): cv.string,
            vol.Optional("title"): cv.string,
            vol.Optional("message"): cv.string,
            vol.Optional("room_name"): cv.string,
            vol.Optional(ATTR_EVENT_TIME): vol.Any(cv.datetime, cv.string),
        },
        extra=vol.ALLOW_EXTRA,
    )


def _build_trigger_service_handler(hass: HomeAssistant):
    """Build a service handler bound to the current Home Assistant instance."""

    async def _async_handle_service(call: ServiceCall) -> None:
        if DOMAIN not in hass.data:
            raise HomeAssistantError(
                "LocknAlert is not configured. Add locknalert to configuration.yaml."
            )

        if not hass.data[DOMAIN]:
            raise HomeAssistantError("No LocknAlert servers are configured.")

        tasks = [client.async_trigger_fall_alert(call.data) for client in hass.data[DOMAIN]]
        await asyncio.gather(*tasks)

    return _async_handle_service


class _LocknAlertClient:
    """Handle interactions with a LocknAlert server instance."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        base_url: str,
        api_key: str | None,
        default_serial: str | None,
        timeout: int,
    ) -> None:
        self._session = aiohttp_client.async_get_clientsession(hass)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_serial = default_serial
        self._timeout = timeout

    async def async_trigger_fall_alert(self, data: Mapping[str, Any]) -> None:
        """Send a fall alert payload to the LocknAlert server."""
        payload: dict[str, Any] = dict(data)

        serial = payload.get(ATTR_SERIAL) or payload.get(ATTR_SERIAL_NUMBER) or self._default_serial
        if not serial:
            raise HomeAssistantError(
                "Call must include 'serial' or 'serial_number', or configure default_serial."
            )

        if ATTR_SERIAL not in payload and ATTR_SERIAL_NUMBER not in payload:
            payload[ATTR_SERIAL] = serial

        if ATTR_EVENT_TIME in payload:
            normalized_time = self._normalize_event_time(payload[ATTR_EVENT_TIME])
            if normalized_time is not None:
                payload[ATTR_EVENT_TIME] = normalized_time

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}{FALL_ENDPOINT}"
        _LOGGER.debug("Sending LocknAlert fall alert to %s with payload %s", url, payload)

        try:
            async with async_timeout.timeout(self._timeout):
                response = await self._session.post(url, json=payload, headers=headers)
        except asyncio.TimeoutError as err:
            raise HomeAssistantError(
                "Timeout communicating with the LocknAlert server"
            ) from err
        except ClientError as err:
            raise HomeAssistantError("Error communicating with the LocknAlert server") from err

        try:
            response_data = await response.json(content_type=None)
        except (ContentTypeError, json.JSONDecodeError):
            response_data = await response.text()

        if response.status >= 400:
            raise HomeAssistantError(
                f"LocknAlert server returned HTTP {response.status}: {response_data}"
            )

        _LOGGER.debug("LocknAlert server response: %s", response_data)

    def _normalize_event_time(self, value: Any) -> str | None:
        """Normalize event time to an ISO formatted UTC string."""
        dt_value: datetime | None
        if isinstance(value, datetime):
            dt_value = value
        else:
            dt_value = dt_util.parse_datetime(str(value))

        if dt_value is None:
            _LOGGER.warning("Could not parse event_time value '%s'; sending as-is", value)
            return None

        if dt_value.tzinfo is None:
            dt_value = dt_util.default_time_zone().localize(dt_value)

        return dt_util.as_utc(dt_value).isoformat()
