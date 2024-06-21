"""Enet Smart Home light support"""
import logging
from typing import Optional

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.util.scaling import scale_ranged_value_to_int_range

from custom_components.enet.enet_data.enums import ChannelApplicationMode, ChannelTypeFunctionName

from .entity import EnetBaseChannelEntity
from .aioenet import ActuatorChannel
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet light devices from a config entry"""
    hub = hass.data[DOMAIN][entry.entry_id]

    supported_app_modes = [
        ChannelApplicationMode.LIGHT_SWITCHING,
        ChannelApplicationMode.LIGHT_DIMMING,
    ]

    for device in hub.devices:
        for channel in device.channels:
            if isinstance(channel, ActuatorChannel) and channel.application_mode in supported_app_modes:
                async_add_entities([EnetLight(channel, hub.coordinator)])
    _LOGGER.info("Finished async setup()")


class EnetLight(EnetBaseChannelEntity, LightEntity):
    """A representation of a Enet Smart Home dimmer or switch channel"""

    @property
    def is_on(self):
        """Return true if light is on."""
        return self.channel.get_current_value(ChannelTypeFunctionName.ON_OFF)

    @property
    def brightness(self) -> Optional[int]:
        """Return the current brightness."""
        return scale_ranged_value_to_int_range(
            (1, 100),
            (1, 255),
            self.channel.get_current_value(ChannelTypeFunctionName.BRIGHTNESS),
        )

    @property
    def supported_color_modes(self):
        """Return the list of supported color modes."""
        if self.channel.application_mode == ChannelApplicationMode.LIGHT_DIMMING:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    @property
    def color_mode(self):
        """Return the color mode of the light."""
        if self.channel.application_mode == ChannelApplicationMode.LIGHT_DIMMING:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    async def async_added_to_hass(self):
        """Subscribe entity to updates when added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_turn_on(self, **kwargs):
        """Turn the light on. Either to a specific brightness level, or fully on."""
        _LOGGER.info("async_turn_on: (%s) %s", self.name, kwargs)

        if brightness := kwargs.get("brightness"):
            value = scale_ranged_value_to_int_range(
                (1, 255),
                (1, 100),
                brightness,
            )
            await self.channel.set_value(ChannelTypeFunctionName.BRIGHTNESS, value)
        else:
            await self.channel.turn_on()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the light off."""
        _LOGGER.info("async_turn_off: (%s) %s", self.name, kwargs)
        await self.channel.turn_off()
        self.async_write_ha_state()
