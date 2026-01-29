"""Support for Enet binary sensors."""

from __future__ import annotations

import logging
import asyncio

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from custom_components.enet.enet_data.enums import (
    ChannelApplicationMode,
    ChannelTypeFunctionName,
)

from .aioenet import SensorChannel
from .const import DOMAIN
from .entity import EnetBaseChannelEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet binary sensor devices from a config entry."""
    hub = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []

    for device in hub.devices:
        for channel in device.channels:
            if not isinstance(channel, SensorChannel):
                continue

            # eNet motion detectors use MOVEMENT application mode and typically
            # report triggers via TRIGGER_START.
            if channel.application_mode == ChannelApplicationMode.MOVEMENT:
                for output_function in channel.output_functions.values():
                    if (
                        output_function["function"]
                        == ChannelTypeFunctionName.TRIGGER_START
                    ):
                        entities.append(
                            EnetMotionBinarySensor(channel, hub.coordinator)
                        )

    if entities:
        async_add_entities(entities)

    _LOGGER.info("Finished async setup(binary_sensor)")


class EnetMotionBinarySensor(EnetBaseChannelEntity, BinarySensorEntity):
    """Representation of an Enet motion trigger as a binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_name = "Motion"
    _enet_channel_function = ChannelTypeFunctionName.TRIGGER_START

    @property
    def is_on(self) -> bool | None:
        """Return true if motion is detected."""
        value = self.native_value
        if value is None:
            return None
        try:
            return bool(int(value))
        except (TypeError, ValueError):
            return bool(value)

    def __init__(self, channel, coordinator):
        super().__init__(channel, coordinator)
        # Do not use value supplied by enet server on startup, only react to events
        self.update_value(0)
        self._clear_motion_task = None

    async def on_value_updated(self):
        self.async_write_ha_state()

        if self.is_on:
            wait = self.native_value
            _LOGGER.debug(
                "%s: Motion detected, clearing in %s seconds",
                self.name,
                wait,
            )
            if self._clear_motion_task is not None:
                self._clear_motion_task.cancel()
            self._clear_motion_task = asyncio.create_task(self.clear_motion(wait))

    async def clear_motion(self, wait=10):
        await asyncio.sleep(wait)
        _LOGGER.debug("%s: Clearing motion", self.name)
        self.update_value(0)
        self.async_write_ha_state()
