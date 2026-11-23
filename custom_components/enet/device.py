"Representation of a Enet device in Home Assistant"

from collections import defaultdict
import logging

from homeassistant.helpers import area_registry, device_registry, floor_registry
from homeassistant.helpers.storage import Store

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

    await async_assign_floors(hass, entry, hub)


def floors_store(hass, entry_id):
    """The areas that were put on floors, per config entry"""
    return Store(hass, 1, f"{DOMAIN}_floors_{entry_id}")


async def async_assign_floors(hass, entry, hub):
    """Put the areas of the Enet devices on the floors of the Enet server.

    An area gets the floor of its Enet devices when it has no floor and all
    of them are on the same floor; the floor is created if needed. Each area
    is handled once, so a floor the user changes or removes is kept.
    """
    store = floors_store(hass, entry.entry_id)
    handled = set(await store.async_load() or [])
    floors_of_area = defaultdict(set)
    for enet_device in hub.devices:
        device = getattr(enet_device, "hass_device_entry", None)
        if device is not None and device.area_id and enet_device.floor:
            floors_of_area[device.area_id].add(enet_device.floor)

    areas = area_registry.async_get(hass)
    floors = floor_registry.async_get(hass)
    new = [area_id for area_id in floors_of_area if area_id not in handled]
    for area_id in new:
        area = areas.async_get_area(area_id)
        if area is None or area.floor_id or len(floors_of_area[area_id]) != 1:
            continue
        name = next(iter(floors_of_area[area_id]))
        floor = floors.async_get_floor_by_name(name) or floors.async_create(name)
        _LOGGER.debug("Putting the area %s on the floor %s", area.name, name)
        areas.async_update(area_id, floor_id=floor.floor_id)
    if new:
        await store.async_save(sorted(handled | set(new)))
