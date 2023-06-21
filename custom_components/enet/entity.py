"""Generic Enet Smart Home Entity Model"""
import logging
from homeassistant.helpers.entity import Entity, DeviceInfo
from . import enet_devices
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class EnetBaseEntity(Entity):
    """Generic Entity Class for Enet Smart Home devices"""

    def __init__(self, channel, coordinator):
        self._name = channel.name
        self.channel = channel
        self.coordinator = coordinator
        _LOGGER.info("Enet sensor %s", self._name)

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
        device_info = enet_devices.device_info.get(self.channel.device.device_type)
        return DeviceInfo(
            {
                "identifiers": {(DOMAIN, self.channel.device.uid)},
                "name": self.channel.device.name,
                "manufacturer": device_info.get("manufacturer"),
                "model": f"{self.channel.device.device_type} ({device_info.get('description')})",
                "suggested_area": self.channel.device.location.replace("My home:", ""),
                "via_device": (DOMAIN, "Enet Controller"),
            }
        )

    @property
    def should_poll(self):
        return False
