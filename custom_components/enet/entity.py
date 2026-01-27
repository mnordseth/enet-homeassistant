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
        self.channel.on_update_callbacks.append(self.on_value_updated)

    @property
    def unique_id(self):
        """Return a unique ID."""
        if self._enet_channel_function:
            return f"{self.channel.uid}-{self._enet_channel_function}"
        return self.channel.uid

    @property
    def name(self):
        """Return the name of the device."""
        sensor_name = getattr(self, "_attr_name", None)
        if sensor_name:
            return f"{self._name} {sensor_name}"
        return self._name

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

    async def on_value_updated(self) -> None:
        """Callback function when the value is updated."""
        self.async_write_ha_state()


class EnetBaseDeviceEntity(EnetBaseEntity, Entity):
    """Generic Entity Class for Enet Smart Home devices"""

    def __init__(self, device, coordinator):
        self.device = device
        self.coordinator = coordinator

    @property
    def device_info(self):
        """Return the device information."""
        return get_device_info(self.device)
