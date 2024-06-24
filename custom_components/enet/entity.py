"""Generic Enet Smart Home Entity Model"""
import logging
from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

class EnetBaseEntity(Entity):
    """Generic Entity Class for Enet Smart Home entities"""
    @property
    def available(self):
        """Return True if entity is available."""
        return True

    @property
    def should_poll(self):
        """Return the polling state. False means the entity will only update when it has new data."""
        return False

class EnetBaseChannelEntity(EnetBaseEntity, Entity):
    """Generic Entity Class for Enet Smart Home channel"""

    def __init__(self, channel, coordinator):
        self._name = channel.name
        self.channel = channel
        self.coordinator = coordinator
        _LOGGER.info("Enet entity %s", self._name)

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self.channel.uid

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def device_info(self):
        """Return the device information."""
        return self.channel.device.get_device_info()

class EnetBaseDeviceEntity(EnetBaseEntity, Entity):
    """Generic Entity Class for Enet Smart Home devices"""

    def __init__(self, device, coordinator):
        self.device = device
        self.coordinator = coordinator

    @property
    def device_info(self):
        """Return the device information."""
        return self.device.get_device_info()
