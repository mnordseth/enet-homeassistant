"""Enet Smart Home switch support"""
import logging

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass

from custom_components.enet.enet_data.enums import ChannelApplicationMode, ChannelTypeFunctionName

from .entity import EnetBaseChannelEntity
from .aioenet import ActuatorChannel

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet switch devices from a config entry"""
    hub = entry.runtime_data

    supported_app_modes = [
        ChannelApplicationMode.SWITCHING
    ]

    for device in hub.devices:
        for channel in device.channels:
            if isinstance(channel, ActuatorChannel) and channel.application_mode in supported_app_modes:
                async_add_entities([EnetSwitch(channel)])
    _LOGGER.debug("Finished async setup()")


class EnetSwitch(EnetBaseChannelEntity, SwitchEntity):
    """A representation of a Enet Smart Home switch channel"""

    def __init__(self, channel):
        super().__init__(channel)
        self._attr_device_class = SwitchDeviceClass.SWITCH

    @property
    def is_on(self):
        """Return true if switch is on."""
        return self.channel.get_current_value(ChannelTypeFunctionName.ON_OFF)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        _LOGGER.debug("async_turn_on: (%s) %s", self.name, kwargs)

        await self.channel.turn_on()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        _LOGGER.debug("async_turn_off: (%s) %s", self.name, kwargs)
        await self.channel.turn_off()
        self.async_write_ha_state()
