"""Enet Smart Home cover / blinds support"""

import logging
from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntity,
    CoverEntityFeature,
    CoverDeviceClass
)

from custom_components.enet.enet_data.enums import ChannelApplicationMode, ChannelTypeFunctionName
from .entity import EnetBaseChannelEntity
from .aioenet import ActuatorChannel
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet cover devices from a config entry"""
    hub = hass.data[DOMAIN][entry.entry_id]

    supported_app_modes = [
        ChannelApplicationMode.BLINDS
    ]

    for device in hub.devices:
        for channel in device.channels:
            if isinstance(channel, ActuatorChannel) and channel.application_mode in supported_app_modes:
                async_add_entities([EnetCover(channel, hub.coordinator)])
    _LOGGER.info("Finished async setup()")


class EnetCover(EnetBaseChannelEntity, CoverEntity):
    """A representation of an Enet Smart Home cover / blinds channel"""

    def __init__(self, channel, coordinator):
        super().__init__(channel, coordinator)

        self._operation_mode = channel.get_operation_mode()

        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
            | CoverEntityFeature.STOP
        )
        match self._operation_mode:
            case "BLINDS":
                self._attr_supported_features |= (
                    CoverEntityFeature.OPEN_TILT
                    | CoverEntityFeature.CLOSE_TILT
                    | CoverEntityFeature.SET_TILT_POSITION
                    | CoverEntityFeature.STOP_TILT
                )
                self._attr_device_class = CoverDeviceClass.BLIND
            case "SHUTTER":
                self._attr_device_class = CoverDeviceClass.SHADE
            case "MARQUEE":
                self._attr_device_class = CoverDeviceClass.AWNING

    @property
    def is_closed(self) -> bool:
        """
        Determine if the cover is fully closed.

        Returns:
            bool: True if the cover is fully closed, False otherwise.
        """
        current_value = self.channel.get_current_value(ChannelTypeFunctionName.COVER_POSITION)
        return current_value == 100

    @property
    def current_cover_position(self) -> int:
        """Return current position of cover tilt.

        None is unknown, 0 is fully closed, 100 is fully open.
        """

        current_value = self.channel.get_current_value(ChannelTypeFunctionName.COVER_POSITION)
        return 100 - current_value

    @property
    def current_cover_tilt_position(self):
        """Return current position of cover tilt.

        None is unknown, 0 is fully closed, 100 is fully open.
        """
        if self._operation_mode == "BLINDS":
            current_value = self.channel.get_current_value(ChannelTypeFunctionName.TILT_POSITION)
            return 100 - current_value

        return None

    async def async_added_to_hass(self):
        """Subscribe entity to updates when added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_close_cover(self, **kwargs) -> None:
        """Close the cover."""
        await self.channel.set_value(ChannelTypeFunctionName.UP_DOWN, True)
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs) -> None:
        """Open the cover."""
        await self.channel.set_value(ChannelTypeFunctionName.UP_DOWN, False)
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs) -> None:
        """Move the cover to a specific position."""
        enet_position = 100 - kwargs[ATTR_POSITION]
        await self.channel.set_value(ChannelTypeFunctionName.COVER_POSITION, enet_position)
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs) -> None:
        """Stop the cover."""
        await self.channel.set_value(ChannelTypeFunctionName.STOP)
        self.async_write_ha_state()

    async def async_open_cover_tilt(self, **kwargs) -> None:
        """Open the cover tilt."""
        await self.channel.set_value(ChannelTypeFunctionName.TILT_POSITION, 0)
        self.async_write_ha_state()

    async def async_close_cover_tilt(self, **kwargs) -> None:
        """Close the cover tilt."""
        await self.channel.set_value(ChannelTypeFunctionName.TILT_POSITION, 100)
        self.async_write_ha_state()

    async def async_set_cover_tilt_position(self, **kwargs) -> None:
        """Move the cover tilt to a specific position."""
        enet_tilt_position = 100 - kwargs[ATTR_TILT_POSITION]
        await self.channel.set_value(ChannelTypeFunctionName.TILT_POSITION, enet_tilt_position)
        self.async_write_ha_state()

    async def async_stop_cover_tilt(self, **kwargs) -> None:
        """Stop the cover."""
        self.async_stop_cover(**kwargs)
