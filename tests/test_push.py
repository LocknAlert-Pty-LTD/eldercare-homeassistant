"""Tests for real-time push: the socket client and the coordinator's push loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import json
from types import TracebackType
from typing import Any, Self, cast
from unittest.mock import MagicMock, patch

from aiohttp import (
    ClientConnectionError,
    ClientSession,
    WSMessage,
    WSMsgType,
    WSServerHandshakeError,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.locknalert.api import (
    HouseholdStatus,
    LocknAlertApiClient,
    LocknAlertApiError,
    LocknAlertAuthError,
    LocknAlertResponseError,
    parse_status,
)
from custom_components.locknalert.const import (
    PUSH_CLOSE_TOKEN_INVALID,
    PUSH_FALLBACK_INTERVAL,
    PUSH_URL,
    STATUS_URL,
    UPDATE_INTERVAL,
)
from custom_components.locknalert.coordinator import LocknAlertCoordinator

from .conftest import (
    ACCESS_TOKEN,
    JSON_HEADERS,
    SERIAL,
    setup_integration,
    status_payload,
)

FALL = "binary_sensor.mary_lna_00042_fall_alert"

# These tests exercise the real socket client (or script it themselves)
pytestmark = pytest.mark.real_push


def _frame(payload: dict[str, Any]) -> WSMessage:
    """Build a text frame carrying a status message.

    Args:
        payload: The status body.

    Returns:
        The frame.
    """
    return WSMessage(WSMsgType.TEXT, json.dumps({"type": "status", **payload}), None)


class FakeSocket:
    """A push socket that delivers fixed frames, then closes with a code."""

    def __init__(self, frames: list[WSMessage], close_code: int | None) -> None:
        """Initialise the fake.

        Args:
            frames: Frames to deliver, in order.
            close_code: The close code once they run out.
        """
        self._frames = list(frames)
        self.close_code = close_code

    async def __aenter__(self) -> Self:
        """Open the socket.

        Returns:
            The socket.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the socket.

        Args:
            exc_type: Unused.
            exc: Unused.
            tb: Unused.
        """

    def __aiter__(self) -> Self:
        """Iterate over the frames.

        Returns:
            The socket.
        """
        return self

    async def __anext__(self) -> WSMessage:
        """Deliver the next frame.

        Returns:
            The frame.

        Raises:
            StopAsyncIteration: When the frames run out (the socket closed).
        """
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


class FakeSession:
    """Records the socket request and returns the fake socket, or raises."""

    def __init__(
        self, socket: FakeSocket | None = None, error: Exception | None = None
    ) -> None:
        """Initialise the fake.

        Args:
            socket: What ws_connect returns.
            error: What ws_connect raises instead.
        """
        self._socket = socket
        self._error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def ws_connect(self, url: str, **kwargs: Any) -> FakeSocket:  # noqa: ANN401 - mirrors aiohttp
        """Open the (fake) socket.

        Args:
            url: The socket URL.
            **kwargs: The connection options.

        Returns:
            The fake socket.

        Raises:
            Exception: The configured error.
        """
        self.calls.append((url, kwargs))
        if self._error is not None:
            raise self._error
        assert self._socket is not None
        return self._socket


async def _token() -> str:
    """Return the test access token.

    Returns:
        The token.
    """
    return ACCESS_TOKEN


def _client(session: FakeSession) -> LocknAlertApiClient:
    """Build a client on a fake session.

    Args:
        session: The fake session.

    Returns:
        The client.
    """
    return LocknAlertApiClient(cast("ClientSession", session), _token)


async def test_stream_delivers_statuses_then_reports_a_refused_token() -> None:
    """Each frame becomes a status; a 4401 close means the token was refused."""
    session = FakeSession(
        FakeSocket(
            [_frame(status_payload()), _frame(status_payload(fall=True))],
            PUSH_CLOSE_TOKEN_INVALID,
        )
    )
    received: list[HouseholdStatus] = []
    connected: list[bool] = []
    with pytest.raises(LocknAlertAuthError):
        await _client(session).async_stream_status(
            received.append, lambda: connected.append(True)
        )
    assert [status.alerts["fall"] for status in received] == [False, True]
    # Told once that the stream is live, after the first status
    assert connected == [True]
    # The token goes in the header of a request to the fixed push URL
    url, options = session.calls[0]
    assert url == PUSH_URL
    assert options["headers"] == {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    assert options["heartbeat"] > 0
    assert options["max_msg_size"] > 0


@pytest.mark.parametrize(
    ("socket", "error", "expected"),
    [
        (FakeSocket([], 1000), None, LocknAlertApiError),
        (
            FakeSocket([WSMessage(WSMsgType.TEXT, '{"type": "hello"}', None)], 1000),
            None,
            LocknAlertResponseError,
        ),
        (
            FakeSocket([WSMessage(WSMsgType.TEXT, "{not json", None)], 1000),
            None,
            LocknAlertResponseError,
        ),
        (
            FakeSocket([WSMessage(WSMsgType.BINARY, b"\x00", None)], 1000),
            None,
            LocknAlertApiError,
        ),
        (
            None,
            WSServerHandshakeError(MagicMock(), (), status=403),
            LocknAlertAuthError,
        ),
        (
            None,
            WSServerHandshakeError(MagicMock(), (), status=502),
            LocknAlertApiError,
        ),
        (None, ClientConnectionError(), LocknAlertApiError),
    ],
)
async def test_stream_failures(
    socket: FakeSocket | None, error: Exception | None, expected: type[Exception]
) -> None:
    """Closes, bad frames, refused and failed handshakes each raise the right error.

    Args:
        socket: The fake socket, if the handshake works.
        error: The handshake error, if it fails.
        expected: The error the stream must raise.
    """
    session = FakeSession(socket, error)
    received: list[HouseholdStatus] = []
    with pytest.raises(expected):
        await _client(session).async_stream_status(received.append, lambda: None)
    assert received == []


def _push_state(coordinator: LocknAlertCoordinator) -> tuple[bool, timedelta | None]:
    """Read the coordinator's push state fresh (no type narrowing across awaits).

    Args:
        coordinator: The coordinator.

    Returns:
        Whether the socket is connected, and the current polling interval.
    """
    return coordinator.push_connected, coordinator.update_interval


class ScriptedStream:
    """Stands in for async_stream_status, controlled step by step by a test."""

    def __init__(self) -> None:
        """Initialise the script."""
        self.on_status: Callable[[HouseholdStatus], None] | None = None
        self.connected = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def __call__(
        self,
        on_status: Callable[[HouseholdStatus], None],
        on_connected: Callable[[], None],
    ) -> None:
        """Connect, then wait until the test drops the connection.

        Args:
            on_status: The coordinator's status handler.
            on_connected: The coordinator's connected handler.

        Raises:
            LocknAlertApiError: When the test drops the connection.
        """
        self.calls += 1
        if self.calls > 1:
            # Later reconnects never succeed in this test
            await asyncio.Event().wait()
        self.on_status = on_status
        on_status(parse_status(status_payload()))
        on_connected()
        self.connected.set()
        await self.release.wait()
        msg = "dropped"
        raise LocknAlertApiError(msg)


async def test_coordinator_applies_pushes_and_falls_back_to_polling(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Pushes apply at once; polling slows while connected and speeds up after a drop.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry.
    """
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    stream = ScriptedStream()
    with patch(
        "custom_components.locknalert.api.LocknAlertApiClient.async_stream_status",
        new=stream,
    ):
        await setup_integration(hass, mock_config_entry)
        await asyncio.wait_for(stream.connected.wait(), 5)
        coordinator = mock_config_entry.runtime_data

        # Connected: polling is only a backstop
        assert _push_state(coordinator) == (True, PUSH_FALLBACK_INTERVAL)

        # A pushed fall shows up immediately, without a poll
        polls_before = aioclient_mock.call_count
        assert stream.on_status is not None
        stream.on_status(parse_status(status_payload(fall=True)))
        await hass.async_block_till_done()
        assert hass.states.get(FALL).state == STATE_ON  # type: ignore[union-attr]
        assert aioclient_mock.call_count == polls_before

        # A push for someone else is ignored
        stream.on_status(
            parse_status(status_payload(person_ref="Zz9" + "a" * 19, fall=False))
        )
        await hass.async_block_till_done()
        assert hass.states.get(FALL).state == STATE_ON  # type: ignore[union-attr]

        # The socket drops: fast polling again, and an immediate catch-up poll
        stream.release.set()
        await hass.async_block_till_done()
        assert _push_state(coordinator) == (False, UPDATE_INTERVAL)
        assert aioclient_mock.call_count > polls_before
        # The catch-up poll says nothing is active any more
        assert hass.states.get(FALL).state == STATE_OFF  # type: ignore[union-attr]
        assert coordinator.serial == SERIAL

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


