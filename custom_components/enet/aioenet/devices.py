"""Enet devices and their channels, created from the device data of the server"""

import logging

from typing import Any, Dict, Union


from ..enums import ChannelUseType
from ..enet_data.data import enet_data
from ..enet_data.constants import CHANNEL_TYPES_IGNORED
from ..enet_data.enums import (
    ChannelTypeUseType,
    ChannelTypeSubSectionType,
    ChannelApplicationMode,
    ChannelTypeFunctionName,
    DeviceBatteryState,
)
from ..enet_data.channel_mapping import CHANNEL_TYPE_CONFIGURATION
from ..enet_data.utils import getitem_from_dict

from .const import URL

log = logging.getLogger(__name__)


def create_device(client, raw):
    """Create an enet Actuator or Sensor depending on its type"""
    device_type = raw["typeID"]
    info = enet_data.get_device_type_by_id(device_type)
    if not info:
        log.warning(
            "Unknown device: typeID=%s name=%s", raw["typeID"], raw["installationArea"]
        )
    try:
        return Device(client, raw)
    except Exception:
        log.exception(
            "Failed to create device: typeID=%s name=%s",
            raw["typeID"],
            raw["installationArea"],
        )


class Device:
    """Physical Enet Device. Can contain any combination of actuator and sensor channels"""

    def __init__(self, client, raw):
        self.client = client
        self._raw = raw
        self.channels = []
        self.location = ""  # the path of its location, "Home:Floor:Room"
        self.room = None  # the name of its room, its floor, from the locations
        self.floor = None
        self.area_name = ""  # suggested area: the room, with the floor if the name is not unique
        self.uid = self._raw["uid"]
        self.name = self._raw["installationArea"]
        self.device_type = self._raw["typeID"]
        self.battery_state = self._raw["batteryState"]
        self.serial_number = self._raw["metaData"]["serialNumber"]
        self.software_update_available = self._raw["isSoftwareUpdateAvailable"]
        self._subscribers = []
        self.create_channels()

    def __repr__(self):
        return f"{self.__class__.__name__} Name: {self.name} Type: {self.device_type}"

    def create_channels(self):
        """Create channels"""
        log.debug(
            "Enet Device %s type %s has the following channels:",
            self.name,
            self.device_type,
        )

        for channel_config_group in self._raw["deviceChannelConfigurationGroups"]:
            for device_channel in channel_config_group["deviceChannels"]:
                channel_use_type = self.get_channel_use_type(device_channel)
                channel_type_id = device_channel["channelTypeID"]

                match channel_use_type:
                    case ChannelUseType.ACTUATOR:
                        channel = ActuatorChannel(self, device_channel)
                        if channel.active:
                            self.channels.append(channel)
                    case ChannelUseType.SENSOR:
                        channel = SensorChannel(self, device_channel)
                        if channel.active:
                            self.channels.append(channel)
                    case ChannelUseType.IGNORED:
                        continue
                    case ChannelUseType.UNSUPPORTED:
                        log.warning(
                            "Unsupported enet channel type: %s", channel_type_id
                        )
                        continue

                log.debug(
                    "  Channel no: %s Type: %s Area: %s Meta: %s Output functions: %s",
                    device_channel["no"],
                    device_channel["channelTypeID"],
                    device_channel["effectArea"],
                    CHANNEL_TYPE_CONFIGURATION.get(channel_type_id),
                    channel.output_functions,
                )

    def get_channel_use_type(self, device_channel):
        """Determine if the channel is an actuator or a sensor"""
        channel_type_id = device_channel["channelTypeID"]
        channel_type_config = CHANNEL_TYPE_CONFIGURATION.get(channel_type_id)

        if channel_type_id in CHANNEL_TYPES_IGNORED:
            return ChannelUseType.IGNORED

        # CT_1F01_DUMMY has type None, but needs to be supported as well
        channel_meta_data = enet_data.get_channel_meta_data_from_channel_type(
            channel_type_id
        )
        if channel_meta_data.get("useTypeID", "") == ChannelTypeUseType.ACTUATOR:
            if channel_meta_data.get("subSectionTypeID", "") in {
                ChannelTypeSubSectionType.BLINDS,
                ChannelTypeSubSectionType.LIGHT,
            }:
                return ChannelUseType.ACTUATOR
            else:  # Special case for CT_1F19 energy sensor
                return ChannelUseType.SENSOR
        elif channel_meta_data.get("useTypeID", "") == ChannelTypeUseType.SENSOR:
            return ChannelUseType.SENSOR

        if channel_type_config is None:
            return ChannelUseType.UNSUPPORTED

        return ChannelUseType.UNSUPPORTED

    def subscribe(self, callback):
        """Subscribe to device updates, currently battery state changes.
        The callback is called without arguments after the device is updated.
        Returns a function that unsubscribes the callback.
        """
        self._subscribers.append(callback)

        def unsubscribe():
            self._subscribers.remove(callback)

        return unsubscribe

    def get_battery_state(self):
        return self.battery_state

    def update_battery_state(self, battery_state):
        if battery_state in [state.value for state in DeviceBatteryState]:
            log.debug(
                "Updating battery state for %s from %s to %s",
                self.name,
                self.battery_state,
                battery_state,
            )
            self.battery_state = battery_state
            for callback in list(self._subscribers):
                callback()
        else:
            log.warning("Unknown battery state: %s", battery_state)

    def get_function_uids_for_event(self):
        """Return a list of function uids we should setup event listeners for"""
        function_uids = {}
        for channel in self.channels:
            function_uids.update(channel.get_function_uids_for_event())
        return function_uids

    async def register_events(self):
        """Setup event subscription for all outputfunctions"""
        for function_uid, desc in self.get_function_uids_for_event().items():
            log.debug(
                "Register event subscription for %s - %s (%s)",
                self.name,
                function_uid,
                desc,
            )
            await self.client.setup_event_subscription(function_uid)


