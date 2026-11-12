"""Constants for the Enet Smart Home integration."""

from .enums import ButtonEventType

DOMAIN = "enet"

NAME_ENET_CONTROLLER = "Enet Controller"
NAME_ENET_SERVER = "Enet Smart Home Server"

CONF_SUBTYPE = "subtype"
CONF_UNIQUE_ID = "unique_id"

# Option: which scenes of the Enet server are added
CONF_SCENES = "scenes"
SCENES_LIBENET = "libenet"  # only scenes with libenet in the name
SCENES_ALL = "all"  # all scenes made in the Enet app
SCENES_ALL_WITH_LOCATION = "all_with_location"  # and the automatic scenes per location
DEFAULT_SCENES = SCENES_LIBENET

ATTR_ENET_EVENT = "enet_event"
EVENT_TYPE_INITIAL_PRESS = ButtonEventType.INITIAL_PRESS.value
EVENT_TYPE_SHORT_RELEASE = ButtonEventType.SHORT_RELEASE.value
EVENT_TYPE_LONG_RELEASE = ButtonEventType.LONG_RELEASE.value
