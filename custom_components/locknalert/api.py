"""Client for the LocknAlert household status API.

Responses are validated strictly: anything that does not match the expected
shape exactly is rejected rather than partially trusted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
import json
from types import MappingProxyType

from aiohttp import ClientError, ClientSession, WSMsgType, WSServerHandshakeError
from homeassistant.util import dt as dt_util

from .const import (
    ALERT_KEYS,
    MAX_PERSON_NAME_LENGTH,
    MAX_RESPONSE_BYTES,
    PERSON_REF_PATTERN,
    PUSH_CLOSE_INSUFFICIENT_SCOPE,
    PUSH_CLOSE_TOKEN_INVALID,
    PUSH_HEARTBEAT,
    PUSH_URL,
    REQUEST_TIMEOUT,
    STATUS_URL,
)

type AccessTokenProvider = Callable[[], Awaitable[str]]
type StatusCallback = Callable[[HouseholdStatus], None]
type ConnectedCallback = Callable[[], None]


class LocknAlertApiError(Exception):
    """The API could not be reached or answered with an error."""


class LocknAlertAuthError(LocknAlertApiError):
    """The access token was refused: the integration must be linked again."""


class LocknAlertResponseError(LocknAlertApiError):
    """The API answered with something that is not a valid status."""


@dataclass(frozen=True, slots=True)
class HouseholdStatus:
    """Which kinds of alert are active for the person who linked this entry.

    Covers that person's own alerts plus the alerts the household's bridge
    raises itself; never another member's personal alerts.
    """

    serial: str
    # Pairwise pseudonymous id: stable for this person, household and integration
    person_ref: str
    # The person's first name, if they set one (they saw it on the consent page)
    person_name: str | None
    active_alert: bool
    open_alert_count: int
    alerts: Mapping[str, bool]
    # Excluded from equality so an unchanged status does not rewrite states
    generated_at: datetime = field(compare=False)


def _require_bool(payload: Mapping[str, object], key: str) -> bool:
    """Return a boolean field, or raise if it is missing or not a boolean.

    Args:
        payload: The object holding the field.
        key: The field name.

    Returns:
        The field's value.

    Raises:
        LocknAlertResponseError: The field is missing or not a JSON boolean.
    """
    value = payload.get(key)
    if not isinstance(value, bool):
        msg = f"Field {key!r} must be a boolean"
        raise LocknAlertResponseError(msg)
    return value


def parse_status(payload: object) -> HouseholdStatus:
    """Validate a decoded status reply.

    Args:
        payload: The decoded JSON body.

    Returns:
        The validated status.

    Raises:
        LocknAlertResponseError: The body does not match the expected shape.
    """
    if not isinstance(payload, dict):
        msg = "Status must be a JSON object"
        raise LocknAlertResponseError(msg)

    serial = payload.get("serial")
    if not isinstance(serial, str) or not serial:
        msg = "Field 'serial' must be a non-empty string"
        raise LocknAlertResponseError(msg)

    person = payload.get("person")
    if not isinstance(person, dict):
        msg = "Field 'person' must be an object"
        raise LocknAlertResponseError(msg)
    person_ref = person.get("ref")
    if not isinstance(person_ref, str) or not PERSON_REF_PATTERN.fullmatch(person_ref):
        msg = "Field 'person.ref' must be a 22-character URL-safe id"
        raise LocknAlertResponseError(msg)
    person_name = person.get("name")
    if person_name is not None and (
        not isinstance(person_name, str)
        or not person_name.strip()
        or len(person_name) > MAX_PERSON_NAME_LENGTH
    ):
        msg = "Field 'person.name' must be a short string or null"
        raise LocknAlertResponseError(msg)

    count = payload.get("open_alert_count")
    # bool is a subclass of int, so it is excluded explicitly
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        msg = "Field 'open_alert_count' must be a non-negative integer"
        raise LocknAlertResponseError(msg)

    raw_alerts = payload.get("alerts")
    if not isinstance(raw_alerts, dict):
        msg = "Field 'alerts' must be an object"
        raise LocknAlertResponseError(msg)
    alerts = {key: _require_bool(raw_alerts, key) for key in ALERT_KEYS}

    raw_generated = payload.get("generated_at")
    generated = (
        dt_util.parse_datetime(raw_generated)
        if isinstance(raw_generated, str)
        else None
    )
    if generated is None or generated.tzinfo is None:
        msg = "Field 'generated_at' must be a timezone-aware ISO 8601 datetime"
        raise LocknAlertResponseError(msg)

    return HouseholdStatus(
        serial=serial,
        person_ref=person_ref,
        person_name=person_name.strip() if person_name is not None else None,
        active_alert=_require_bool(payload, "active_alert"),
        open_alert_count=count,
        alerts=MappingProxyType(alerts),
        generated_at=generated,
    )


class LocknAlertApiClient:
    """Reads the linked household's alert status."""

    def __init__(
        self, session: ClientSession, access_token: AccessTokenProvider
    ) -> None:
        """Initialise the client.

        Args:
            session: Home Assistant's shared aiohttp session (TLS verified).
            access_token: Returns a currently valid access token, refreshing
                it first when needed.
        """
        self._session = session
        self._access_token = access_token

    async def async_get_status(self) -> HouseholdStatus:
        """Fetch the household's current alert status.

        Returns:
            The validated status.

        Raises:
            LocknAlertAuthError: The token was refused (401/403).
            LocknAlertApiError: Network failure, timeout or an unexpected reply.
        """
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            async with (
                asyncio.timeout(REQUEST_TIMEOUT),
                # Redirects are never followed: the token must not be re-sent
                # to wherever a redirect points
                self._session.get(
                    STATUS_URL, headers=headers, allow_redirects=False
                ) as response,
            ):
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    msg = f"Access token refused (HTTP {response.status})"
                    raise LocknAlertAuthError(msg)
                if response.status != HTTPStatus.OK:
                    msg = f"Unexpected HTTP {response.status} from LocknAlert"
                    raise LocknAlertApiError(msg)
                if response.content_type != "application/json":
                    msg = f"Unexpected content type {response.content_type!r}"
                    raise LocknAlertResponseError(msg)
                body = await response.content.read(MAX_RESPONSE_BYTES + 1)
        except (TimeoutError, ClientError) as err:
            msg = "Could not reach LocknAlert"
            raise LocknAlertApiError(msg) from err

        if len(body) > MAX_RESPONSE_BYTES:
            msg = "Status reply is too large"
            raise LocknAlertResponseError(msg)
        try:
            payload: object = json.loads(body)
        except ValueError as err:
            msg = "Status reply is not valid JSON"
            raise LocknAlertResponseError(msg) from err
        return parse_status(payload)

    async def async_stream_status(
        self, on_status: StatusCallback, on_connected: ConnectedCallback
    ) -> None:
        """Hold the push socket open, calling on_status for every status it sends.

        Returns only by raising, when the socket ends for any reason.

        Args:
            on_status: Called with each validated status (the first one is the
                current status, then one per change).
            on_connected: Called once the first status has arrived.

        Raises:
            LocknAlertAuthError: The token was refused (refresh it, or sign in again).
            LocknAlertResponseError: The API sent something that isn't a valid status.
            LocknAlertApiError: The connection failed or closed (reconnect later).
        """
        token = await self._access_token()
        connected = False
        try:
            async with self._session.ws_connect(
                PUSH_URL,
                headers={"Authorization": f"Bearer {token}"},
                heartbeat=PUSH_HEARTBEAT,
                max_msg_size=MAX_RESPONSE_BYTES,
            ) as socket:
                async for message in socket:
                    if message.type is not WSMsgType.TEXT:
                        # Pings are answered by aiohttp; anything else ends the stream
                        break
                    status = _parse_push(message.data)
                    on_status(status)
                    if not connected:
                        connected = True
                        on_connected()
                code = socket.close_code
        except WSServerHandshakeError as err:
            if err.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                msg = f"Push socket refused (HTTP {err.status})"
                raise LocknAlertAuthError(msg) from err
            msg = f"Push socket handshake failed (HTTP {err.status})"
            raise LocknAlertApiError(msg) from err
        except (TimeoutError, ClientError) as err:
            msg = "Push socket connection failed"
            raise LocknAlertApiError(msg) from err
        if code in (PUSH_CLOSE_TOKEN_INVALID, PUSH_CLOSE_INSUFFICIENT_SCOPE):
            msg = f"Push socket closed: token refused ({code})"
            raise LocknAlertAuthError(msg)
        msg = f"Push socket closed ({code})"
        raise LocknAlertApiError(msg)


def _parse_push(data: object) -> HouseholdStatus:
    """Validate one push message.

    Args:
        data: The text frame.

    Returns:
        The validated status.

    Raises:
        LocknAlertResponseError: The message is not a valid status message.
    """
    if not isinstance(data, str) or len(data) > MAX_RESPONSE_BYTES:
        msg = "Push message must be a short text frame"
        raise LocknAlertResponseError(msg)
    try:
        payload: object = json.loads(data)
    except ValueError as err:
        msg = "Push message is not valid JSON"
        raise LocknAlertResponseError(msg) from err
    if not isinstance(payload, dict) or payload.get("type") != "status":
        msg = "Push message is not a status"
        raise LocknAlertResponseError(msg)
    return parse_status(payload)
