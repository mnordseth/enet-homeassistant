"""Support for Enet sensors."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import LIGHT_LUX, EntityCategory

from custom_components.enet.enet_data.enums import (
    ChannelApplicationMode,
    ChannelTypeFunctionName,
    DeviceBatteryState,
)

from .aioenet import SensorChannel
from .const import DOMAIN
from .entity import EnetBaseChannelEntity, EnetBaseDeviceEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet sensor devices from a config entry"""
    hub = hass.data[DOMAIN][entry.entry_id]

    supported_app_modes = [
        ChannelApplicationMode.SCENE,
        ChannelApplicationMode.ENERGY,
        ChannelApplicationMode.MOVEMENT,
    ]

    for device in hub.devices:
        if device.get_battery_state() is not None:
            async_add_entities([EnetBatterySensor(device, hub.coordinator)])
        for channel in device.channels:
            if (
                isinstance(channel, SensorChannel)
                and channel.application_mode in supported_app_modes
            ):
                channel_type_brightness = channel.get_channel_configuration_entry(
                    "outputDeviceFunctions", ChannelTypeFunctionName.BRIGHTNESS
                )
                # Check if brightness is supported by channel, since light sensor provides one off spec channel without brightness
                if (
                    channel_type_brightness is not None
                    and channel_type_brightness in channel.current_values
                ):
                    async_add_entities([EnetLightLevelSensor(channel, hub.coordinator)])
    _LOGGER.info("Finished async setup(sensor)")


class EnetLightLevelSensor(EnetBaseChannelEntity, SensorEntity):
    """Representation of a Hue LightLevel (illuminance) sensor."""

    _attr_native_unit_of_measurement = LIGHT_LUX
    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the last value reported by the sensor."""
        return self.channel.get_current_value(ChannelTypeFunctionName.BRIGHTNESS)

    def update_value(self, value: int) -> None:
        """Update the value of the sensor."""
        self.channel.set_current_value(ChannelTypeFunctionName.BRIGHTNESS, value)

    async def async_added_to_hass(self):
        """Subscribe entity to updates when added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )


class EnetBatterySensor(EnetBaseDeviceEntity, SensorEntity):
    """Representation of a Enet Battery sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = [state.value for state in DeviceBatteryState]
    _attr_has_entity_name = True

    def __init__(self, device, coordinator):
        super().__init__(device, coordinator)
        self._uid = f"{self.device.uid}_battery_state"

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._uid

    @property
    def translation_key(self):
        """Return the translation key to translate the entity's name and states."""
        return "battery_state"

    @property
    def native_value(self) -> int:
        """Return the value reported by the sensor."""
        return self.device.get_battery_state()

    async def async_added_to_hass(self):
        """Subscribe entity to updates when added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
