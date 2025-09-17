# LocknAlert Home Assistant Integration

This custom Home Assistant integration exposes a `locknalert.trigger_fall_alert` service that relays fall detections to the LocknAlert companion app backend. When you call the service, it POSTs to `https://api.locknalert.co.za/v1/homeassistant/fall_detected` with the payload expected by the platform so any connected mobile or web client receives the alert instantly.

## Installation

1. Copy the `custom_components/locknalert` directory into your Home Assistant `config/custom_components` folder.
2. Restart Home Assistant to load the new integration.

## Configuration

The integration is configured from `configuration.yaml`. The service is hard-wired to the LocknAlert cloud endpoint so you only need to provide optional authentication details or a default serial number for the device you want to target.

```yaml
locknalert:
  api_key: "YOUR_TOKEN"  # Optional: placed in the Authorization header as a Bearer token
  default_serial: "LNA-DEVICE-001"  # Optional
  timeout: 10  # Optional (seconds)
```

## Service usage

After reloading the configuration, call the service `locknalert.trigger_fall_alert`. Provide either `serial` or `serial_number`; all other keys are optional and are forwarded to the backend. Unknown keys are preserved so you can embed structured metadata for your automations.

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
