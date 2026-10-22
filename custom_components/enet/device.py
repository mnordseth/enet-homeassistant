"Representation of a Enet device in Home Assistant"

import logging

from homeassistant.helpers import device_registry
from .const import DOMAIN, NAME_ENET_CONTROLLER, NAME_ENET_SERVER
from .utils import get_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_devices(hass, entry, hub):
    """Manage setup of devices from Enet devices."""
    dev_reg = device_registry.async_get(hass)

    # The controller is the parent (via device) of all Enet devices
    controller = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, NAME_ENET_CONTROLLER)},
        name=NAME_ENET_SERVER,
    )

    # create/update all current devices found in controller ensuring devices that only generate events also gets registered
    for enet_device in hub.devices:
        _LOGGER.debug("add_device() %s", enet_device)
        enet_device.hass_device_entry = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            via_device_id=controller.id,
            **get_device_info(enet_device),
        )
