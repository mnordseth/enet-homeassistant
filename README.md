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
| Scenes on the Enet server | Scene and a button to save it (see below) |

### Buttons

Every button creates an event entity, and a rocker creates one for each side. Each button can be used as a device trigger in automations for:

- Initial press
- Release after a short press
- Release after a long press

Button presses are also fired on the event bus as `enet_event`, with the data `device_id`, `unique_id`, `type` (`initial_press`, `short_release` or `long_release`) and `subtype` (the button number).

### Scenes

The scenes of the Enet server are added as scenes: the scenes made in the Enet app, and scenes created from Home Assistant. Activating one runs the scene on the Enet server, the same as the scene buttons and the Enet app.

The server also makes lights on/off scenes for each room, each floor and the whole home, which the Enet app uses for the on/off switch of a room. To add those too, turn on **Lights on/off scenes of rooms** under **Settings → Devices & services → Enet Smart Home → Configure**. They are named like *Kitchen lights on*. Room scenes without members, like the blinds scenes in a house without blinds, are left out.

When the option is changed, the integration reloads. Scenes that are no longer added are removed, with their *Save* buttons.

Each scene shows its members and their levels in the attributes:

- `entity_id`: the lights, switches and covers of the scene
- `levels`: the level of each member in the scene. Brightness in percent for dimmers (0 is off), `on`/`off` for switches, and `position` and `tilt_position` for covers (100 is open). The levels are read from the server after startup, so they can take a moment to appear.

The scenes can be changed from Home Assistant. The changes are stored on the Enet server, and are used by the scene buttons and the Enet app as well:

- **Save the current state:** set the lights the way you want them, then press the scene's *Save* button (on the Enet Smart Home Server device), or use the action `enet.save_scene`. Every member is stored at its current level. The members store their level themselves when they receive one scene telegram, and the changed levels are then read back from the devices. This takes a few seconds, and the lights don't flicker. A member that misses the telegram is programmed with its level instead. Nothing is sent when no member changed.
- **Change one member:** the action `enet.set_scene_level` sets the level of one member: `brightness_pct` or `state` for lights and switches, `position` and `tilt_position` for covers.

  ```yaml
  action: enet.set_scene_level
  target:
    entity_id: scene.enet_smart_home_server_evening
  data:
    member: light.kitchen
    brightness_pct: 40
  ```

- **Add or remove a member:** the actions `enet.add_scene_member` and `enet.remove_scene_member` add a light, switch or cover to the scene or remove it. A new member is stored at its current state, or at the level given like for `enet.set_scene_level`.

  ```yaml
  action: enet.add_scene_member
  target:
    entity_id: scene.enet_smart_home_server_evening
  data:
    member: light.kitchen
  ```

- **Create a scene:** the action `enet.create_scene` creates a scene with lights, switches and covers at their current state. `icon` is the scene's icon in the Enet app (`lights_on` if left out). The scene is added in Home Assistant, with its *Save* button, and shows up in the Enet app.

  ```yaml
  action: enet.create_scene
  data:
    name: Movie
    members:
      - light.living_room
      - light.kitchen
    icon: movie_projector
  ```

- **Delete a scene:** the action `enet.delete_scene` deletes the scene on the Enet server, so it is gone from the Enet app and the scene buttons as well.

Setting a level with `enet.set_scene_level`, adding or removing a member, and creating or deleting a scene program the devices by radio, which takes a few seconds per member (adding a member about 20 seconds). Programming restarts the device, and lights and switches that were on come back off: the integration switches them back on to their level afterwards. The lights on/off scenes the server makes for each room can't be changed or deleted. Scenes can't be edited in Home Assistant's scene editor, which only edits Home Assistant's own scenes.

### Motion detectors

The motion sensor turns on when the detector reports motion, and turns off after the time reported by the detector. On startup it is off until the first motion is reported.

## Requirements

- Home Assistant 2024.11 or newer
- A Gira or Jung eNet Smart Home server reachable from Home Assistant
- A user account on the Enet server

## No eNet server?

The eNet Smart Home server has been discontinued. If your server has stopped
working, have a look at [enet-bridge](https://github.com/engiadina/enet-bridge).
It replaces the server with an ESP32 and CC1101 USB radio and comes with its own
Home Assistant integration. It can import the export from your eNet server to
take over the existing installation.

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
