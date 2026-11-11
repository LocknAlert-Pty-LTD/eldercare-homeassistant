"""Tests for setup, polling, token refresh, reauth and removal."""

from __future__ import annotations

from http import HTTPStatus
import time
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.locknalert.const import (
    ALERT_KEYS,
    DOMAIN,
    OAUTH2_CLIENT_ID,
    OAUTH2_REVOKE_URL,
    OAUTH2_TOKEN_URL,
    STATUS_URL,
)

from .conftest import (
    ACCESS_TOKEN,
    JSON_HEADERS,
    PERSON_NAME,
    PERSON_REF,
    REFRESH_TOKEN,
    SERIAL,
    setup_integration,
    status_payload,
    token_payload,
)

ACTIVE_ALERT = "binary_sensor.mary_lna_00042_active_alert"
HEART_RATE = "binary_sensor.mary_lna_00042_heart_rate_alert"
FALL = "binary_sensor.mary_lna_00042_fall_alert"
WATCH_OFF = "binary_sensor.mary_lna_00042_watch_off"
OPEN_ALERTS = "sensor.mary_lna_00042_open_alerts"


def _reauth_started(hass: HomeAssistant) -> bool:
    """Return whether a reauth flow is in progress for the integration.

    Args:
        hass: The Home Assistant instance.

    Returns:
        True when one is.
    """
    return any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )


async def test_setup_creates_every_alert_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """One binary sensor per alert kind plus 'any alert', and an open-alerts count.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry.
        entity_registry: The entity registry.
    """
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    entries = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    assert {entry.unique_id for entry in entries} == {
        f"{SERIAL}_{PERSON_REF}_{key}"
        for key in ("active_alert", "open_alert_count", *ALERT_KEYS)
    }
    assert hass.states.get(ACTIVE_ALERT).state == STATE_OFF  # type: ignore[union-attr]
    assert hass.states.get(ACTIVE_ALERT).attributes["device_class"] == "safety"  # type: ignore[union-attr]
    assert hass.states.get(WATCH_OFF).attributes["device_class"] == "problem"  # type: ignore[union-attr]
    assert hass.states.get(OPEN_ALERTS).state == "0"  # type: ignore[union-attr]


async def test_alert_flags_drive_sensor_states(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A new poll switches on exactly the reported alerts.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry.
    """
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    await setup_integration(hass, mock_config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        STATUS_URL,
        json=status_payload(vitals=True, heart_rate=True),
        headers=JSON_HEADERS,
    )
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(ACTIVE_ALERT).state == STATE_ON  # type: ignore[union-attr]
    assert hass.states.get(HEART_RATE).state == STATE_ON  # type: ignore[union-attr]
    assert hass.states.get(FALL).state == STATE_OFF  # type: ignore[union-attr]
    assert hass.states.get(OPEN_ALERTS).state == "2"  # type: ignore[union-attr]


async def test_poll_failure_marks_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A failed poll makes the sensors unavailable instead of showing stale safety.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry.
    """
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    await setup_integration(hass, mock_config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(STATUS_URL, status=HTTPStatus.BAD_GATEWAY)
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(ACTIVE_ALERT).state == STATE_UNAVAILABLE  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("kwargs", "state"),
    [
        ({"status": HTTPStatus.BAD_GATEWAY}, ConfigEntryState.SETUP_RETRY),
        (
            {"json": status_payload("LNA-99999"), "headers": JSON_HEADERS},
            ConfigEntryState.SETUP_RETRY,
        ),
        (
            {
                "json": status_payload(person_ref="Zz9" + "a" * 19),
                "headers": JSON_HEADERS,
            },
            ConfigEntryState.SETUP_RETRY,
        ),
        ({"status": HTTPStatus.UNAUTHORIZED}, ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_setup_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    kwargs: dict[str, object],
    state: ConfigEntryState,
) -> None:
    """Temporary problems retry; a refused token starts reauth.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry.
        kwargs: How the status endpoint answers.
        state: The expected entry state.
    """
    aioclient_mock.get(STATUS_URL, **kwargs)
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is state
    assert _reauth_started(hass) is (state is ConfigEntryState.SETUP_ERROR)


@pytest.mark.parametrize("expires_at", [time.time() - 60])
async def test_expired_token_is_refreshed_and_stored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An expired access token is rotated before the status is read.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry (with an expired token).
    """
    aioclient_mock.post(
        OAUTH2_TOKEN_URL, json=token_payload("lna_at_rotated", "lna_rt_rotated")
    )
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    (refresh,) = [
        data
        for _, url, data, _ in aioclient_mock.mock_calls
        if str(url) == OAUTH2_TOKEN_URL
    ]
    assert refresh == {
        "grant_type": "refresh_token",
        "client_id": OAUTH2_CLIENT_ID,
        "refresh_token": REFRESH_TOKEN,
    }
    token = mock_config_entry.data["token"]
    assert token["access_token"] == "lna_at_rotated"
    assert token["refresh_token"] == "lna_rt_rotated"
    status_call = next(
        headers
        for _, url, _, headers in aioclient_mock.mock_calls
        if str(url) == STATUS_URL
    )
    assert status_call["Authorization"] == "Bearer lna_at_rotated"


@pytest.mark.parametrize("expires_at", [time.time() - 60])
async def test_refused_refresh_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A refused refresh (revoked or replayed grant) asks the person to sign in again.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry (with an expired token).
    """
    aioclient_mock.post(
        OAUTH2_TOKEN_URL,
        status=HTTPStatus.BAD_REQUEST,
        json={"error": "invalid_grant"},
    )
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert _reauth_started(hass)


async def test_unload_and_remove_revokes_the_grant(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Removing the integration revokes its grant at LocknAlert.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry.
    """
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    aioclient_mock.post(OAUTH2_REVOKE_URL, status=HTTPStatus.SERVICE_UNAVAILABLE)
    await setup_integration(hass, mock_config_entry)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    revocations = [
        data
        for _, url, data, _ in aioclient_mock.mock_calls
        if str(url) == OAUTH2_REVOKE_URL
    ]
    assert revocations == [{"client_id": OAUTH2_CLIENT_ID, "token": REFRESH_TOKEN}]


async def test_version_1_entries_must_be_added_again(hass: HomeAssistant) -> None:
    """Old API-key entries can't become household grants: migration fails clearly.

    Args:
        hass: The Home Assistant instance.
    """
    old = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={"base_url": "https://api.locknalert.co.za", "api_key": "secret"},
    )
    old.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    assert old.state is ConfigEntryState.MIGRATION_ERROR


async def test_diagnostics_never_contain_tokens(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Diagnostics show the flags but redact every token.

    Args:
        hass: The Home Assistant instance.
        hass_client: An authenticated HTTP client.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry.
    """
    aioclient_mock.get(STATUS_URL, json=status_payload(fall=True), headers=JSON_HEADERS)
    await setup_integration(hass, mock_config_entry)
    assert await async_setup_component(hass, "diagnostics", {})

    result: dict[str, Any] = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )
    text = repr(result)
    assert ACCESS_TOKEN not in text
    assert REFRESH_TOKEN not in text
    assert result["entry"]["data"]["token"] == "**REDACTED**"
    # The person is never identified in diagnostics
    assert PERSON_NAME not in text
    assert PERSON_REF not in text
    assert result["status"]["alerts"]["fall"] is True
    assert result["status"]["serial"] == SERIAL
