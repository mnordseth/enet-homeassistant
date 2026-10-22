"Helper functions for Enet Smart Home integration"

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .enet_data.data import enet_data


def get_device_info(enet_device):
    """Return device info for registering an Enet device"""
    return DeviceInfo(
        identifiers={(DOMAIN, enet_device.uid)},
        name=enet_device.name,
        manufacturer=enet_data.get_manufacturer_name_from_device_type_id(
            enet_device.device_type
        ),
        model=f"{enet_device.device_type} ({enet_data.get_device_name_from_device_type_id(enet_device.device_type)})",
        serial_number=enet_device.serial_number,
        suggested_area=enet_device.location.partition(":")[2],
    )
