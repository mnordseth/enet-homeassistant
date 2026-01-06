"""The Enet Smart Home integration."""
from __future__ import annotations

from datetime import timedelta
import logging
import asyncio

from typing import Any, Dict, NoReturn

from .enet_data.enums import ChannelTypeFunctionName
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .aioenet import EnetClient, ActuatorChannel, SensorChannel
from .const import DOMAIN, ATTR_ENET_EVENT, EVENT_TYPE_INITIAL_PRESS, EVENT_TYPE_SHORT_RELEASE, EVENT_TYPE_LONG_RELEASE
from .device import async_setup_devices

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.SCENE,
    Platform.COVER,
    Platform.SENSOR,
    Platform.SWITCH
]

EVENT_TYPE_CHANNELS = [
    ChannelTypeFunctionName.BUTTON_ROCKER,
    ChannelTypeFunctionName.MASTER_DIMMING,
    ChannelTypeFunctionName.SCENE_CONTROL,
    ChannelTypeFunctionName.TRIGGER_START
]
EVENT_DEVICE_BATTERY_STATE_CHANGED = "deviceBatteryStateChanged"
EVENT_OUTPUT_DEVICE_FUNCTION_CALLED = "outputDeviceFunctionCalled"
EVENT_VALUE_TYPE_ROCKER_STATE = "VT_ROCKER_STATE"
EVENT_VALUE_TYPE_ROCKER_SWITCH_TIME = "VT_ROCKER_SWITCH_TIME"
EVENT_VALUE_DOWN_BUTTON = "DOWN_BUTTON"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Enet Smart Home from a config entry."""
    _LOGGER.debug("Setting up Enet Smart Home entry")

    hass.data.setdefault(DOMAIN, {})
    hub = EnetClient(entry.data["url"],
                     entry.data["username"], entry.data["password"]
                     # ,noconnect=True, load_file="/workspaces/enet-homeassistant/examples/config_entry-enet-97f179557e3785d3430b8007471e68ff.json"
                     # ,noconnect=True, load_file="/workspaces/enet-homeassistant/examples/config_entry-enet-01JRNS5MR3V9DX73M7BQ79KC40.json"
                     )
    hub.coordinator = EnetCoordinator(hass, hub, entry)

    try:
        await hub.simple_login()
    except Exception as e:
        _LOGGER.error("Failed to login to Enet Smart Home: %s", e)
        return False

    hass.data[DOMAIN][entry.entry_id] = hub

    try:
        hub.devices = await hub.get_devices()
    except Exception as e:
        _LOGGER.error("Failed to get devices from Enet Smart Home: %s", e)
        return False

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await hub.coordinator.setup_event_listeners()
    await async_setup_devices(hub.coordinator)

    hass.loop.create_task(hub.coordinator.async_refresh())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Enet Smart Home entry")
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class EnetCoordinator(DataUpdateCoordinator):
    """Enet Smart Home coordinator responsible for subscribing to and handling events"""

    def __init__(self, hass: HomeAssistant, hub: EnetClient, entry: ConfigEntry) -> None:
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
        self.function_uid_map: Dict[str, Any] = {}
        if self.hub.baseurl.startswith("https://"):
            asyncio.create_task(self.ping_forever())
        _LOGGER.debug("EnetCoordinator initialized")

    async def setup_event_listeners(self) -> None:
        """Setup event listener for all output functions"""
        _LOGGER.debug("Setting up event listeners")
        await self.hub.setup_event_subscription_battery_state()
        for device in self.hub.devices:
            func_uids = device.get_function_uids_for_event()
            self.function_uid_map.update(func_uids)
            await device.register_events()

    async def ping_forever(self):
        """Ping server to keep conncetion alive"""
        while True:
            await self.hub.ping()
            await asyncio.sleep(28)

    async def _async_update_data(self) -> NoReturn:
        """Fetch events from Enet server

        This endpoint blocks for 30s if no events are available
        or returns immediatly when events are there. Loop forever
        to get next events.

        This function and event processing should be moved to aioenet
        at some point.

        """
        # _LOGGER.debug("_async_update_data()")
        if self.hub._offline:
            return
        while True:
            event = await self.hub.get_events()
            if event:
                try:
                    self.handle_event(event)
                except Exception as e:
                    _LOGGER.exception(
                        "Failed to handle event: %s (%s)", event, e)

    def handle_event(self, event_data: Dict[str, Any]) -> None:
        """Handle events from Enet Server. Either update value of actuator or
        forward event from sensor
        """
        for event in event_data["events"]:

            _LOGGER.debug("Handling event: %s", event)
            if event["event"] == EVENT_OUTPUT_DEVICE_FUNCTION_CALLED:
                data = event["eventData"]
                function_uid = data["deviceFunctionUID"]
                device = self.function_uid_map.get(function_uid)
                if not device:
                    _LOGGER.warning(
                        "Function %s does not map to device", function_uid)
                    continue

                values = data["values"]
                channel_type = device.get_channel_type_function_name_from_output_function_uid(
                    function_uid)

                if channel_type not in [ChannelTypeFunctionName.BUTTON_ROCKER, ChannelTypeFunctionName.MASTER_DIMMING, ChannelTypeFunctionName.SCENE_CONTROL, ChannelTypeFunctionName.TRIGGER_START]:
                    device.update_values(function_uid, values)
                    self.async_update_listeners()
                else:
                    # Decode sensor / button events and forward to hass bus
                    subtype = data["channelNumber"]
                    if len(values) != 2:
                        _LOGGER.warning("Expected 2 values: %s", event)
                        continue

                    event_type = EVENT_TYPE_INITIAL_PRESS
                    if values[0]["valueTypeID"] == EVENT_VALUE_TYPE_ROCKER_STATE:
                        # If a button is configured as a rocker, you have the UP and Down
                        # button on the same channel.
                        if values[0]["value"] == EVENT_VALUE_DOWN_BUTTON:
                            subtype += 1
                    if values[1]["valueTypeID"] == EVENT_VALUE_TYPE_ROCKER_SWITCH_TIME:
                        # Switch time is 0 on press and a number for release.
                        # We don't distinguish between long and short press for the
                        # moment.
                        switch_time = values[1]["value"]
                        if switch_time > 0:
                            event_type = EVENT_TYPE_SHORT_RELEASE
                        if switch_time > 60:
                            event_type = EVENT_TYPE_LONG_RELEASE

                    bus_data = {
                        "device_id": device.device.hass_device_entry.id,
                        "unique_id": device.device.uid,
                        "type": event_type,
                        "subtype": str(subtype),
                    }
                    self.hass.bus.async_fire(ATTR_ENET_EVENT, bus_data)
            elif event["event"] == EVENT_DEVICE_BATTERY_STATE_CHANGED:
                # _LOGGER.debug("Battery state changed: %s", event["eventData"])
                data = event["eventData"]
                device_uid = data.get("deviceUID", None)
                battery_state = data.get("batteryState", None)
                device = next(
                    (d for d in self.hub.devices if d.uid == device_uid), None)
                if device:
                    device.update_battery_state(battery_state)
                    self.async_update_listeners()
