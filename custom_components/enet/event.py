"""Support for Enet button events.

Every button is an event entity, like the buttons of the Hue integration.
The device triggers are independent of these entities, they listen to the
enet_event fired on the bus by events.py.
"""

from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import callback

from custom_components.enet.enet_data.enums import ChannelTypeFunctionName

from .aioenet import ButtonEvent, SensorChannel
from .entity import EnetBaseChannelEntity
from .enums import ButtonEventType

_LOGGER = logging.getLogger(__name__)

BUTTON_FUNCTIONS = {
    ChannelTypeFunctionName.BUTTON_ROCKER,
    ChannelTypeFunctionName.SCENE_CONTROL,
}


async def async_setup_entry(hass, entry, async_add_entities):
    """Add an Enet button event entity for every button of the sensor channels."""
    hub = entry.runtime_data

    entities: list[EventEntity] = []

    for device in hub.devices:
        for channel in device.channels:
            if not isinstance(channel, SensorChannel):
                continue

            functions = {f["function"] for f in channel.output_functions.values()}
            if not functions & BUTTON_FUNCTIONS:
                continue

            number = channel.channel["no"]
            entities.append(EnetButtonEvent(hub, channel, number))
            # A rocker has the up and down button on the same channel
            if ChannelTypeFunctionName.BUTTON_ROCKER in functions:
                entities.append(EnetButtonEvent(hub, channel, number + 1))

    if entities:
        async_add_entities(entities)

    _LOGGER.debug("Finished async setup(event)")


class EnetButtonEvent(EnetBaseChannelEntity, EventEntity):
    """Representation of an Enet button as an event entity."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [event_type.value for event_type in ButtonEventType]

    def __init__(self, hub, channel, subtype):
        super().__init__(channel)
        self.hub = hub
        self.subtype = subtype

    @property
    def name(self):
        """Return the button name, the same as the subtype of the device triggers.

        Channel names of buttons are generic (Push-button, Scene), so they are
        left out. Home Assistant puts the device name in front.
        """
        return f"Button {self.subtype}"

    @property
    def unique_id(self):
        """Return a unique ID."""
        return f"{self.channel.uid}-button-{self.subtype}"

    async def async_added_to_hass(self) -> None:
        """Subscribe to button events of the hub when added to hass."""
        self.async_on_remove(self.hub.subscribe(self._handle_event))

    @callback
    def _handle_event(self, event) -> None:
        if not isinstance(event, ButtonEvent):
            return
        if event.channel is not self.channel or event.subtype != self.subtype:
            return
        self._trigger_event(str(event.type))
        self.async_write_ha_state()
