"""The Enet Smart Home integration."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceEntry

from .aioenet import AuthError, EnetClient, EnetServerError
from .const import DOMAIN, NAME_ENET_CONTROLLER
from .device import async_setup_devices
from .events import async_setup_enet_events

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.SCENE,
    Platform.COVER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
]

type EnetConfigEntry = ConfigEntry[EnetClient]


async def async_setup_entry(hass: HomeAssistant, entry: EnetConfigEntry) -> bool:
    """Set up Enet Smart Home from a config entry."""
    _LOGGER.debug("Setting up Enet Smart Home entry")

    hub = EnetClient(
        entry.data["url"],
        entry.data["username"],
        entry.data["password"],
        # noconnect=True,
        # load_file="/workspaces/enet-homeassistant/examples/config_entry-enet-oliver.json",
        # ,noconnect=True, load_file="/workspaces/enet-homeassistant/examples/config_entry-enet-01JRNS5MR3V9DX73M7BQ79KC40.json"
    )

    try:
        await hub.initialize()
    except AuthError as e:
        await hub.close()
        raise ConfigEntryAuthFailed(
            "Invalid username or password for Enet Smart Home"
        ) from e
    except (TimeoutError, aiohttp.ClientError, EnetServerError) as e:
        await hub.close()
        raise ConfigEntryNotReady("Failed to connect to Enet Smart Home") from e
    except Exception:
        # Unexpected errors are logged by Home Assistant, just release the session
        await hub.close()
        raise

    entry.runtime_data = hub
    await async_setup_devices(hass, entry, hub)
    async_setup_enet_events(hass, entry, hub)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnetConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Enet Smart Home entry")
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.close()

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: EnetConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow manual removal of devices the Enet server no longer reports.

    Devices that still exist on the Enet server (and the controller itself)
    cannot be removed, as they would be recreated on the next reload anyway.
    """
    hub = getattr(config_entry, "runtime_data", None)
    if hub is None:
        return False

    known_uids = {device.uid for device in hub.devices}
    known_uids.add(NAME_ENET_CONTROLLER)

    return not any(
        identifier[0] == DOMAIN and identifier[1] in known_uids
        for identifier in device_entry.identifiers
    )

