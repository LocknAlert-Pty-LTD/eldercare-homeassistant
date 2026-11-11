"""Config flow for LocknAlert: sign in (Google or LocknAlert account), link a bridge.

Steps:

1. ``user``: choose how to sign in (Google, or a LocknAlert email and password).
2. ``serial``: the bridge serial of the household to link.
3. The browser opens the LocknAlert sign-in (with two-factor) and consent page;
   access is granted only to that household, and only if the account is an
   approved member of it.
4. The returned token is checked against the household before the entry is created.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, override

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import probatio

from .api import (
    HouseholdStatus,
    LocknAlertApiClient,
    LocknAlertApiError,
    LocknAlertAuthError,
)
from .const import (
    CONF_PERSON_NAME,
    CONF_PERSON_REF,
    CONF_SERIAL,
    DOMAIN,
    SIGN_IN_EMAIL,
    SIGN_IN_GOOGLE,
    entry_unique_id,
    normalize_serial,
)
from .oauth import async_revoke_token

STEP_GOOGLE = "google"
STEP_ACCOUNT = "locknalert_account"


def _entry_title(status: HouseholdStatus) -> str:
    """Return the entry (and device) name for a person in a household.

    Args:
        status: The first status read with the new token.

    Returns:
        e.g. "Mary (LNA-00042)", or "LocknAlert LNA-00042 (q3J7)" when the
        person hasn't set a first name (the short id tells entries apart).
    """
    if status.person_name:
        return f"{status.person_name} ({status.serial})"
    return f"LocknAlert {status.serial} ({status.person_ref[:4]})"


SERIAL_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_SERIAL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="off")
        )
    }
)


class LocknAlertFlowHandler(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle LocknAlert sign-in and household linking."""

    DOMAIN = DOMAIN
    # Version 2: OAuth sign-in (version 1 used a shared API key)
    VERSION = 2

    def __init__(self) -> None:
        """Initialise the flow."""
        super().__init__()
        self._serial: str | None = None
        self._sign_in_method = SIGN_IN_GOOGLE

    @property
    @override
    def logger(self) -> logging.Logger:
        """Return the flow's logger."""
        return logging.getLogger(__name__)

    @property
    @override
    def extra_authorize_data(self) -> dict[str, str]:
        """Add the household and sign-in method to the authorize URL."""
        if self._serial is None:  # pragma: no cover - set before authorizing
            msg = "The bridge serial must be known before signing in"
            raise RuntimeError(msg)
        return {"bridge_serial": self._serial, "method": self._sign_in_method}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask how the person wants to sign in.

        Args:
            user_input: Unused (the menu answers with its own step).

        Returns:
            The sign-in method menu, or an abort.
        """
        # LocknAlert only accepts My Home Assistant as the sign-in redirect
        if "my" not in self.hass.config.components:
            return self.async_abort(reason="my_home_assistant_required")
        return self.async_show_menu(
            step_id="user", menu_options=[STEP_GOOGLE, STEP_ACCOUNT]
        )

    async def async_step_google(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in with Google.

        Args:
            user_input: Unused.

        Returns:
            The next step.
        """
        del user_input
        self._sign_in_method = SIGN_IN_GOOGLE
        return await self.async_step_serial()

    async def async_step_locknalert_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in with a LocknAlert email and password.

        Args:
            user_input: Unused.

        Returns:
            The next step.
        """
        del user_input
        self._sign_in_method = SIGN_IN_EMAIL
        return await self.async_step_serial()

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the bridge serial (kept from the entry when re-authenticating).

        Args:
            user_input: The submitted form, if any.

        Returns:
            The form, or the browser sign-in.
        """
        if self.source == SOURCE_REAUTH and self._serial is not None:
            return await self.async_step_pick_implementation()

        errors: dict[str, str] = {}
        if user_input is not None:
            serial = normalize_serial(str(user_input[CONF_SERIAL]))
            if serial is None:
                errors[CONF_SERIAL] = "invalid_serial"
            else:
                # Several people may link the same household (one entry each),
                # so duplicates are only known once the person has signed in
                self._serial = serial
                return await self.async_step_pick_implementation()

        return self.async_show_form(
            step_id="serial",
            data_schema=self.add_suggested_values_to_schema(
                SERIAL_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication for an existing household.

        Args:
            entry_data: The entry's stored data.

        Returns:
            The confirmation step.
        """
        self._serial = str(entry_data[CONF_SERIAL])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain that the person must sign in again.

        Args:
            user_input: The (empty) confirmation, if submitted.

        Returns:
            The confirmation form, or the sign-in method menu.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={"serial": self._serial or ""},
            )
        return await self.async_step_user()

    @override
    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Check the new token against the household, then create or update the entry.

        Args:
            data: The implementation domain and the token.

        Returns:
            The created entry, the reauth result, or an abort.
        """
        serial = self._serial
        token: dict[str, Any] = data["token"]
        if serial is None:  # pragma: no cover - set before authorizing
            return self.async_abort(reason="oauth_error")

        status, reason = await self._async_check_token(token, serial)
        unique_id = (
            entry_unique_id(serial, status.person_ref) if status is not None else None
        )
        if unique_id is not None:
            reason = self._duplicate_or_wrong_person(unique_id)
        if status is None or unique_id is None or reason is not None:
            # Nothing will store this token: revoke it so no grant is left behind
            await self._async_discard(token)
            return self.async_abort(reason=reason or "oauth_error")

        await self.async_set_unique_id(unique_id)
        entry_data = {
            **data,
            CONF_SERIAL: serial,
            CONF_PERSON_REF: status.person_ref,
            CONF_PERSON_NAME: status.person_name,
        }
        if self.source == SOURCE_REAUTH:
            reauth_entry = self._get_reauth_entry()
            old_token = reauth_entry.data.get("token")
            result = self.async_update_reload_and_abort(reauth_entry, data=entry_data)
            # The previous grant is replaced: make sure it can't be used again
            if isinstance(old_token, Mapping):
                await self._async_discard(old_token)
            return result
        return self.async_create_entry(title=_entry_title(status), data=entry_data)

    def _duplicate_or_wrong_person(self, unique_id: str) -> str | None:
        """Check the signed-in person against existing entries.

        Args:
            unique_id: The entry unique id for this person and household.

        Returns:
            "wrong_account" when re-authenticating as someone else,
            "already_configured" when this person already linked this
            household, otherwise None.
        """
        if self.source == SOURCE_REAUTH:
            # An entry holds one person's alerts: only that person may renew it
            reauth_unique_id = self._get_reauth_entry().unique_id
            return None if reauth_unique_id == unique_id else "wrong_account"
        if self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unique_id):
            return "already_configured"
        return None

    async def _async_check_token(
        self, token: Mapping[str, Any], serial: str
    ) -> tuple[HouseholdStatus | None, str | None]:
        """Read the status with a new token to prove it works for that household.

        Args:
            token: The token response.
            serial: The bridge serial the person entered.

        Returns:
            The status and None when the token reads that household, else
            None and the abort reason.
        """

        async def access_token() -> str:
            """Return the token just issued.

            Returns:
                The access token.
            """
            return str(token["access_token"])

        client = LocknAlertApiClient(async_get_clientsession(self.hass), access_token)
        try:
            status = await client.async_get_status()
        except LocknAlertAuthError:
            return None, "oauth_unauthorized"
        except LocknAlertApiError:
            return None, "cannot_connect"
        # The grant must be for the household the person asked to link
        if status.serial != serial:
            return None, "serial_mismatch"
        return status, None

    async def _async_discard(self, token: Mapping[str, Any]) -> None:
        """Revoke a token that won't be stored, so no unused grant is left behind.

        Args:
            token: The token response.
        """
        refresh_token = token.get("refresh_token")
        if isinstance(refresh_token, str) and refresh_token:
            await async_revoke_token(self.hass, refresh_token)
