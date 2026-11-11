"""The LocknAlert integration: Eldercare household alerts as binary sensors."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, ImplementationUnavailableError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
    async_register_implementation,
)
from homeassistant.helpers.typing import ConfigType

from .api import LocknAlertApiClient
from .const import CONF_PERSON_REF, CONF_SERIAL, DOMAIN
from .coordinator import LocknAlertConfigEntry, LocknAlertCoordinator
from .oauth import LocknAlertOAuth2Implementation, async_revoke_token

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the LocknAlert sign-in for config flows.

    Args:
        hass: The Home Assistant instance.
        config: The YAML configuration (unused: setup is UI only).

    Returns:
        True.
    """
    del config
    async_register_implementation(hass, DOMAIN, LocknAlertOAuth2Implementation(hass))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LocknAlertConfigEntry) -> bool:
    """Set up a linked household.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.

    Returns:
        True when set up.

    Raises:
        ConfigEntryNotReady: The sign-in implementation isn't available yet.
    """
    try:
        implementation = await async_get_config_entry_implementation(hass, entry)
    except ImplementationUnavailableError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="implementation_unavailable"
        ) from err
    oauth_session = OAuth2Session(hass, entry, implementation)

    async def access_token() -> str:
        """Return a valid access token, refreshing it first when needed.

        Returns:
            The access token.
        """
        await oauth_session.async_ensure_token_valid()
        return str(oauth_session.token["access_token"])

    client = LocknAlertApiClient(async_get_clientsession(hass), access_token)
    coordinator = LocknAlertCoordinator(
        hass, entry, client, entry.data[CONF_SERIAL], entry.data[CONF_PERSON_REF]
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Real-time updates over the push socket (cancelled when the entry unloads)
    _ = entry.async_create_background_task(
        hass, coordinator.async_run_push(), name=f"{DOMAIN} push {entry.entry_id}"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LocknAlertConfigEntry) -> bool:
    """Unload a linked household.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.

    Returns:
        True when unloaded.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: LocknAlertConfigEntry) -> None:
    """Revoke LocknAlert's grant when the integration is removed.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being removed.
    """
    token = entry.data.get("token")
    refresh_token = token.get("refresh_token") if isinstance(token, dict) else None
    if isinstance(refresh_token, str) and refresh_token:
        await async_revoke_token(hass, refresh_token)


async def async_migrate_entry(
    hass: HomeAssistant, entry: LocknAlertConfigEntry
) -> bool:
    """Migrate an old config entry.

    Version 1 entries used a shared API key and no sign-in. They can't be
    converted to a household-scoped grant, so they must be added again.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.

    Returns:
        False for version 1 entries (they must be removed and added again).
    """
    del hass
    if entry.version == 1:
        _LOGGER.error(
            "This LocknAlert entry was created by an older version that used an "
            "API key. Remove it and add LocknAlert again to sign in securely"
        )
        return False
    return True
