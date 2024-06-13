from enum import StrEnum, unique

@unique
class ChannelTypeUseType(StrEnum):
    ACTUATOR = "UT_ACTUATOR"
    NONE = "UT_NONE"
    SENSOR = "UT_SENSOR"
    SENSOR_ACTUATOR = "UT_SENSOR_ACTUATOR" # Tado

@unique
class ChannelTypeSubSectionType(StrEnum):
    BLINDS: "SST_BLINDS"
    HVAC: "SST_HVAC" # Tado
    LIGHT: "SST_LIGHT"
    NONE: "SST_NONE"

@unique
class ChannelApplicationMode(StrEnum):
    UNUSED: 0
    SWITCHING: 1
    LIGHT_SWITCHING: 3
    LIGHT_DIMMING: 4
    BLINDS: 5
    ROCKER: 6
    SCENE: 7
    ENERGY: 8
    MOVEMENT: 9
    HVAC: 10 # Tado
    SWITCHING_SLAVE: 11
    LIGHT_SWITCHING_SLAVE: 12

@unique
class DeviceTypeArticleGroup(StrEnum):
    ACTUATOR = "AG_ACTUATORS"
    COVER = "AG_COVERS"
    MOTION_DETECTOR = "AG_MOTION_DETECTORS"
    SENDER = "AG_SENDERS"
    SENSOR = "AG_SENSORS"
    SYSTEM_DEVICE = "AG_SYSTEM_DEVICES"
