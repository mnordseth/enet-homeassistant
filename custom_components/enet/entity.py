"""Generic Enet Smart Home Entity Model"""
import logging
from homeassistant.helpers.entity import Entity
from .utils import get_device_info

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
    _enet_channel_function = None

    def __init__(self, channel, coordinator):
        self._name = channel.name
        self.channel = channel
        self.coordinator = coordinator
        _LOGGER.info("Enet entity %s", self._name)

    @property
    def unique_id(self):
        """Return a unique ID."""
        return f"{self.channel.uid}-{self._enet_channel_function}"

    @property
    def name(self):
        """Return the name of the device."""
        return f"{self._name}"

    @property
    def device_info(self):
        """Return the device information."""
        return get_device_info(self.channel.device)

    @property
    def native_value(self) -> int:
        """Return the last value reported by the sensor."""
        return self.channel.get_current_value(self._enet_channel_function)

    def update_value(self, value: int) -> None:
        """Update the value of the sensor."""
        self.channel.set_current_value(self._enet_channel_function, value)

    async def async_added_to_hass(self):
        """Subscribe entity to updates when added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )


class EnetBaseDeviceEntity(EnetBaseEntity, Entity):
    """Generic Entity Class for Enet Smart Home devices"""

    def __init__(self, device, coordinator):
        self.device = device
        self.coordinator = coordinator

    @property
    def device_info(self):
        """Return the device information."""
        return get_device_info(self.device)
