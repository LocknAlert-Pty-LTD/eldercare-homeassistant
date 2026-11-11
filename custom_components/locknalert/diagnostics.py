"""Diagnostics for the LocknAlert integration.

Diagnostics are often shared publicly (for example in a GitHub issue), so
tokens, the person's name (also in the entry title) and their pseudonymous
id are always redacted. The alert flags themselves are shown, because they
are what a support request is usually about; review before sharing.
"""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_PERSON_NAME, CONF_PERSON_REF
from .coordinator import LocknAlertConfigEntry

TO_REDACT: Final = {
    "token",
    "access_token",
    "refresh_token",
    "code_verifier",
    "title",
    "unique_id",
    CONF_PERSON_NAME,
    CONF_PERSON_REF,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: LocknAlertConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.

    Returns:
        The redacted entry and the latest status flags.
    """
    del hass
    coordinator = entry.runtime_data
    status = coordinator.data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "status": {
            "serial": status.serial,
            "active_alert": status.active_alert,
            "open_alert_count": status.open_alert_count,
            "alerts": dict(status.alerts),
            "generated_at": status.generated_at.isoformat(),
        },
    }
