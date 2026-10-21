"""Constants for the Enet Smart Home integration."""

from .enums import ButtonEventType

DOMAIN = "enet"

NAME_ENET_CONTROLLER = "Enet Controller"
NAME_ENET_SERVER = "Enet Smart Home Server"

CONF_SUBTYPE = "subtype"
CONF_UNIQUE_ID = "unique_id"

ATTR_ENET_EVENT = "enet_event"
EVENT_TYPE_INITIAL_PRESS = ButtonEventType.INITIAL_PRESS.value
EVENT_TYPE_SHORT_RELEASE = ButtonEventType.SHORT_RELEASE.value
EVENT_TYPE_LONG_RELEASE = ButtonEventType.LONG_RELEASE.value
