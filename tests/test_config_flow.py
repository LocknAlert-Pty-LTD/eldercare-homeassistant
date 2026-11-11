"""Tests for the LocknAlert config flow (OAuth2 with PKCE, then household checks)."""

from __future__ import annotations

import base64
import hashlib
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.config_entry_oauth2_flow import MY_AUTH_CALLBACK_PATH
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from yarl import URL

from custom_components.locknalert.const import (
    CONF_PERSON_NAME,
    CONF_PERSON_REF,
    CONF_SERIAL,
    DOMAIN,
    OAUTH2_AUTHORIZE_URL,
    OAUTH2_CLIENT_ID,
    OAUTH2_REVOKE_URL,
    OAUTH2_SCOPE,
    OAUTH2_TOKEN_URL,
    STATUS_URL,
    entry_unique_id,
)

from .conftest import (
    JSON_HEADERS,
    PERSON_NAME,
    PERSON_REF,
    REFRESH_TOKEN,
    SERIAL,
    status_payload,
    token_payload,
)

NEW_ACCESS: str = "lna_at_new"
NEW_REFRESH: str = "lna_rt_new"
# Another household member's pairwise id
OTHER_REF: str = "Sam0000000000000000000"


@pytest.fixture(autouse=True)
def my_home_assistant(hass: HomeAssistant) -> None:
    """Pretend My Home Assistant is loaded (it sets the OAuth redirect).

    Args:
        hass: The Home Assistant instance.
    """
    hass.config.components.add("my")