class RefusingStream:
    """Refuses the token on the first connection; later ones stay open."""

    def __init__(self) -> None:
        """Initialise the script."""
        self.calls = 0
        self.reconnected = asyncio.Event()
        # The first connection refuses only once this is set
        self.go = asyncio.Event()
        self.go.set()

    async def __call__(
        self,
        on_status: Callable[[HouseholdStatus], None],
        on_connected: Callable[[], None],
    ) -> None:
        """Refuse once, then hold the connection.

        Args:
            on_status: Unused.
            on_connected: Unused.

        Raises:
            LocknAlertAuthError: On the first connection.
        """
        del on_status, on_connected
        self.calls += 1
        if self.calls == 1:
            await self.go.wait()
            msg = "token refused"
            raise LocknAlertAuthError(msg)
        self.reconnected.set()
        await asyncio.Event().wait()


async def test_token_expiry_after_a_healthy_connection_reconnects_at_once(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hourly close (token expired) reconnects straight away, with no backoff.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry.
        monkeypatch: Makes every connection count as healthy.
    """
    monkeypatch.setattr(
        "custom_components.locknalert.coordinator.PUSH_HEALTHY_SECONDS", 0.0
    )
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    stream = RefusingStream()
    with patch(
        "custom_components.locknalert.api.LocknAlertApiClient.async_stream_status",
        new=stream,
    ):
        await setup_integration(hass, mock_config_entry)
        # Reconnected well inside the first backoff delay
        await asyncio.wait_for(stream.reconnected.wait(), 2)
        assert stream.calls == 2
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_token_refused_on_connect_checks_with_a_poll(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A refusal right after connecting polls, so revoked access starts reauth.

    Args:
        hass: The Home Assistant instance.
        aioclient_mock: The HTTP mock.
        mock_config_entry: The entry.
    """
    aioclient_mock.get(STATUS_URL, json=status_payload(), headers=JSON_HEADERS)
    stream = RefusingStream()
    # Hold the refusal until the token has stopped working
    stream.go.clear()
    with patch(
        "custom_components.locknalert.api.LocknAlertApiClient.async_stream_status",
        new=stream,
    ):
        # Setup succeeds on a good first poll
        await setup_integration(hass, mock_config_entry)
        # Then the access is revoked: polls are refused too
        aioclient_mock.clear_requests()
        aioclient_mock.get(STATUS_URL, status=401)
        # The socket is refused right away
        stream.go.set()
        for _ in range(10):
            await asyncio.sleep(0)
        await hass.async_block_till_done()
        # The poll found the token refused: the person is asked to sign in again
        assert aioclient_mock.call_count >= 1
        assert any(
            flow["context"]["source"] == "reauth"
            for flow in hass.config_entries.flow.async_progress_by_handler("locknalert")
        )
        # No immediate reconnect after an unhealthy connection
        assert stream.calls == 1
