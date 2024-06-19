from enum import StrEnum, unique

@unique
class ChannelUseType(StrEnum):
    ACTUATOR = "ACTUATOR"
    SENSOR = "SENSOR"
    IGNORED = "IGNORED"
    UNSUPPORTED = "UNSUPPORTED"