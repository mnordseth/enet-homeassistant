<img src="https://www.enet-smarthome.com/fileadmin/user_upload/Icons/eNet-Logo.svg" width="150" height="150" />

# Enet Smart Home integration for Home Assistant
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

This integration connects a Gira or Jung eNet Smart Home server to Home Assistant. It reads the devices configured on the server and lets you control and monitor them from Home Assistant.

The integration talks to the server on your local network. Changes are pushed by the server as they happen, so there is no polling.

## Supported features

| Enet channel | Home Assistant entity |
|---|---|
| Switching and dimming light actuators | Light (on/off, brightness for dimmers) |
| Switching actuators | Switch |
| Blind, shutter and awning actuators | Cover (open, close, stop, position; tilt for blinds) |
| Energy sensors | Sensors for power, current, voltage and total energy |
| Light sensors | Illuminance sensor |
| Motion detectors | Motion binary sensor |
| Buttons, rockers and scene buttons | Event entities and device triggers (see below) |
| Battery powered devices | Battery state sensor (diagnostic) |
| Scenes on the Enet server | Scene (see below) |

### Buttons

Every button creates an event entity, and a rocker creates one for each side. Each button can be used as a device trigger in automations for:

- Initial press
- Release after a short press
- Release after a long press

Button presses are also fired on the event bus as `enet_event`, with the data `device_id`, `unique_id`, `type` (`initial_press`, `short_release` or `long_release`) and `subtype` (the button number).

### Scenes

Scenes defined in the Enet app can be activated from Home Assistant.

### Motion detectors

The motion sensor turns on when the detector reports motion, and turns off after the time reported by the detector. On startup it is off until the first motion is reported.

## Requirements

- Home Assistant 2024.11 or newer
- A Gira or Jung eNet Smart Home server reachable from Home Assistant
- A user account on the Enet server

## Installation using HACS

1. Add this repository as a custom repository inside HACS settings. Make sure you select `Integration` as Category.
2. Search for enet in the Overview page and install the component.
3. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & services → Add integration** and search for **Enet Smart Home**.
2. Enter:
   - **URL**: the address of the Enet server, for example `http://192.168.1.10`. Use `https://` if the server is set up for it. The server's certificate is not verified.
   - **Username** and **Password** of a user on the Enet server.

The login is checked before the integration is added. Each Enet server can only be added once.

To change the URL or the login later, open the integration and select **Reconfigure**. If the server rejects the login, Home Assistant asks for the username and password again.

Setting up a server found by network discovery is not supported yet. Add the integration manually as described above.

## Removal

1. Go to **Settings → Devices & services**, select **Enet Smart Home**, open the menu (⋮) of the entry and select **Delete**. This removes all devices and entities of the integration from Home Assistant. Nothing is changed on the Enet server.
2. To remove the files as well, open HACS, find **enet**, select **Remove** and restart Home Assistant.

A device that has been removed from the Enet server stays in Home Assistant until you delete it from its device page.

## Troubleshooting

- **Diagnostics**: on the integration page, open the menu (⋮) and select **Download diagnostics**. The file contains the data read from the Enet server. The username and password are removed.
- **Debug logging**: add this to `configuration.yaml` and restart Home Assistant:

  ```yaml
  logger:
    logs:
      custom_components.enet: debug
  ```