async def _start(hass: HomeAssistant, method: str = "google") -> dict[str, Any]:
    """Start a flow and choose a sign-in method.

    Args:
        hass: The Home Assistant instance.
        method: The menu option.

    Returns:
        The serial form result.
    """
    assert await async_setup_component(hass, DOMAIN, {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["google", "locknalert_account"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": method}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "serial"
    return dict(result)


async def _to_browser(
    hass: HomeAssistant, method: str = "google", serial: str = " lna-00042 "
) -> tuple[str, URL]:
    """Go through the menu and serial form to the browser sign-in.

    Args:
        hass: The Home Assistant instance.
        method: The menu option.
        serial: The serial typed into the form.

    Returns:
        The flow id and the authorize URL.
    """
    result = await _start(hass, method)
    result = dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SERIAL: serial}
        )
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    return str(result["flow_id"]), URL(str(result["url"]))


async def _callback(
    hass_client_no_auth: ClientSessionGenerator, url: URL, **params: str
) -> None:
    """Simulate My Home Assistant delivering the authorize response.

    Args:
        hass_client_no_auth: An HTTP client for Home Assistant.
        url: The authorize URL (for its state).
        **params: code=... or error=...
    """
    client = await hass_client_no_auth()
    response = await client.get(
        "/auth/external/callback", params={**params, "state": url.query["state"]}
    )
    assert response.status == HTTPStatus.OK


def _calls_to(aioclient_mock: AiohttpClientMocker, target: str) -> list[Any]:
    """Return the request bodies sent to one URL.

    Args:
        aioclient_mock: The HTTP mock.
        target: The URL.

    Returns:
        The data of each call.
    """
    return [data for _, url, data, _ in aioclient_mock.mock_calls if str(url) == target]


@pytest.mark.usefixtures("current_request_with_host")
async def test_full_flow_with_pkce(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Google sign-in for a household, proven with a per-flow PKCE verifier.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
        aioclient_mock: The HTTP mock.
    """
    flow_id, url = await _to_browser(hass)
    # The authorize request names the household, the method, PKCE and the scope
    assert str(url.with_query(None)) == OAUTH2_AUTHORIZE_URL
    assert url.query["client_id"] == OAUTH2_CLIENT_ID
    assert url.query["redirect_uri"] == MY_AUTH_CALLBACK_PATH
    assert url.query["response_type"] == "code"
    assert url.query["code_challenge_method"] == "S256"
    assert url.query["scope"] == OAUTH2_SCOPE
    assert url.query["bridge_serial"] == SERIAL
    assert url.query["method"] == "google"

    await _callback(hass_client_no_auth, url, code="lna_ac_code")
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=token_payload(NEW_ACCESS, NEW_REFRESH))
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    with patch("custom_components.locknalert.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(flow_id)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{PERSON_NAME} ({SERIAL})"
    assert result["result"].unique_id == entry_unique_id(SERIAL, PERSON_REF)
    assert result["data"][CONF_SERIAL] == SERIAL
    assert result["data"][CONF_PERSON_REF] == PERSON_REF
    assert result["data"][CONF_PERSON_NAME] == PERSON_NAME
    assert result["data"]["token"]["refresh_token"] == NEW_REFRESH

    # The exchange proved possession of the verifier behind the challenge
    (exchange,) = _calls_to(aioclient_mock, OAUTH2_TOKEN_URL)
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["client_id"] == OAUTH2_CLIENT_ID
    assert "client_secret" not in exchange
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(exchange["code_verifier"].encode()).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert challenge == url.query["code_challenge"]


@pytest.mark.usefixtures("current_request_with_host")
async def test_each_flow_gets_its_own_verifier(hass: HomeAssistant) -> None:
    """Two flows never share a PKCE challenge.

    Args:
        hass: The Home Assistant instance.
    """
    _, first = await _to_browser(hass)
    # Another household (the same one would abort: a flow is already in progress)
    _, second = await _to_browser(hass, "locknalert_account", "LNA-00043")
    assert first.query["code_challenge"] != second.query["code_challenge"]
    assert second.query["method"] == "password"


async def test_invalid_serial_is_rejected(hass: HomeAssistant) -> None:
    """A serial with characters a bridge serial can't have is refused.

    Args:
        hass: The Home Assistant instance.
    """
    result = await _start(hass)
    result = dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SERIAL: "LNA 42; DROP"}
        )
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SERIAL: "invalid_serial"}


async def _complete(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    status: dict[str, Any],
) -> dict[str, Any]:
    """Run a new flow through sign-in with the given status reply.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
        aioclient_mock: The HTTP mock.
        status: The status the new token reads.

    Returns:
        The final flow result.
    """
    flow_id, url = await _to_browser(hass)
    await _callback(hass_client_no_auth, url, code="lna_ac_code")
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=token_payload(NEW_ACCESS, NEW_REFRESH))
    aioclient_mock.get(STATUS_URL, json=status, headers=JSON_HEADERS)
    aioclient_mock.post(OAUTH2_REVOKE_URL, json={})
    with patch("custom_components.locknalert.async_setup_entry", return_value=True):
        return dict(await hass.config_entries.flow.async_configure(flow_id))


@pytest.mark.usefixtures("current_request_with_host")
async def test_same_person_twice_aborts_and_revokes(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Linking the same person to the same household again is refused.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The person's existing entry.
    """
    mock_config_entry.add_to_hass(hass)
    result = await _complete(
        hass, hass_client_no_auth, aioclient_mock, status_payload()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert _calls_to(aioclient_mock, OAUTH2_REVOKE_URL) == [
        {"client_id": OAUTH2_CLIENT_ID, "token": NEW_REFRESH}
    ]


@pytest.mark.usefixtures("current_request_with_host")
async def test_another_person_in_the_household_gets_their_own_entry(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A second member of the same household links separately, as their own entry.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The first person's entry.
    """
    mock_config_entry.add_to_hass(hass)
    result = await _complete(
        hass,
        hass_client_no_auth,
        aioclient_mock,
        status_payload(person_ref=OTHER_REF, person_name="Sam"),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Sam ({SERIAL})"
    assert result["result"].unique_id == entry_unique_id(SERIAL, OTHER_REF)
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    assert not _calls_to(aioclient_mock, OAUTH2_REVOKE_URL)


@pytest.mark.usefixtures("current_request_with_host")
async def test_person_without_a_first_name_gets_a_distinct_title(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Without a first name, the title uses a short part of the pseudonymous id.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
        aioclient_mock: The HTTP mock.
    """
    result = await _complete(
        hass, hass_client_no_auth, aioclient_mock, status_payload(person_name=None)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"LocknAlert {SERIAL} ({PERSON_REF[:4]})"
    assert result["data"][CONF_PERSON_NAME] is None


async def test_requires_my_home_assistant(hass: HomeAssistant) -> None:
    """Without My Home Assistant the redirect LocknAlert accepts isn't available.

    Args:
        hass: The Home Assistant instance.
    """
    hass.config.components.remove("my")
    assert await async_setup_component(hass, DOMAIN, {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "my_home_assistant_required"


@pytest.mark.usefixtures("current_request_with_host")
async def test_cancelled_consent_aborts(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Pressing Cancel on the consent page ends the flow.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
    """
    flow_id, url = await _to_browser(hass)
    await _callback(hass_client_no_auth, url, error="access_denied")
    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "user_rejected_authorize"


@pytest.mark.parametrize(
    ("status_kwargs", "reason"),
    [
        (
            {"json": status_payload("LNA-99999"), "headers": JSON_HEADERS},
            "serial_mismatch",
        ),
        ({"status": HTTPStatus.UNAUTHORIZED}, "oauth_unauthorized"),
        ({"status": HTTPStatus.BAD_GATEWAY}, "cannot_connect"),
    ],
)
@pytest.mark.usefixtures("current_request_with_host")
async def test_unusable_token_is_revoked(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    status_kwargs: dict[str, Any],
    reason: str,
) -> None:
    """A token that can't read the entered household aborts and is revoked.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
        aioclient_mock: The HTTP mock.
        status_kwargs: How the status check answers.
        reason: The expected abort reason.
    """
    flow_id, url = await _to_browser(hass)
    await _callback(hass_client_no_auth, url, code="lna_ac_code")
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=token_payload(NEW_ACCESS, NEW_REFRESH))
    aioclient_mock.get(STATUS_URL, **status_kwargs)
    aioclient_mock.post(OAUTH2_REVOKE_URL, json={})
    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert _calls_to(aioclient_mock, OAUTH2_REVOKE_URL) == [
        {"client_id": OAUTH2_CLIENT_ID, "token": NEW_REFRESH}
    ]
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.usefixtures("current_request_with_host")
async def test_refused_code_exchange_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A refused code exchange (e.g. a replayed code) ends the flow.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
        aioclient_mock: The HTTP mock.
    """
    flow_id, url = await _to_browser(hass)
    await _callback(hass_client_no_auth, url, code="lna_ac_code")
    aioclient_mock.post(
        OAUTH2_TOKEN_URL,
        status=HTTPStatus.BAD_REQUEST,
        json={"error": "invalid_grant"},
    )
    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "oauth_unauthorized"


@pytest.mark.usefixtures("current_request_with_host")
async def test_reauth_replaces_and_revokes_the_old_grant(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Signing in again keeps the household, stores the new token, revokes the old.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The existing entry.
    """
    mock_config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    result: dict[str, Any] = dict(await mock_config_entry.start_reauth_flow(hass))
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["serial"] == SERIAL
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))
    assert result["type"] is FlowResultType.MENU
    result = dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "locknalert_account"}
        )
    )
    # No serial form: the household is the entry's own
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    url = URL(str(result["url"]))
    assert url.query["bridge_serial"] == SERIAL

    await _callback(hass_client_no_auth, url, code="lna_ac_code")
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=token_payload(NEW_ACCESS, NEW_REFRESH))
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    aioclient_mock.post(OAUTH2_REVOKE_URL, json={})
    with patch("custom_components.locknalert.async_setup_entry", return_value=True):
        result = dict(await hass.config_entries.flow.async_configure(result["flow_id"]))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data["token"]["refresh_token"] == NEW_REFRESH
    assert _calls_to(aioclient_mock, OAUTH2_REVOKE_URL) == [
        {"client_id": OAUTH2_CLIENT_ID, "token": REFRESH_TOKEN}
    ]
    assert mock_config_entry.state is ConfigEntryState.LOADED


@pytest.mark.usefixtures("current_request_with_host")
async def test_reauth_as_another_person_is_refused(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An entry holds one person's alerts: someone else can't take it over.

    Args:
        hass: The Home Assistant instance.
        hass_client_no_auth: An HTTP client for Home Assistant.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The existing entry.
    """
    mock_config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    result: dict[str, Any] = dict(await mock_config_entry.start_reauth_flow(hass))
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))
    result = dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "google"}
        )
    )
    url = URL(str(result["url"]))
    await _callback(hass_client_no_auth, url, code="lna_ac_code")
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=token_payload(NEW_ACCESS, NEW_REFRESH))
    aioclient_mock.get(
        STATUS_URL,
        json=status_payload(person_ref=OTHER_REF, person_name="Sam"),
        headers=JSON_HEADERS,
    )
    aioclient_mock.post(OAUTH2_REVOKE_URL, json={})
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"]))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    # The entry keeps the original person's token; the new one is revoked
    assert mock_config_entry.data["token"]["refresh_token"] == REFRESH_TOKEN
    assert _calls_to(aioclient_mock, OAUTH2_REVOKE_URL) == [
        {"client_id": OAUTH2_CLIENT_ID, "token": NEW_REFRESH}
    ]
