"""Generic Enet Smart Home Entity Model"""
import logging
from homeassistant.helpers.entity import Entity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class EnetBaseEntity(Entity):
    """Generic Entity Class for Enet Smart Home devices"""

    def __init__(self, channel, coordinator):
        self._name = channel.name
        self.channel = channel
        self.coordinator = coordinator
        _LOGGER.info("Enet entity %s", self._name)

    @property
    def available(self):
        return True

    @property
    def unique_id(self):
        return self.channel.uid

    @property
    def name(self):
        return self._name

    @property
    def device_info(self):
        return self.channel.device.get_device_info()

    @property
    def should_poll(self):
        return False
