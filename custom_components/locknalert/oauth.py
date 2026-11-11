"""OAuth 2.0 (Authorization Code with PKCE) for the LocknAlert integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final, override

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import OAuth2TokenRequestConnectionError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    LocalOAuth2Implementation,
    LocalOAuth2ImplementationWithPkce,
)
from yarl import URL

from .const import (
    DOMAIN,
    OAUTH2_AUTHORIZE_URL,
    OAUTH2_CLIENT_ID,
    OAUTH2_REVOKE_URL,
    OAUTH2_SCOPE,
    OAUTH2_TOKEN_URL,
    REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

# Abandoned setup flows leave a verifier behind; keep at most this many
_MAX_PENDING_VERIFIERS: Final = 32
# The longest verifier RFC 7636 allows (more entropy)
_VERIFIER_LENGTH: Final = 128


class LocknAlertOAuth2Implementation(LocalOAuth2Implementation):
    """The LocknAlert sign-in, with a fresh PKCE verifier for every setup flow.

    Home Assistant's built-in PKCE implementation creates one verifier when it
    is registered and reuses it for every flow until restart. Here each flow
    gets its own verifier, held only until its code is exchanged.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the implementation.

        Args:
            hass: The Home Assistant instance.
        """
        super().__init__(
            hass,
            DOMAIN,
            client_id=OAUTH2_CLIENT_ID,
            # A public client: PKCE replaces the secret
            client_secret="",
            authorize_url=OAUTH2_AUTHORIZE_URL,
            token_url=OAUTH2_TOKEN_URL,
        )
        self._verifiers: dict[str, str] = {}

    @property
    @override
    def name(self) -> str:
        """Return the implementation's display name."""
        return "LocknAlert"

    @override
    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Return the authorize URL for a flow, with a new PKCE challenge.

        Args:
            flow_id: The config flow's id.

        Returns:
            The URL to open in the browser.
        """
        verifier = LocalOAuth2ImplementationWithPkce.generate_code_verifier(
            _VERIFIER_LENGTH
        )
        # Drop the oldest pending verifiers if many flows were abandoned
        while len(self._verifiers) >= _MAX_PENDING_VERIFIERS:
            self._verifiers.pop(next(iter(self._verifiers)))
        self._verifiers[flow_id] = verifier
        url = await super().async_generate_authorize_url(flow_id)
        return str(
            URL(url).update_query(
                {
                    "code_challenge": (
                        LocalOAuth2ImplementationWithPkce.compute_code_challenge(
                            verifier
                        )
                    ),
                    "code_challenge_method": "S256",
                    "scope": OAUTH2_SCOPE,
                }
            )
        )

    @override
    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        """Exchange the authorization code, proving possession of the verifier.

        Args:
            external_data: The callback data: the code and the decoded state.

        Returns:
            The token response.

        Raises:
            OAuth2TokenRequestConnectionError: No verifier is pending for the flow.
        """
        flow_id: str = external_data["state"]["flow_id"]
        verifier = self._verifiers.pop(flow_id, None)
        if verifier is None:
            raise OAuth2TokenRequestConnectionError(domain=DOMAIN)
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": external_data["code"],
                "redirect_uri": external_data["state"]["redirect_uri"],
                "code_verifier": verifier,
            }
        )


async def async_revoke_token(hass: HomeAssistant, token: str) -> None:
    """Revoke a token's grant at LocknAlert (RFC 7009); failures are logged only.

    Args:
        hass: The Home Assistant instance.
        token: The refresh (or access) token to revoke.
    """
    session = async_get_clientsession(hass)
    try:
        async with (
            asyncio.timeout(REQUEST_TIMEOUT),
            session.post(
                OAUTH2_REVOKE_URL,
                data={"client_id": OAUTH2_CLIENT_ID, "token": token},
                allow_redirects=False,
            ) as response,
        ):
            if response.status >= 400:  # noqa: PLR2004 - HTTP error range
                _LOGGER.warning(
                    "LocknAlert refused to revoke the token (HTTP %s)",
                    response.status,
                )
    except (TimeoutError, ClientError) as err:
        _LOGGER.warning("Could not revoke the LocknAlert token: %s", err)