class DeviceChannel:
    """A generic class representing a device channel"""

    def __init__(self, device, raw_channel):
        self.device = device
        self.channel = raw_channel
        self.active = False
        self.uid = f"{self.device.uid}-{self.channel['no']}"
        self.channel_type = self.channel["channelTypeID"]
        self.application_mode = ChannelApplicationMode.UNUSED
        self.name = self.channel["effectArea"]
        self.current_values = {}
        self.parameter_values = {}
        self.input_functions = {}
        self.output_functions = {}
        self.device_parameters = {}
        self.on_update_callbacks = []
        self._find_output_functions()
        self._find_input_functions()
        self._find_device_parameters()
        self._set_application_mode()
        self._set_active()

    def subscribe(self, callback):
        """Subscribe to value updates. The callback is awaited without
        arguments after current_values has been updated from an event.
        Returns a function that unsubscribes the callback.
        """
        self.on_update_callbacks.append(callback)

        def unsubscribe():
            self.on_update_callbacks.remove(callback)

        return unsubscribe

    def _find_output_functions(self):
        device_output_function_list = self._get_mapped_type_ids("outputDeviceFunctions")
        for output_function in self.channel["outputDeviceFunctions"]:
            type_id = output_function.get("typeID")
            uid = output_function.get("uid")
            if type_id in device_output_function_list and output_function.get("active"):
                type_name = enet_data.get_output_device_function_name(type_id)
                function_name = device_output_function_list.get(type_id)
                name = f"Channel {self.name} - {type_name}"

                self.output_functions[type_id] = dict(
                    uid=uid, typeID=type_id, function=function_name, name=name
                )

                self.current_values[type_id] = self._get_current_value_from_dict(
                    output_function
                )

    def _find_input_functions(self) -> None:
        device_input_function_list = self._get_mapped_type_ids("inputDeviceFunctions")
        for input_function in self.channel["inputDeviceFunctions"]:
            type_id = input_function.get("typeID")
            if type_id in device_input_function_list and input_function.get("active"):
                type_name = enet_data.get_input_device_function_name(type_id)
                function_name = device_input_function_list.get(type_id)
                input_template = (
                    enet_data.get_value_template_from_input_device_function(type_id)
                )
                name = f"Channel {self.name} - {type_name}"

                self.input_functions[type_id] = {
                    "uid": input_function["uid"],
                    "typeID": input_function["typeID"],
                    "function": function_name,
                    "name": name,
                    "template": input_template,
                }

    def _get_output_function_by_uid(self, uid: str) -> Union[dict, None]:
        for output_function in self.output_functions.values():
            if output_function["uid"] == uid:
                return output_function

    def get_function_uids_for_event(self) -> dict:
        """Return a list of function uids we should setup event listeners for"""
        function_uids = {}
        for output_function in self.output_functions.values():
            if "uid" in output_function:
                function_uids[output_function["uid"]] = self
        return function_uids

    def _get_mapped_type_ids(self, list_name) -> dict:
        channel_map = getitem_from_dict(
            CHANNEL_TYPE_CONFIGURATION.get(self.channel_type), [list_name]
        )
        return {value: key for key, value in channel_map.items()}

    def get_channel_type_function_name_from_output_function_uid(self, uid: str) -> str:
        output_function = self._get_output_function_by_uid(uid)
        if output_function is not None:
            channel_map = self._get_mapped_type_ids("outputDeviceFunctions")
            return channel_map.get(output_function["typeID"])

    def _find_device_parameters(self):
        device_parameter_list = self._get_mapped_type_ids("deviceParameters")
        for device_parameter in self.channel["deviceParameters"]:
            if device_parameter["typeID"] in device_parameter_list.keys():
                if device_parameter.get("active", False) is True:
                    type_name = enet_data.get_device_parameter_name(
                        device_parameter.get("typeID")
                    )
                    name = f"Parameter {self.name} - {type_name}"

                    self.device_parameters[device_parameter.get("typeID")] = {
                        "uid": device_parameter.get("uid"),
                        "typeID": device_parameter.get("typeID"),
                        "name": name,
                    }

                    self.parameter_values[device_parameter.get("typeID")] = (
                        self._get_current_value_from_dict(device_parameter)
                    )

    def _get_parameter_value(self, channel_param_name: ChannelTypeFunctionName) -> Any:
        device_param_id = self.get_channel_configuration_entry(
            "deviceParameters", channel_param_name
        )
        if device_param_id is not None:
            param_value = self.parameter_values.get(device_param_id, None)
            if param_value is not None:
                return param_value.get("value")

    def _get_current_value_from_dict(self, function_object) -> Union[dict, list]:
        current_values = getitem_from_dict(function_object, ["currentValues"])
        if len(current_values) == 1:
            return self._parse_value(current_values[0])
        else:
            return [self._parse_value(value) for value in current_values]

    def _parse_value(self, value_to_parse: dict) -> dict:
        value = value_to_parse.get("value", None)
        value_type_id = value_to_parse.get("valueTypeID", "")

        if value is not None and value_type_id != "":
            return {"value": value, "valueTypeID": value_type_id}
        else:
            log.error("Invalid value: %s", value_type_id)

    def _set_application_mode(self):
        application_mode_value = self._get_parameter_value(
            ChannelTypeFunctionName.APPLICATION_MODE
        )
        if application_mode_value is not None:
            self.application_mode = ChannelApplicationMode[application_mode_value]

    def _set_active(self) -> bool:
        if self.application_mode != ChannelApplicationMode.UNUSED and (
            len(self.output_functions) > 0 or len(self.input_functions) > 0
        ):
            self.active = True

    def get_channel_configuration_entry(
        self, config_group: str, config_param: ChannelTypeFunctionName
    ) -> str:
        """Return the configuration for a specific config parameter"""
        return getitem_from_dict(
            CHANNEL_TYPE_CONFIGURATION, [self.channel_type, config_group, config_param]
        )

    async def update_values(self, function_uid: str, values: Dict) -> None:
        """Update current values with data from event"""
        if len(values) != 1:
            log.warning(
                "Event for device '%s' has multiple values: %s, expected 1.",
                function_uid,
                values,
            )
        else:
            output_function = self._get_output_function_by_uid(function_uid)
            if output_function is not None:
                for value_container in values:
                    new_value = self._parse_value(value_container)
                    self.current_values[output_function.get("typeID")] = new_value
                    log.debug("Updating value of %s to %s", self.name, new_value)
                for callback in list(self.on_update_callbacks):
                    await callback()

    def get_current_value(self, channel_function_name: ChannelTypeFunctionName) -> Any:
        """Return the last known value of the channel function, None if unknown"""
        channel_config_id = self.get_channel_configuration_entry(
            "outputDeviceFunctions", channel_function_name
        )
        value_container = self.current_values.get(channel_config_id, {})
        return value_container.get("value", None)

    def set_current_value(
        self, channel_function_name: ChannelTypeFunctionName, value=None
    ) -> None:
        """Set channel to new value"""
        channel_config_id = self.get_channel_configuration_entry(
            "outputDeviceFunctions", channel_function_name
        )
        if value is not None:
            self.current_values.setdefault(channel_config_id, {})["value"] = value


