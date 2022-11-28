"""Enet Smart Home platform"""
from datetime import timedelta
import logging

from homeassistant.components.light import SUPPORT_BRIGHTNESS, LightEntity
import homeassistant.util.dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
SKIP_UPDATES_DELAY = timedelta(seconds=5)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet light devices from a config entry"""
    hub = hass.data[DOMAIN][entry.entry_id]
    devices = await hub.get_devices()
    for device in devices:
        for channel in device.channels:
            async_add_entities([EnetLight(channel)])
    _LOGGER.info("Finished async setup()")


class EnetLight(LightEntity):
    """A representation of a Enet Smart Home dimmer or switch channel"""

    def __init__(self, channel):
        self._name = channel.name
        self.channel = channel
        self._no_updates_until = dt_util.utcnow()
        _LOGGER.info("EnetLight.init()  done %s", self.name)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.channel.device.uid)},
            "name": self.channel.device.name,
            "manufacturer": "",
            "model": self.channel.device.device_type,
            "suggested_area": self.channel.device.location.replace("My home:", ""),
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
        return True

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
        _LOGGER.info("Enet async_turn_off: (%s) %s", self.name, kwargs)
        await self.channel.turn_off()
        self._no_updates_until = dt_util.utcnow() + SKIP_UPDATES_DELAY
        self.async_write_ha_state()
