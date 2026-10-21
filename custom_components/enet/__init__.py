"""The Enet Smart Home integration."""

from __future__ import annotations

import logging
import asyncio
import aiohttp

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .aioenet import BatteryStateEvent, ButtonEvent, EnetClient
from .const import DOMAIN, ATTR_ENET_EVENT, NAME_ENET_CONTROLLER
from .device import async_setup_devices

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.SCENE,
    Platform.COVER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Enet Smart Home from a config entry."""
    _LOGGER.debug("Setting up Enet Smart Home entry")

    hass.data.setdefault(DOMAIN, {})
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
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        await hub.close()
        raise ConfigEntryNotReady("Failed to connect to Enet Smart Home") from e
    except Exception:
        _LOGGER.exception("Failed to set up Enet Smart Home")
        await hub.close()
        return False

    hass.data[DOMAIN][entry.entry_id] = hub
    hub.coordinator = EnetCoordinator(hass, hub, entry)
    await async_setup_devices(hub.coordinator)
    entry.async_on_unload(hub.subscribe(hub.coordinator.handle_enet_event))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Enet Smart Home entry")
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.close()

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow manual removal of devices the Enet server no longer reports.

    Devices that still exist on the Enet server (and the controller itself)
    cannot be removed, as they would be recreated on the next reload anyway.
    """
    hub = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if hub is None:
        return False

    known_uids = {device.uid for device in hub.devices}
    known_uids.add(NAME_ENET_CONTROLLER)

    return not any(
        identifier[0] == DOMAIN and identifier[1] in known_uids
        for identifier in device_entry.identifiers
    )


class EnetCoordinator(DataUpdateCoordinator):
    """Enet Smart Home coordinator notifying entities of battery state changes
    and forwarding button events to the hass bus.

    Events are read by the EnetClient's own background task, so the
    coordinator never fetches data itself.
    """

    def __init__(
        self, hass: HomeAssistant, hub: EnetClient, entry: ConfigEntry
    ) -> None:
        """Initialize the coordinator."""
        self.hub = hub
        self.hass = hass
        self.config_entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )
        _LOGGER.debug("EnetCoordinator initialized")

    def handle_enet_event(self, event) -> None:
        """Forward button events to the hass bus and update entities on
        battery state changes. Value updates reach the entities through the
        channel's on_update_callbacks.
        """
        if isinstance(event, ButtonEvent):
            bus_data = {
                "device_id": event.channel.device.hass_device_entry.id,
                "unique_id": event.channel.device.uid,
                "type": str(event.type),
                "subtype": str(event.subtype),
            }
            self.hass.bus.async_fire(ATTR_ENET_EVENT, bus_data)
        elif isinstance(event, BatteryStateEvent):
            self.async_update_listeners()
