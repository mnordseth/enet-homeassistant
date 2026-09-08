"Helper functions for Enet Smart Home integration"

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.device_registry import async_get_device_id_by_identifier

from .const import DOMAIN, NAME_ENET_CONTROLLER
from .enet_data.data import enet_data


def get_device_info(enet_device, coordinator):
    """Return device info"""
    return DeviceInfo(
        identifiers={(DOMAIN, enet_device.uid)},
        name=enet_device.name,
        manufacturer=enet_data.get_manufacturer_name_from_device_type_id(
            enet_device.device_type
        ),
        model=f"{enet_device.device_type} ({enet_data.get_device_name_from_device_type_id(enet_device.device_type)})",
        serial_number=enet_device.serial_number,
        suggested_area=enet_device.location.partition(":")[2],
        # ATTR_VIA_DEVICE: (DOMAIN, NAME_ENET_CONTROLLER),
        via_device_id=async_get_device_id_by_identifier(
            coordinator.hass,
            (DOMAIN, NAME_ENET_CONTROLLER),
            config_entry_id=coordinator.config_entry.entry_id,
        ),
    )
