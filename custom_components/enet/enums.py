"""Enums for enet integration."""

from enum import StrEnum, unique

@unique
class ChannelUseType(StrEnum):
    """Enum for the use type of a channel."""
    ACTUATOR = "ACTUATOR"
    SENSOR = "SENSOR"
    IGNORED = "IGNORED"
    UNSUPPORTED = "UNSUPPORTED"


@unique
class ButtonEventType(StrEnum):
    """Enum for the type of a button event."""
    INITIAL_PRESS = "initial_press"
    SHORT_RELEASE = "short_release"
    LONG_RELEASE = "long_release"
