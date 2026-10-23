<img src="https://www.enet-smarthome.com/fileadmin/user_upload/Icons/eNet-Logo.svg" width="150" height="150" />

# Enet Smart Home integration for Home Assistant
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

This plugin integrates a Gira or Jung Enet Smart Home server to Home Assistant.

Supported features:
- Most actuators like dimmers and relays
- Listing and activating scenes defined on the Enet server
- Events from buttons (press and release). Both buttons configured for scenes and buttons connected to an actuator creates events.


## Installation using HACS

1. Add this repository as a custom repository inside HACS settings. Make sure you select `Integration` as Category.
2. Search for enet in the Overview page and install the component.
3. Provide the URL, username and password of your Enet Smart Home server

