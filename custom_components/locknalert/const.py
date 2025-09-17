"""Constants for the LocknAlert Home Assistant integration."""

DOMAIN = "locknalert"
CONF_BASE_URL = "base_url"
CONF_API_KEY = "api_key"
CONF_TIMEOUT = "timeout"
CONF_DEFAULT_SERIAL = "default_serial"

DEFAULT_BASE_URL = "https://api.locknalert.co.za"
DEFAULT_TIMEOUT = 10
SERVICE_TRIGGER_FALL = "trigger_fall_alert"

# Service field constants
ATTR_SERIAL = "serial"
ATTR_SERIAL_NUMBER = "serial_number"
ATTR_ROOM_NAME = "room_name"
ATTR_TITLE = "title"
ATTR_MESSAGE = "message"
ATTR_EVENT_TIME = "event_time"

FALL_ENDPOINT = "/v1/homeassistant/fall_detected"
