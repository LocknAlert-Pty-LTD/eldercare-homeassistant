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
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client, config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_EVENT_TIME,
    ATTR_MESSAGE,
    ATTR_ROOM_NAME,
    ATTR_SERIAL,
    ATTR_SERIAL_NUMBER,
    ATTR_TITLE,
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
    FALL_ENDPOINT,
    SERVICE_TRIGGER_FALL,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: Mapping[str, Any]) -> bool:
    """Set up the LocknAlert integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LocknAlert from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    _register_services(hass)

    entry_data = _resolve_entry_data(entry)
    client = _LocknAlertClient(
        hass,
        base_url=entry_data[CONF_BASE_URL],
        api_key=entry_data.get(CONF_API_KEY),
        serial_number=entry_data.get(CONF_SERIAL_NUMBER),
        default_title=entry_data.get(CONF_DEFAULT_TITLE),
        default_message=entry_data.get(CONF_DEFAULT_MESSAGE),
        default_room_name=entry_data.get(CONF_DEFAULT_ROOM_NAME),
        timeout=entry_data[CONF_TIMEOUT],
    )

    hass.data[DOMAIN][entry.entry_id] = client

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry when its data changes."""
    await hass.config_entries.async_reload(entry.entry_id)


def _resolve_entry_data(entry: ConfigEntry) -> dict[str, Any]:
    """Merge config entry data and options into a single mapping."""
    data = {**entry.data, **entry.options}
    return _normalize_entry_data(data)


def _normalize_entry_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize entry data into canonical keys."""
    normalized: dict[str, Any] = dict(data)

    base_url = normalized.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    normalized[CONF_BASE_URL] = str(base_url).rstrip("/")

    timeout = normalized.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
    normalized[CONF_TIMEOUT] = int(timeout)

    serial = normalized.get(CONF_SERIAL_NUMBER) or normalized.get(CONF_DEFAULT_SERIAL)
    if serial:
        normalized[CONF_SERIAL_NUMBER] = str(serial)
    normalized.pop(CONF_DEFAULT_SERIAL, None)

    return normalized


def _register_services(hass: HomeAssistant) -> None:
    """Register integration services if they are not already registered."""
    if hass.services.has_service(DOMAIN, SERVICE_TRIGGER_FALL):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_FALL,
        _build_trigger_service_handler(hass),
        schema=_fall_service_schema(),
    )


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
        clients = list(hass.data.get(DOMAIN, {}).values())

        if not clients:
            raise HomeAssistantError(
                "LocknAlert is not configured. Add LocknAlert via the integrations UI."
            )

        tasks = [client.async_trigger_fall_alert(call.data) for client in clients]
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
        serial_number: str | None,
        default_title: str | None,
        default_message: str | None,
        default_room_name: str | None,
        timeout: int,
    ) -> None:
        self._session = aiohttp_client.async_get_clientsession(hass)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._serial_number = serial_number
        self._default_title = default_title
        self._default_message = default_message
        self._default_room_name = default_room_name
        self._timeout = timeout

    async def async_trigger_fall_alert(self, data: Mapping[str, Any]) -> None:
        """Send a fall alert payload to the LocknAlert server."""
        payload: dict[str, Any] = dict(data)

        serial = (
            payload.get(ATTR_SERIAL)
            or payload.get(ATTR_SERIAL_NUMBER)
            or self._serial_number
        )
        if not serial:
            raise HomeAssistantError(
                "Call must include 'serial' or 'serial_number', or configure a serial number in the integration options."
            )

        if ATTR_SERIAL not in payload:
            payload[ATTR_SERIAL] = serial
        if ATTR_SERIAL_NUMBER not in payload:
            payload[ATTR_SERIAL_NUMBER] = serial

        if self._default_title and ATTR_TITLE not in payload:
            payload[ATTR_TITLE] = self._default_title
        if self._default_message and ATTR_MESSAGE not in payload:
            payload[ATTR_MESSAGE] = self._default_message
        if self._default_room_name and ATTR_ROOM_NAME not in payload:
            payload[ATTR_ROOM_NAME] = self._default_room_name

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
            tz = dt_util.default_time_zone()
            if hasattr(tz, "localize"):
                dt_value = tz.localize(dt_value)
            else:
                dt_value = dt_value.replace(tzinfo=tz)

        return dt_util.as_utc(dt_value).isoformat()
