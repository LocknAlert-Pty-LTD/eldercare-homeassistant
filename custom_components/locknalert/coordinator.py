"""Keeps the LocknAlert status current: pushed over a websocket, with polling as backup.

While the push socket is connected, every change arrives within moments and
polling slows to a backstop (PUSH_FALLBACK_INTERVAL). When the socket drops,
polling returns to UPDATE_INTERVAL until it reconnects, so the entities never
fall behind by more than that.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    OAuth2TokenRequestBaseError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HouseholdStatus,
    LocknAlertApiClient,
    LocknAlertApiError,
    LocknAlertAuthError,
)
from .const import (
    DOMAIN,
    PUSH_FALLBACK_INTERVAL,
    PUSH_HEALTHY_SECONDS,
    PUSH_RECONNECT_MAX,
    PUSH_RECONNECT_MIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type LocknAlertConfigEntry = ConfigEntry[LocknAlertCoordinator]


class LocknAlertCoordinator(DataUpdateCoordinator[HouseholdStatus]):
    """Fetches one person's alert status (and the household's) on a fixed interval."""

    config_entry: LocknAlertConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: LocknAlertConfigEntry,
        client: LocknAlertApiClient,
        serial: str,
        person_ref: str,
    ) -> None:
        """Initialise the coordinator.

        Args:
            hass: The Home Assistant instance.
            entry: The config entry.
            client: The API client.
            serial: The bridge serial the entry is linked to.
            person_ref: The pseudonymous id of the person who linked it.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {serial}",
            update_interval=UPDATE_INTERVAL,
            # Only notify entities when the alert flags actually change
            always_update=False,
        )
        self.client = client
        self.serial = serial
        self.person_ref = person_ref
        # Whether the push socket is connected right now
        self.push_connected = False

    @override
    async def _async_update_data(self) -> HouseholdStatus:
        """Fetch the latest status.

        Returns:
            The household status.

        Raises:
            ConfigEntryAuthFailed: The grant is no longer valid; start reauth.
            UpdateFailed: A temporary problem; keep the last state and retry.
        """
        try:
            status = await self.client.async_get_status()
        except (LocknAlertAuthError, OAuth2TokenRequestReauthError) as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except (LocknAlertApiError, OAuth2TokenRequestBaseError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="update_failed"
            ) from err
        # The grant is bound to one household and one person, so any other
        # serial or person means the reply can't be trusted for this entry
        if status.serial != self.serial or status.person_ref != self.person_ref:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="serial_mismatch"
            )
        return status

    async def async_run_push(self) -> None:
        """Keep the push socket connected for as long as the entry is loaded.

        Runs as the entry's background task (cancelled when it unloads). Each
        drop falls back to fast polling and reconnects with a doubling delay.
        """
        delay = PUSH_RECONNECT_MIN
        while True:
            started = time.monotonic()
            reconnect_now = False
            try:
                await self.client.async_stream_status(
                    self._handle_push, self._handle_push_connected
                )
            except LocknAlertAuthError, OAuth2TokenRequestReauthError:
                # The API closes the socket when the access token expires
                # (hourly). After a healthy connection, reconnect at once: the
                # token is refreshed first. Otherwise let a poll find out
                # whether the access has really ended (that starts reauth).
                healthy = time.monotonic() - started >= PUSH_HEALTHY_SECONDS
                reconnect_now = healthy
                if not healthy:
                    await self.async_request_refresh()
            except (LocknAlertApiError, OAuth2TokenRequestBaseError) as err:
                _LOGGER.debug("LocknAlert push socket ended: %s", err)
            self._handle_push_disconnected()
            if time.monotonic() - started >= PUSH_HEALTHY_SECONDS:
                delay = PUSH_RECONNECT_MIN
            if reconnect_now:
                continue
            # Jitter, so many installations don't reconnect in step
            await asyncio.sleep(delay * random.uniform(0.8, 1.2))  # noqa: S311 - not security
            delay = min(delay * 2, PUSH_RECONNECT_MAX)

    def _handle_push(self, status: HouseholdStatus) -> None:
        """Apply a pushed status, if it is for this entry's household and person.

        Args:
            status: The pushed status.
        """
        if status.serial != self.serial or status.person_ref != self.person_ref:
            _LOGGER.warning("Ignored a LocknAlert push for another household or person")
            return
        self.async_set_updated_data(status)

    def _handle_push_connected(self) -> None:
        """Slow polling to a backstop while pushes arrive."""
        self.push_connected = True
        self.update_interval = PUSH_FALLBACK_INTERVAL

    def _handle_push_disconnected(self) -> None:
        """Go back to fast polling until the socket reconnects."""
        if self.push_connected:
            self.push_connected = False
            self.update_interval = UPDATE_INTERVAL
            # Poll straight away: changes may have been missed while it dropped
            _ = self.hass.async_create_task(self.async_request_refresh())
