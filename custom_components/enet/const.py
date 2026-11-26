"""Constants for the Enet Smart Home integration."""

from .enums import ButtonEventType

DOMAIN = "enet"

NAME_ENET_CONTROLLER = "Enet Controller"
NAME_ENET_SERVER = "Enet Smart Home Server"

CONF_SUBTYPE = "subtype"
CONF_UNIQUE_ID = "unique_id"
# Serial number of the Enet server, from discovery
CONF_SERIAL_NUMBER = "serial_number"

# Option: also add the lights/blinds on/off scenes the server makes for each
# location. The other scenes are always added.
CONF_LOCATION_SCENES = "location_scenes"

ATTR_ENET_EVENT = "enet_event"
EVENT_TYPE_INITIAL_PRESS = ButtonEventType.INITIAL_PRESS.value
EVENT_TYPE_SHORT_RELEASE = ButtonEventType.SHORT_RELEASE.value
EVENT_TYPE_LONG_RELEASE = ButtonEventType.LONG_RELEASE.value
