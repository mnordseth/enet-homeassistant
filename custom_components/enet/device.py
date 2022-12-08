"Representation of a Enet device in Home Assistant"

import logging
from homeassistant.const import (
    ATTR_CONNECTIONS,
    ATTR_IDENTIFIERS,
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    ATTR_NAME,
    ATTR_SUGGESTED_AREA,
    ATTR_SW_VERSION,
    ATTR_VIA_DEVICE,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry
from .aioenet import Actuator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_devices(coordinator):
    """Manage setup of devices from Hue devices."""
    entry = coordinator.config_entry
    hass = coordinator.hass
    dev_reg = device_registry.async_get(hass)
    # dev_controller = api.devices

    @callback
    def add_device(enet_device):
        """Register a Hue device in device registry."""
        _LOGGER.debug("add_device() %s", enet_device)
        params = {
            ATTR_IDENTIFIERS: {(DOMAIN, enet_device.uid)},
            # ATTR_SW_VERSION: hue_device.product_data.software_version,
            ATTR_NAME: enet_device.name,
            ATTR_MODEL: enet_device.device_type,
            # ATTR_MANUFACTURER: hue_device.product_data.manufacturer_name,
        }
        # if room := dev_controller.get_room(hue_device.id):
        params[ATTR_SUGGESTED_AREA] = enet_device.location.replace("My home:", "")
        # if hue_device.metadata.archetype == DeviceArchetypes.BRIDGE_V2:
        #    params[ATTR_IDENTIFIERS].add((DOMAIN, api.config.bridge_id))
        # else:
        #    params[ATTR_VIA_DEVICE] = (DOMAIN, api.config.bridge_device.id)

        return dev_reg.async_get_or_create(config_entry_id=entry.entry_id, **params)

    @callback
    def remove_device(hue_device_id: str) -> None:
        """Remove device from registry."""
        if device := dev_reg.async_get_device({(DOMAIN, hue_device_id)}):
            # note: removal of any underlying entities is handled by core
            dev_reg.async_remove_device(device.id)

    @callback
    def handle_device_event(evt_type, hue_device):
        """Handle event from Hue devices controller."""
        pass
        # if evt_type == EventType.RESOURCE_DELETED:
        #    remove_device(hue_device.id)
        # else:
        #    # updates to existing device will also be handled by this call
        #    add_device(hue_device)

    # create/update all current devices found in controller
    for enet_device in coordinator.hub.devices:
        if not isinstance(enet_device, Actuator):
            add_device(enet_device)
    # known_devices = [add_device(hue_device) for hue_device in dev_controller]

    # Check for nodes that no longer exist and remove them
    # for device in device_registry.async_entries_for_config_entry(
    #    dev_reg, entry.entry_id
    # ):
    #    if device not in known_devices:
    #        # handle case where a virtual device was created for a Hue group
    ##        hue_dev_id = next(x[1] for x in device.identifiers if x[0] == DOMAIN)
    #       if hue_dev_id in api.groups:
    #            continue
    #        dev_reg.async_remove_device(device.id)

    # add listener for updates on Hue devices controller
    # entry.async_on_unload(dev_controller.subscribe(handle_device_event))
