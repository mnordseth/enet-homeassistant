"""Support for Enet sensors."""

from __future__ import annotations
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)

from homeassistant.const import (
    LIGHT_LUX,
    EntityCategory,
    UnitOfEnergy,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)  # , UnitOfReactivePower

from custom_components.enet.enet_data.enums import (
    ChannelApplicationMode,
    ChannelTypeFunctionName,
    DeviceBatteryState,
)

from .entity import EnetBaseChannelEntity, EnetBaseDeviceEntity
from .aioenet import SensorChannel
from .const import DOMAIN

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
                for output_function in channel.output_functions.values():
                    match output_function["function"]:
                        case ChannelTypeFunctionName.BRIGHTNESS:
                            async_add_entities(
                                [EnetLightLevelSensor(channel, hub.coordinator)]
                            )
                        case ChannelTypeFunctionName.ENERGY_ACTIVE:
                            async_add_entities(
                                [EnetEnergySensor(channel, hub.coordinator)]
                            )
                        case ChannelTypeFunctionName.VOLTAGE:
                            async_add_entities(
                                [EnetVoltageSensor(channel, hub.coordinator)]
                            )
                        case ChannelTypeFunctionName.CURRENT:
                            async_add_entities(
                                [EnetCurrentSensor(channel, hub.coordinator)]
                            )
                        case ChannelTypeFunctionName.POWER_ACTIVE:
                            async_add_entities(
                                [EnetPowerSensor(channel, hub.coordinator)]
                            )
                        case ChannelTypeFunctionName.SCENE_CONTROL:
                            # Handled as device action
                            pass
                        case _:
                            _LOGGER.debug(
                                "Unsupported output function: %s",
                                output_function["function"],
                            )

                # channel_type_brightness = channel.get_channel_configuration_entry(
                #    "outputDeviceFunctions", ChannelTypeFunctionName.BRIGHTNESS)
                # Check if brightness is supported by channel, since light sensor provides one off spec channel without brightness
                # if (
                #    channel_type_brightness is not None
                #    and channel_type_brightness in channel.current_values
                # ):
                #    async_add_entities(
                #        [EnetLightLevelSensor(channel, hub.coordinator)])

    _LOGGER.info("Finished async setup(sensor)")


class EnetPowerSensor(EnetBaseChannelEntity, SensorEntity):
    """Representation of a Enet Power Active sensor."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _enet_channel_function = ChannelTypeFunctionName.POWER_ACTIVE
    _attr_name = "Power"


class EnetCurrentSensor(EnetBaseChannelEntity, SensorEntity):
    """Representation of a Enet Current sensor."""

    _attr_native_unit_of_measurement = UnitOfElectricCurrent.MILLIAMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _enet_channel_function = ChannelTypeFunctionName.CURRENT
    _attr_name = "Current"


class EnetVoltageSensor(EnetBaseChannelEntity, SensorEntity):
    """Representation of a Enet Voltage sensor."""

    _attr_native_unit_of_measurement = UnitOfElectricPotential.MILLIVOLT
    _attr_suggested_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _enet_channel_function = ChannelTypeFunctionName.VOLTAGE
    _attr_name = "Voltage"


class EnetEnergySensor(EnetBaseChannelEntity, SensorEntity):
    """Representation of a Enet Energy sensor."""

    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _enet_channel_function = ChannelTypeFunctionName.ENERGY_ACTIVE
    _attr_name = "Total energy consumed"


class EnetLightLevelSensor(EnetBaseChannelEntity, SensorEntity):
    """Representation of a Enet LightLevel sensor."""

    _attr_native_unit_of_measurement = LIGHT_LUX
    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _enet_channel_function = ChannelTypeFunctionName.BRIGHTNESS
    _attr_name = "Light level"


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
