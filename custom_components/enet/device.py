"Representation of a Enet device in Home Assistant"

import logging

from homeassistant.core import callback
from homeassistant.helpers import device_registry
from .aioenet import ActuatorChannel
from .utils import get_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_devices(coordinator):
    """Manage setup of devices from Enet devices."""
    entry = coordinator.config_entry
    hass = coordinator.hass
    dev_reg = device_registry.async_get(hass)

    @callback
    def add_device(enet_device):
        """Register a Enet device in device registry."""
        _LOGGER.debug("add_device() %s", enet_device)
        params = get_device_info(enet_device)
        return dev_reg.async_get_or_create(config_entry_id=entry.entry_id, **params)

    # create/update all current devices found in controller ensuring devices that only generate events also gets registered
    for enet_device in coordinator.hub.devices:
        hass_device_entry = add_device(enet_device)
        enet_device.hass_device_entry = hass_device_entry
