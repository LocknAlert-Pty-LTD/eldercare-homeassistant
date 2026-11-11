"""Constants for the LocknAlert integration."""

from __future__ import annotations

from datetime import timedelta
import re
from typing import Final

DOMAIN: Final = "locknalert"

# The LocknAlert API. Fixed on purpose: tokens are only ever sent here, so a
# misconfiguration or a malicious "custom server" can never receive them.
API_BASE_URL: Final = "https://api.locknalert.co.za"
OAUTH2_AUTHORIZE_URL: Final = f"{API_BASE_URL}/eldercare/oauth/authorize"
OAUTH2_TOKEN_URL: Final = f"{API_BASE_URL}/v1/oauth/token"
OAUTH2_REVOKE_URL: Final = f"{API_BASE_URL}/v1/oauth/revoke"
STATUS_URL: Final = f"{API_BASE_URL}/v1/integrations/household/status"
# The same status pushed as soon as it changes (token in the header, never the URL)
PUSH_URL: Final = "wss://api.locknalert.co.za/v1/integrations/household/ws"

# The public OAuth client registered for this integration (PKCE, no secret)
OAUTH2_CLIENT_ID: Final = "locknalert-home-assistant"
# The only scope: read which kinds of alert are active in one household
OAUTH2_SCOPE: Final = "household:alerts:read"

# Config entry data
CONF_SERIAL: Final = "serial"
# The person's pairwise pseudonymous id (one entry per person per household)
CONF_PERSON_REF: Final = "person_ref"
# The person's first name at the time they linked (for naming the device)
CONF_PERSON_NAME: Final = "person_name"

# Sign-in method hints passed to the LocknAlert sign-in page
SIGN_IN_GOOGLE: Final = "google"
SIGN_IN_EMAIL: Final = "password"

# How often the household status is read (the API allows one read every 5 s)
UPDATE_INTERVAL: Final = timedelta(seconds=15)
# While the push socket is connected, polling is only a slow backstop
PUSH_FALLBACK_INTERVAL: Final = timedelta(minutes=5)
# Reconnect delays after the socket drops: doubling from the first to the last
PUSH_RECONNECT_MIN: Final = 5.0
PUSH_RECONNECT_MAX: Final = 300.0
# A connection that lasted this long counts as healthy (resets the backoff)
PUSH_HEALTHY_SECONDS: Final = 60.0
# Socket ping interval, so a dead connection is noticed (seconds)
PUSH_HEARTBEAT: Final = 30.0
# Close codes the API uses: the token no longer works / not allowed / try later
PUSH_CLOSE_TOKEN_INVALID: Final = 4401
PUSH_CLOSE_INSUFFICIENT_SCOPE: Final = 4403
PUSH_CLOSE_TRY_LATER: Final = 4429
# Network timeout for each API request, in seconds
REQUEST_TIMEOUT: Final = 10
# Status replies are a few hundred bytes; anything larger is refused unread
MAX_RESPONSE_BYTES: Final = 16 * 1024

# A bridge serial after upper-casing, e.g. LNA-00042 (mirrors the API's check)
SERIAL_PATTERN: Final = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,31}$")
# The API's pairwise person id: 128 bits as unpadded URL-safe base64
PERSON_REF_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{22}$")
# Longest first name accepted from the API
MAX_PERSON_NAME_LENGTH: Final = 64

# Every alert flag the API reports, in display order
ALERT_KEYS: Final[tuple[str, ...]] = (
    "fall",
    "help",
    "emergency",
    "vitals",
    "heart_rate",
    "spo2",
    "hrv",
    "respiration",
    "irregular_rhythm",
    "combined_vitals",
    "routine",
    "stillness",
    "watch_off",
    "family_check",
)


def entry_unique_id(serial: str, person_ref: str) -> str:
    """Return a config entry's unique id: one entry per person per household.

    Args:
        serial: The bridge serial.
        person_ref: The person's pairwise pseudonymous id.

    Returns:
        The unique id, e.g. "LNA-00042_q3J...".
    """
    return f"{serial}_{person_ref}"


def normalize_serial(value: str) -> str | None:
    """Return a bridge serial in canonical form, or None if it is not plausible.

    Args:
        value: The serial as typed, e.g. " lna-00042 ".

    Returns:
        The serial upper-cased and trimmed, e.g. "LNA-00042", or None.
    """
    serial = value.strip().upper()
    return serial if SERIAL_PATTERN.fullmatch(serial) else None
