"Forward Enet button events to the Home Assistant event bus"

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .aioenet import ButtonEvent, EnetClient
from .const import ATTR_ENET_EVENT


@callback
def async_setup_enet_events(
    hass: HomeAssistant, entry: ConfigEntry, hub: EnetClient
) -> None:
    """Fire an enet_event on the hass bus for every button event.

    The subscription is removed when the config entry is unloaded.
    """

    @callback
    def handle_event(event) -> None:
        if not isinstance(event, ButtonEvent):
            return
        bus_data = {
            "device_id": event.channel.device.hass_device_entry.id,
            "unique_id": event.channel.device.uid,
            "type": str(event.type),
            "subtype": str(event.subtype),
        }
        hass.bus.async_fire(ATTR_ENET_EVENT, bus_data)

    entry.async_on_unload(hub.subscribe(handle_event))
