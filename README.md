# LocknAlert Home Assistant Integration

This custom Home Assistant integration exposes a `locknalert.trigger_fall_alert` service that relays fall detections to the LocknAlert companion app backend. When you call the service, it POSTs to `https://api.locknalert.co.za/v1/homeassistant/fall_detected` with the payload expected by the platform so any connected mobile or web client receives the alert instantly.

## Installation

### Via HACS (recommended)

1. Add this repository as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) in HACS (category **Integration**).
2. Install **LocknAlert** from the HACS UI.
3. Restart Home Assistant to load the new integration.

### Manual installation

1. Copy the `custom_components/locknalert` directory into your Home Assistant `config/custom_components` folder.
2. Restart Home Assistant to load the new integration.

## Configuration

LocknAlert is configured entirely from the Home Assistant UI:

1. Navigate to **Settings → Devices & Services → Add Integration** and search for **LocknAlert**.
2. Enter your API key if you have one, along with the default serial number and any fallback title, message, or room name you want to reuse in service calls. The base URL and timeout fields are pre-populated with sensible defaults.
3. After the integration is created, open its **Configure** dialog at any time to adjust the defaults used by the service.

## Service usage

After configuring the integration, call the service `locknalert.trigger_fall_alert`. Provide either `serial` or `serial_number`; if neither is supplied, the integration falls back to the serial number configured in the integration options. `title`, `message`, and `room_name` fall back to the defaults defined in the integration settings if you omit them. Unknown keys are preserved so you can embed structured metadata for your automations.

Example service call:

```yaml
service: locknalert.trigger_fall_alert
data:
  serial: "LNA-DEVICE-001"
  room_name: "Bedroom"
  title: "Assisted living suite"
  message: "Resident may have fallen"
  event_time: "2024-04-25T18:42:23-05:00"
  detected_by: "pressure_mat"
  severity: "high"
```

### Event time normalization

`event_time` may be a Home Assistant datetime object or an ISO-8601 string. The integration converts it to UTC before sending it to the server. If the value cannot be parsed as a datetime it is sent unchanged so the backend can handle it.

### Error handling

* **404 Device is not connected** – the paired app has not yet established a WebSocket handshake. Reconnect the app before retrying.
* **410 Device connection lost** – the socket dropped after handshake. Re-establish the WebSocket connection and retry the service call.
* Other HTTP errors or network timeouts are surfaced as Home Assistant service call exceptions so they appear in the logbook and can be used in automations.

## Development

* `custom_components/locknalert/__init__.py` contains the integration logic and service registration.
* `custom_components/locknalert/services.yaml` documents the service fields for the Home Assistant UI.
* `custom_components/locknalert/manifest.json` registers the integration with Home Assistant.
