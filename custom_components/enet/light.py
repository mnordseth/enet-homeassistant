"""Enet Smart Home platform"""
from datetime import timedelta
import logging

from homeassistant.components.light import SUPPORT_BRIGHTNESS, LightEntity
import homeassistant.util.dt as dt_util

from .aioenet import Actuator
from .const import DOMAIN
from . import enet_devices

_LOGGER = logging.getLogger(__name__)
SKIP_UPDATES_DELAY = timedelta(seconds=5)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet light devices from a config entry"""
    hub = hass.data[DOMAIN][entry.entry_id]

    for device in hub.devices:
        if isinstance(device, Actuator):
            for channel in device.channels:
                async_add_entities([EnetLight(channel, hub.coordinator)])
    _LOGGER.info("Finished async setup()")


class EnetLight(LightEntity):
    """A representation of a Enet Smart Home dimmer or switch channel"""

    def __init__(self, channel, coordinator):
        self._name = channel.name
        self.channel = channel
        self.coordinator = coordinator
        self._no_updates_until = dt_util.utcnow()
        _LOGGER.info("EnetLight.init()  done %s", self.name)

    @property
    def device_info(self):
        device_info = enet_devices.device_info.get(self.channel.device.device_type)
        return {
            "identifiers": {(DOMAIN, self.channel.device.uid)},
            "name": self.channel.device.name,
            "manufacturer": device_info.get("manufacturer"),
            "model": f"{self.channel.device.device_type} ({device_info.get('description')})",
            "suggested_area": self.channel.device.location.replace("My home:", ""),
            "via_device": (DOMAIN, "Enet Controller"),
        }

    @property
    def icon(self):
        if self.available:
            if self.is_on:
                return "mdi:lightbulb-on"
            else:
                return "mdi:lightbulb-outline"
        else:
            return "mdi:exclamation-thick"

    @property
    def name(self):
        return self._name

    @property
    def should_poll(self):
        return False

    @property
    def is_on(self):
        return self.channel.state != 0

    @property
    def available(self):
        return True

    @property
    def brightness(self):
        return int(float(self.channel.state / 100) * 255)

    @property
    def unique_id(self):
        return self.channel.uid

    @property
    def supported_features(self):
        if self.channel.has_brightness:
            return SUPPORT_BRIGHTNESS
        return 0

    async def async_added_to_hass(self):
        """Subscribe entity to updates when added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_device_update(self, *args, **kwargs):
        _LOGGER.debug("async_device_update(%s, %s)", args, kwargs)
        if self._no_updates_until > dt_util.utcnow():
            return
        old_state = self.channel.state
        self.channel.state = await self.channel.get_value()
        _LOGGER.info("Update: (%s) %s -> %s", self.name, old_state, self.channel.state)

    async def async_turn_on(self, **kwargs):
        _LOGGER.info("async_turn_on: (%s) %s", self.name, kwargs)

        if brightness := kwargs.get("brightness"):
            value = int(float(brightness) / 255 * 100)
            await self.channel.set_value(value)
        else:
            await self.channel.turn_on()
        self._no_updates_until = dt_util.utcnow() + SKIP_UPDATES_DELAY
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        _LOGGER.info("async_turn_off: (%s) %s", self.name, kwargs)
        await self.channel.turn_off()
        self._no_updates_until = dt_util.utcnow() + SKIP_UPDATES_DELAY
        self.async_write_ha_state()