class SensorChannel(DeviceChannel):
    """A class representing a sensor channel"""

    def __init__(self, device, raw_channel):
        super().__init__(device, raw_channel)
        log.debug("Enet sensor channel type %s initialized", self.channel_type)

    def __repr__(self):
        return f"{self.__class__.__name__} (Name: {self.name} Type: {self.channel_type}"


class ActuatorChannel(DeviceChannel):
    """A class representing a actuator channel that can dim or switch a load"""

    def get_operation_mode(self):
        """Return the operation mode parameter of the actuator"""
        return self._get_parameter_value(ChannelTypeFunctionName.OPERATION_MODE)

    async def get_value(self, channel_function_name: ChannelTypeFunctionName) -> Any:
        """Fetch the updated state from the sever"""
        channel_config_id = self.get_channel_configuration_entry(
            "outputDeviceFunctions", channel_function_name
        )
        channel_config = self.output_functions.get(channel_config_id)
        if channel_config is not None:
            params = {"deviceFunctionUID": channel_config["uid"]}
            result = await self.device.client.request(
                URL.VISUALIZATION, "getCurrentValuesFromOutputDeviceFunction", params
            )

            current_value = self._get_current_value_from_dict(result)
            log.info("%s get_value() returned %s", self.name, current_value)
            return current_value.get("value")

    async def set_value(
        self, channel_function_name: ChannelTypeFunctionName, value=None
    ) -> None:
        """Set channel to new value"""
        channel_config_id = self.get_channel_configuration_entry(
            "inputDeviceFunctions", channel_function_name
        )
        channel_config = self.input_functions.get(channel_config_id)
        input_function_uid = channel_config.get("uid", "")

        params = {"deviceFunctionUID": input_function_uid, "values": []}

        value_template = channel_config.get("template", []).copy()

        if value is not None:
            # try to cast to correct type...
            _type = type(value_template[0]["value"])
            casted_value = _type(value)
            value_template[0]["value"] = casted_value
            self.set_current_value(channel_function_name, casted_value)

        params["values"] = value_template

        await self.device.client.request(
            URL.VISUALIZATION, "callInputDeviceFunction", params
        )

    async def turn_off(self):
        "Turn off device"
        log.info("%s turn_off()", self.name)
        await self.set_value(ChannelTypeFunctionName.ON_OFF, False)

    async def turn_on(self):
        "Turn on device"
        log.info("%s turn_on()", self.name)
        await self.set_value(ChannelTypeFunctionName.ON_OFF, True)

    def __repr__(self):
        return f"{self.__class__.__name__} (Name: {self.name} Type: {self.channel_type} Values: {self.current_values})"
