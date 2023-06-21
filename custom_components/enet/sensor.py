"""Support for Enet sensors."""
from __future__ import annotations
import logging


from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)

from homeassistant.const import LIGHT_LUX, PERCENTAGE, EntityCategory
from .entity import EnetBaseEntity
from .aioenet import ActuatorChannel
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet sensor devices from a config entry"""
    hub = hass.data[DOMAIN][entry.entry_id]

    for device in hub.devices:
        for channel in device.channels:
            if (
                isinstance(channel, ActuatorChannel)
                and channel.ha_domain == "light level"
            ):
                async_add_entities([EnetLightLevelSensor(channel, hub.coordinator)])
    _LOGGER.info("Finished async setup(sensor)")


class EnetLightLevelSensor(EnetBaseEntity, SensorEntity):
    """Representation of a Hue LightLevel (illuminance) sensor."""

    _attr_native_unit_of_measurement = LIGHT_LUX
    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the value reported by the sensor."""
        return self.channel.state


class EnetBatterySensor(EnetBaseEntity, SensorEntity):
    """Representation of a Enet Battery sensor."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the value reported by the sensor."""
        return None  # self.resource.power_state.battery_level

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return None  # {"battery_state": self.resource.power_state.battery_state.value}
