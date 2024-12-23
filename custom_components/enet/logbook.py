"""Describe enet logbook events."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.const import CONF_DEVICE_ID, CONF_ID, CONF_TYPE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import ATTR_ENET_EVENT, CONF_SUBTYPE, DOMAIN

TRIGGER_SUBTYPE = {
    "1": "Button 1",
    "2": "Button 2",
    "3": "Button 3",
    "4": "Button 4",
    "5": "Button 5",
    "6": "Button 6",
    "7": "Button 7",
    "8": "Button 8",
}
TRIGGER_TYPE = {
    "initial_press": "'{subtype}' pressed initially",
    "short_release": "'{subtype}' released after short press",
    "long_release": "'{subtype}' released after long press",
}

UNKNOWN_TYPE = "unknown type"
UNKNOWN_SUB_TYPE = "unknown sub type"


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe enet logbook events."""

    @callback
    def async_describe_enet_event(event: Event) -> dict[str, str]:
        """Describe enet logbook event."""
        data = event.data
        name: str | None = None
        if dev_ent := dr.async_get(hass).async_get(data[CONF_DEVICE_ID]):
            name = dev_ent.name
        if name is None:
            name = data[CONF_ID]
        if CONF_TYPE in data:  # v2
            subtype = TRIGGER_SUBTYPE.get(str(data[CONF_SUBTYPE]), UNKNOWN_SUB_TYPE)
            message = TRIGGER_TYPE.get(data[CONF_TYPE], UNKNOWN_TYPE).format(
                subtype=subtype
            )
        return {
            LOGBOOK_ENTRY_NAME: name,
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    async_describe_event(DOMAIN, ATTR_ENET_EVENT, async_describe_enet_event)
