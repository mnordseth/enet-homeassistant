"Helper functions for Enet Smart Home integration"

from homeassistant.const import (
    ATTR_IDENTIFIERS,
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    ATTR_NAME,
    ATTR_SERIAL_NUMBER,
    ATTR_SUGGESTED_AREA,
    ATTR_VIA_DEVICE,
)
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, NAME_ENET_CONTROLLER
from .enet_data.data import enet_data


def get_device_info(enet_device):
    """Return device info"""
    return DeviceInfo(
        {
            ATTR_IDENTIFIERS: {(DOMAIN, enet_device.uid)},
            ATTR_NAME: enet_device.name,
            ATTR_MANUFACTURER: enet_data.get_manufacturer_name_from_device_type_id(enet_device.device_type),
            ATTR_MODEL: f"{enet_device.device_type} ({enet_data.get_device_name_from_device_type_id(enet_device.device_type)})",
            ATTR_SERIAL_NUMBER: enet_device.serial_number,
            ATTR_SUGGESTED_AREA: enet_device.location.partition(":")[2],
            ATTR_VIA_DEVICE: (DOMAIN, NAME_ENET_CONTROLLER),
        }
    )