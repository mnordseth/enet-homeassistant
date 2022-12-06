"""The Enet Smart Home integration."""
from __future__ import annotations

from datetime import timedelta
import logging

import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .aioenet import EnetClient, Channel
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.SCENE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Enet Smart Home from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    hub = EnetClient(entry.data["url"], entry.data["username"], entry.data["password"])
    hub.coordinator = EnetCoordinator(hass, hub)
    await hub.simple_login()
    hass.data[DOMAIN][entry.entry_id] = hub
    hub.devices = await hub.get_devices()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await hub.coordinator.setup_event_listeners()
    hass.loop.create_task(hub.coordinator.async_refresh())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class EnetCoordinator(DataUpdateCoordinator):
    """Enet Smart Home coordinator responsible for subscribing to and handling events"""

    def __init__(self, hass, hub):
        """Initialize my coordinator."""
        self.hub = hub
        self.hass = hass
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )
        self.function_uid_map = {}
        _LOGGER.debug("EnetCoordinator.__init__()")

    async def setup_event_listeners(self):
        """Setup event listener for all output functions"""
        _LOGGER.debug("Setting up event listeners")
        for device in self.hub.devices:
            func_uids = device.get_function_uids_for_event()
            self.function_uid_map.update(func_uids)
            await device.register_events()

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.debug("_async_update_data()")
        while True:
            event = await self.hub.get_events()
            # _LOGGER.debug("Got event: %s", event)
            if event:
                self.handle_event(event)

    def handle_event(self, event_data):
        """Handle events from Enet Server. Either update value of actuator or
        forward event from sensor"""
        for event in event_data["events"]:
            # _LOGGER.debug("Handling event: %s", event)
            if event["event"] == "outputDeviceFunctionCalled":
                data = event["eventData"]
                function_uid = data["deviceFunctionUID"]
                device = self.function_uid_map.get(function_uid)
                if not device:
                    _LOGGER.debug(
                        "Function %s does not map to device",
                        function_uid,
                    )
                    continue
                if isinstance(device, Channel):
                    values = data["values"]
                    if len(values) != 1:
                        _LOGGER.debug(
                            "Event for device '%s' has multiple values: %s",
                            device,
                            values,
                        )
                    for value_spec in values:
                        value = value_spec["value"]
                        device.state = value
                        _LOGGER.debug("Updating value of %s to %s", device, value)
                        # _LOGGER.debug("Updating listners: %s", self._listeners)
                        self.async_update_listeners()
                else:
                    # For now we just forward sensor / button events to hass
                    _LOGGER.debug(
                        "Event is not for actuator... type = (%s)  %s",
                        type(device),
                        data,
                    )
                    self.hass.bus.async_fire("enet_event" "", data)
