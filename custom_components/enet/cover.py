"""Enet Smart Home cover / blinds support"""

import logging
import math
from homeassistant.components.cover import (
    ATTR_POSITION,
    #    ATTR_TILT_POSITION,
    CoverEntity,
    CoverEntityFeature,
)

from .aioenet import ActuatorChannel
from .const import DOMAIN
from . import enet_devices

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet light devices from a config entry"""
    hub = hass.data[DOMAIN][entry.entry_id]

    for device in hub.devices:
        for channel in device.channels:
            if isinstance(channel, ActuatorChannel) and channel.ha_domain == "cover":
                async_add_entities([EnetCover(channel, hub.coordinator)])
    _LOGGER.info("Finished async setup()")


class EnetCover(CoverEntity):
    """A representation of an Enet Smart Home cover / blinds channel"""

    def __init__(self, channel, coordinator):
        self._name = channel.name
        self.channel = channel
        self.coordinator = coordinator

        self._attr_supported_features = (
            CoverEntityFeature.CLOSE
            | CoverEntityFeature.OPEN
            | CoverEntityFeature.SET_POSITION
        )
        _LOGGER.info("EnetCover.init()  done %s", self.name)

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
    def unique_id(self):
        return self.channel.uid

    @property
    def name(self):
        return self._name

    @property
    def is_closed(self):
        return self.channel.state == 0

    @property
    def current_cover_position(self):
        """Return current position of cover tilt.

        None is unknown, 0 is fully open, 100 is closed.
        """
        return math.ceil(float(self.channel.state / 100) * 255)

    async def async_added_to_hass(self):
        """Subscribe entity to updates when added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_close_cover(self, **kwargs) -> None:
        """Close the cover."""
        await self.channel.set_value(100)
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs) -> None:
        """Open the cover."""
        await self.channel.set_value(0)
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs) -> None:
        """Move the cover to a specific position."""
        enet_position = math.ceil(float(kwargs[ATTR_POSITION]) / 255 * 100)
        await self.channel.set_value(enet_position)
        self.async_write_ha_state()
