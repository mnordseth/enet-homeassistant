"Representation of a Enet device in Home Assistant"

import logging
from homeassistant.const import (
    ATTR_IDENTIFIERS,
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    ATTR_NAME,
    ATTR_SERIAL_NUMBER,
    ATTR_SUGGESTED_AREA,
    ATTR_VIA_DEVICE,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry
from .aioenet import ActuatorChannel
from . import enet_devices
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_devices(coordinator):
    """Manage setup of devices from Hue devices."""
    entry = coordinator.config_entry
    hass = coordinator.hass
    dev_reg = device_registry.async_get(hass)

    @callback
    def add_device(enet_device):
        """Register a Enet device in device registry."""
        _LOGGER.debug("add_device() %s", enet_device)
        device_info = enet_devices.device_info.get(enet_device.device_type)
        params = {
            ATTR_IDENTIFIERS: {(DOMAIN, enet_device.uid)},
            ATTR_NAME: enet_device.name,
            ATTR_MODEL: f"{enet_device.device_type} ({device_info.get('description')})",
            ATTR_MANUFACTURER: device_info.get("manufacturer"),
            ATTR_SERIAL_NUMBER: enet_device.serial_number,
            ATTR_SUGGESTED_AREA: enet_device.location.replace("My home:", ""),
            ATTR_VIA_DEVICE: (DOMAIN, "Enet Controller"),
        }
        return dev_reg.async_get_or_create(config_entry_id=entry.entry_id, **params)

    # create/update all current devices found in controller
    for enet_device in coordinator.hub.devices:
        if all([not isinstance(c, ActuatorChannel) for c in enet_device.channels]):
            hass_device_entry = add_device(enet_device)
            enet_device.hass_device_entry = hass_device_entry
