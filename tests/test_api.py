"""Tests for the status client's strict validation."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.locknalert.api import (
    LocknAlertApiClient,
    LocknAlertApiError,
    LocknAlertAuthError,
    LocknAlertResponseError,
    parse_status,
)
from custom_components.locknalert.const import MAX_RESPONSE_BYTES, STATUS_URL

from .conftest import ACCESS_TOKEN, JSON_HEADERS, PERSON_REF, SERIAL, status_payload


async def _token() -> str:
    """Return the test access token.

    Returns:
        The token.
    """
    return ACCESS_TOKEN


def _client(hass: HomeAssistant) -> LocknAlertApiClient:
    """Build a client on Home Assistant's (mocked) session.

    Args:
        hass: The Home Assistant instance.

    Returns:
        The client.
    """
    return LocknAlertApiClient(async_get_clientsession(hass), _token)


def test_parse_valid_status() -> None:
    """A well-formed reply parses into flags."""
    status = parse_status(status_payload(heart_rate=True, vitals=True))
    assert status.serial == SERIAL
    assert status.person_ref == PERSON_REF
    assert status.person_name == "Mary"
    assert status.active_alert is True
    assert status.open_alert_count == 2
    assert status.alerts["heart_rate"] is True
    assert status.alerts["fall"] is False
    assert status.generated_at.tzinfo is not None


def test_status_equality_ignores_generation_time() -> None:
    """Two replies with the same flags compare equal (no needless state writes)."""
    first = parse_status(status_payload(fall=True))
    later = parse_status(
        {**status_payload(fall=True), "generated_at": "2026-11-11T09:00:00+00:00"}
    )
    assert first == later


def _broken(**changes: object) -> dict[str, Any]:
    """Return a valid payload with some fields replaced (None removes a field).

    Args:
        **changes: Field replacements.

    Returns:
        The modified payload.
    """
    payload = status_payload()
    for key, value in changes.items():
        if value is None:
            payload.pop(key)
        else:
            payload[key] = value
    return payload


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "status",
        _broken(serial=None),
        _broken(serial=""),
        _broken(serial=42),
        _broken(person=None),
        _broken(person="Mary"),
        _broken(person={"ref": "short", "name": None}),
        _broken(person={"ref": "q3J7mXo2Vb9tRk1LsYd8F/", "name": None}),
        _broken(person={"ref": "q3J7mXo2Vb9tRk1LsYd8Fw", "name": 7}),
        _broken(person={"ref": "q3J7mXo2Vb9tRk1LsYd8Fw", "name": "  "}),
        _broken(person={"ref": "q3J7mXo2Vb9tRk1LsYd8Fw", "name": "x" * 65}),
        _broken(active_alert="yes"),
        _broken(active_alert=1),
        _broken(open_alert_count=True),
        _broken(open_alert_count=-1),
        _broken(open_alert_count="2"),
        _broken(alerts=None),
        _broken(alerts=[]),
        _broken(alerts={"fall": False}),
        _broken(alerts={**status_payload()["alerts"], "spo2": "false"}),
        _broken(generated_at=None),
        _broken(generated_at="yesterday"),
        _broken(generated_at="2026-11-11T08:00:00"),
    ],
)
def test_parse_rejects_malformed_status(payload: object) -> None:
    """Anything that isn't exactly the expected shape is refused.

    Args:
        payload: A malformed body.
    """
    with pytest.raises(LocknAlertResponseError):
        parse_status(payload)


async def test_client_sends_bearer_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The token goes only in the Authorization header of the status request.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
    """
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    status = await _client(hass).async_get_status()
    assert status.serial == SERIAL
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == STATUS_URL
    assert headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"


@pytest.mark.parametrize("status", [HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN])
async def test_client_maps_refused_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: HTTPStatus
) -> None:
    """401 and 403 mean the integration must sign in again.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        status: The refusal status.
    """
    aioclient_mock.get(STATUS_URL, status=status)
    with pytest.raises(LocknAlertAuthError):
        await _client(hass).async_get_status()


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": HTTPStatus.FOUND}, LocknAlertApiError),
        ({"status": HTTPStatus.TOO_MANY_REQUESTS}, LocknAlertApiError),
        ({"status": HTTPStatus.BAD_GATEWAY}, LocknAlertApiError),
        ({"exc": ClientError()}, LocknAlertApiError),
        ({"exc": TimeoutError()}, LocknAlertApiError),
        (
            {"text": "<html>", "headers": {"Content-Type": "text/html"}},
            LocknAlertResponseError,
        ),
        ({"text": "{not json", "headers": JSON_HEADERS}, LocknAlertResponseError),
        (
            {"text": " " * (MAX_RESPONSE_BYTES + 1), "headers": JSON_HEADERS},
            LocknAlertResponseError,
        ),
    ],
)
async def test_client_refuses_bad_replies(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict[str, Any],
    error: type[Exception],
) -> None:
    """Redirects, errors, wrong content types, bad JSON and huge bodies are refused.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        kwargs: How the mocked reply misbehaves.
        error: The expected exception.
    """
    aioclient_mock.get(STATUS_URL, **kwargs)
    with pytest.raises(error):
        await _client(hass).async_get_status()
