"""Shared fixtures for the LocknAlert tests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import time
from typing import Any, Final
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.locknalert.const import (
    ALERT_KEYS,
    CONF_PERSON_NAME,
    CONF_PERSON_REF,
    CONF_SERIAL,
    DOMAIN,
    OAUTH2_SCOPE,
    entry_unique_id,
)

SERIAL: Final = "LNA-00042"
JSON_HEADERS: Final = {"Content-Type": "application/json"}
ACCESS_TOKEN: Final = "lna_at_current"
REFRESH_TOKEN: Final = "lna_rt_current"
# The pairwise person id the API returns for the test account
PERSON_REF: Final = "q3J7mXo2Vb9tRk1LsYd8Fw"
PERSON_NAME: Final = "Mary"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components in every test.

    Args:
        enable_custom_integrations: The plugin fixture that allows it.
    """
    del enable_custom_integrations


async def _socket_never_connects(*_args: object, **_kwargs: object) -> None:
    """Stand in for the push socket: waits until the entry unloads.

    Args:
        *_args: Ignored.
        **_kwargs: Ignored.
    """
    await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def no_real_push_socket(request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep the push socket from connecting, except in tests marked real_push.

    Args:
        request: The test's request, to read its markers.

    Yields:
        None: While the test runs.
    """
    if request.node.get_closest_marker("real_push") is not None:
        yield
        return
    with patch(
        "custom_components.locknalert.api.LocknAlertApiClient.async_stream_status",
        _socket_never_connects,
    ):
        yield


def status_payload(
    serial: str = SERIAL,
    person_ref: str = PERSON_REF,
    person_name: str | None = PERSON_NAME,
    **alerts: bool,
) -> dict[str, Any]:
    """Build a status reply as the API sends it.

    Args:
        serial: The household's bridge serial.
        person_ref: The person's pairwise id.
        person_name: The person's first name, if any.
        **alerts: Alert flags to switch on.

    Returns:
        The decoded JSON body.
    """
    flags = dict.fromkeys(ALERT_KEYS, False)
    flags.update(alerts)
    return {
        "serial": serial,
        "person": {"ref": person_ref, "name": person_name},
        "active_alert": any(flags.values()),
        "open_alert_count": sum(flags.values()),
        "alerts": flags,
        "generated_at": "2026-11-11T08:00:00+00:00",
    }


def token_payload(
    access_token: str = ACCESS_TOKEN, refresh_token: str = REFRESH_TOKEN
) -> dict[str, Any]:
    """Build a token reply as the API sends it.

    Args:
        access_token: The access token.
        refresh_token: The refresh token.

    Returns:
        The decoded JSON body.
    """
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": refresh_token,
        "scope": OAUTH2_SCOPE,
    }


@pytest.fixture
def expires_at() -> float:
    """When the stored access token expires (an hour from now).

    Returns:
        A Unix timestamp.
    """
    return time.time() + 3600


@pytest.fixture
def mock_config_entry(expires_at: float) -> MockConfigEntry:
    """A linked household's config entry.

    Args:
        expires_at: When its access token expires.

    Returns:
        The entry (not yet added to Home Assistant).
    """
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=entry_unique_id(SERIAL, PERSON_REF),
        title=f"{PERSON_NAME} ({SERIAL})",
        data={
            "auth_implementation": DOMAIN,
            "token": {**token_payload(), "expires_at": expires_at},
            CONF_SERIAL: SERIAL,
            CONF_PERSON_REF: PERSON_REF,
            CONF_PERSON_NAME: PERSON_NAME,
        },
    )


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add an entry and set it up.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.
    """
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
