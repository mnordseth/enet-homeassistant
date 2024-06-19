from enum import StrEnum, IntEnum, unique

@unique
class ChannelTypeUseType(StrEnum):
    ACTUATOR = "UT_ACTUATOR"
    NONE = "UT_NONE"
    SENSOR = "UT_SENSOR"
    SENSOR_ACTUATOR = "UT_SENSOR_ACTUATOR" # Tado

@unique
class ChannelTypeSubSectionType(StrEnum):
    BLINDS = "SST_BLINDS"
    HVAC = "SST_HVAC" # Tado
    LIGHT = "SST_LIGHT"
    NONE = "SST_NONE"

@unique
class ChannelApplicationMode(StrEnum):
    BLINDS = "BLINDS"
    ENERGY = "ENERGY"
    HVAC = "HVAC" # Tado
    LIGHT_DIMMING = "LIGHT_DIMMING"
    LIGHT_SWITCHING = "LIGHT_SWITCHING"
    LIGHT_SWITCHING_SLAVE = "LIGHT_SWITCHING_SLAVE"
    MOVEMENT = "MOVEMENT"
    ROCKER = "ROCKER"
    SCENE = "SCENE"
    SWITCHING = "SWITCHING"
    SWITCHING_SLAVE = "SWITCHING_SLAVE"
    UNUSED = "UNUSED"

@unique
class DeviceTypeArticleGroup(StrEnum):
    ACTUATOR = "AG_ACTUATORS"
    COVER = "AG_COVERS"
    MOTION_DETECTOR = "AG_MOTION_DETECTORS"
    SENDER = "AG_SENDERS"
    SENSOR = "AG_SENSORS"
    SYSTEM_DEVICE = "AG_SYSTEM_DEVICES"

@unique
class ChannelTypeFunctionName(StrEnum):
    APPLICATION_MODE = "applicationMode"
    APPLICATION_MODE_SWITCH = "applicationModeSwitch"
    ANTI_LOCKOUT_STATE = "antiLockoutState"
    BLOCKED_STATE = "blockedState"
    BRIGHTNESS = "brightness"
    BUTTON_ROCKER = "buttonRocker"
    COVER_POSITION = "coverPosition"
    COVER_TILT_POSITION = "coverTiltPosition"
    CURRENT = "current"
    ENERGY_ACTIVE = "energyActive"
    FORCED_STATE = "forcedState"
    MASTER_DIMMING = "masterDimming"
    ON_OFF = "onOff"
    OPERATION_MODE = "operationMode"
    POWER_ACTIVE = "powerActive"
    POWER_APPARENT = "powerApparent"
    POWER_REACTIVE = "powerReactive"
    SCENE_CONTROL = "sceneControl"
    STEP_UP_DOWN = "stepUpDown"
    STOP = "stop"
    TILT_POSITION = "tiltPosition"
    TRIGGER_START = "triggerStart"
    UP_DOWN = "upDown"
    VOLTAGE = "voltage"
