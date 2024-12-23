"""Enums for enet integration."""

from enum import StrEnum, unique


@unique
class ChannelUseType(StrEnum):
    """Enum for the use type of a channel."""

    ACTUATOR = "ACTUATOR"
    SENSOR = "SENSOR"
    IGNORED = "IGNORED"
    UNSUPPORTED = "UNSUPPORTED"
