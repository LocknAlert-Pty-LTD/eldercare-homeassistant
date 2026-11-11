# LocknAlert for Home Assistant

Shows LocknAlert Eldercare alerts in Home Assistant, as binary sensors you can use in automations: is an alert active right now, and what kind. Each person links their own alerts, so several people in one household each appear as their own device.

The integration is **read-only**. It can't see readings, health history, locations or contacts, never shows another person's alerts, and can't change anything in LocknAlert.

## Requirements

- Home Assistant **2026.10** or newer, with **My Home Assistant** enabled (it is part of the default configuration). LocknAlert only sends sign-in responses through My Home Assistant.
- A LocknAlert Eldercare account that is an **approved member** of the household, with two-factor sign-in set up.
- The household bridge's **serial number** (printed on the bridge, for example `LNA-00042`).

## Installation

### HACS (recommended)

1. Add this repository as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) in HACS (category **Integration**).
2. Install **LocknAlert** and restart Home Assistant.

### Manual

Copy `custom_components/locknalert` into your Home Assistant `config/custom_components` folder and restart.

## Setup

1. Go to **Settings → Devices & services → Add integration** and choose **LocknAlert**.
2. Choose how you sign in: **Google**, or your **LocknAlert account** (email and password).
3. Enter the bridge serial number.
4. Your browser opens the LocknAlert sign-in page. Sign in, enter the WhatsApp code, then review what Home Assistant will be able to see and choose **Allow**.

The serial number is asked for before the browser opens because access is granted for that one household only, and LocknAlert checks that your account is an approved member of it.

### Several people in one household

Each person's alerts are their own health information, so **each person links their own**. To add someone else, add the integration again and have them sign in with their own LocknAlert account. A legal representative can sign in on the person's behalf if the account was set up that way.

Each entry shows:

- **that person's own alerts** (from their watch and phone), and
- **alerts the bridge itself raises for the household**, such as a fall the bridge detects, which every household member already receives.

It never shows another member's personal alerts. To link more than one household, add the integration again for each bridge.

## Entities

Each linked person becomes a device (named after them, for example **Mary (LNA-00042)**) with these entities:

| Entity | On when |
| --- | --- |
| Active alert | Any alert below is active |
| Fall alert | A fall was detected by the person's watch, or by the household's bridge |
| Help requested | The person said they need help, or pressed SOS |
| Emergency | An emergency is active for the person (or the household) and the emergency contacts are being alerted |
| Vitals alert | Any abnormal-readings alert |
| Heart rate alert | A heart rate alert |
| Blood oxygen alert | A blood oxygen (SpO2) alert |
| HRV alert | A heart rate variability alert |
| Breathing alert | A breathing rate alert |
| Irregular rhythm alert | An irregular heart rhythm alert |
| Combined vitals alert | Several readings are unusual together |
| Unusual routine alert | The daily routine is unusual (for example not up at the usual time) |
| Stillness alert | Unusually long stillness |
| Watch off | The watch was taken off, or lost contact, and wasn't put back |
| Family check-in | A family member asked the person to check in |
| Open alerts (sensor) | The number of open check-ins |

An alert counts while LocknAlert is waiting for an answer, and after it escalates for as long as the emergency stays active. Updates are **real-time**: Home Assistant keeps a secure websocket open to LocknAlert, which pushes every change the moment it happens. While that connection is up, Home Assistant also checks every 5 minutes as a backstop. If it drops, Home Assistant checks every 15 seconds and reconnects automatically. If LocknAlert can't be reached at all, the entities become **unavailable** rather than showing a stale "safe" state.

### Example automation

```yaml
automation:
  - alias: Flash the lights on a LocknAlert emergency
    triggers:
      - trigger: state
        entity_id: binary_sensor.mary_lna_00042_emergency
        to: "on"
    actions:
      - action: light.turn_on
        target:
          area_id: living_room
        data:
          flash: long
```

## Security

- **No passwords or Google tokens in Home Assistant.** Sign-in happens on LocknAlert's own page, using OAuth 2.0 Authorization Code with PKCE. Every setup uses a new PKCE verifier.
- **One person, one household, read-only.** The grant covers the person who signed in, one household and only the alert-status scope. LocknAlert checks that the account is still an approved member of the bridge's household on every request, and access stops if they leave it.
- **Pseudonymous.** Home Assistant identifies the person by a random-looking ID specific to this integration and household, not their account ID. Only their first name is shared, and the consent page shows it first.
- **Short-lived, rotating tokens.** Access tokens last one hour. Refresh tokens are replaced every time they're used, and if an old one is replayed, LocknAlert revokes the whole grant.
- **Automatic disconnection.** Changing or resetting your LocknAlert password, closing your account or removing the integration revokes access.
- **Fixed endpoint.** Tokens are only ever sent to `api.locknalert.co.za`, over verified TLS, and redirects are never followed. On the websocket the token goes in the `Authorization` header, never in the URL.
- **Live checks on the websocket.** LocknAlert checks the token and household membership again on every change it pushes, and at least every 5 seconds, so revoked access closes the connection within seconds. Every pushed message is validated as strictly as a normal reply.
- **Strict validation.** Status replies must match the expected shape exactly, including the household serial and the person, and anything else is rejected. Diagnostics redact all tokens and the person's name and ID.

When access ends, Home Assistant asks you to sign in again: you'll see a **Reconfigure** prompt on the integration.

## Upgrading from 0.x

Version 1.0 replaces the old API-key setup and the `locknalert.trigger_fall_alert` service. Old entries can't be converted to a secure household grant. Remove the old LocknAlert entry and add the integration again.

## Development

Home Assistant only runs on Linux and macOS, so on Windows use WSL or Docker.

```bash
pip install -r requirements_test.txt
ruff format --check .
ruff check .
mypy
pytest --cov
```
