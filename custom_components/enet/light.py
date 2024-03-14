"""Enet Smart Home light support"""
import logging
import math

from homeassistant.components.light import ColorMode, LightEntity

from .entity import EnetBaseEntity
from .aioenet import ActuatorChannel
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet light devices from a config entry"""
    hub = hass.data[DOMAIN][entry.entry_id]

    for device in hub.devices:
        for channel in device.channels:
            if isinstance(channel, ActuatorChannel) and channel.ha_domain == "light":
                async_add_entities([EnetLight(channel, hub.coordinator)])
    _LOGGER.info("Finished async setup()")


class EnetLight(EnetBaseEntity, LightEntity):
    """A representation of a Enet Smart Home dimmer or switch channel"""

    @property
    def is_on(self):
        return self.channel.state != 0

    @property
    def brightness(self):
        return math.ceil(float(self.channel.state / 100) * 255)

    @property
    def supported_color_modes(self):
        if self.channel.has_brightness:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    @property
    def color_mode(self):
        if self.channel.has_brightness:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    async def async_added_to_hass(self):
        """Subscribe entity to updates when added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_turn_on(self, **kwargs):
        _LOGGER.info("async_turn_on: (%s) %s", self.name, kwargs)

        if brightness := kwargs.get("brightness"):
            value = math.ceil(float(brightness) / 255 * 100)
            await self.channel.set_value(value)
        else:
            await self.channel.turn_on()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        _LOGGER.info("async_turn_off: (%s) %s", self.name, kwargs)
        await self.channel.turn_off()
        self.async_write_ha_state()
