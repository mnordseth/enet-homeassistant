"""async library for communicating with a Enet Smart Home server from Jung / Gira"""

import logging
import functools

from typing import Any, Dict, Union

import asyncio
import aiohttp

from .enums import ChannelUseType
from .enet_data.data import enet_data
from .enet_data.constants import CHANNEL_TYPES_IGNORED
from .enet_data.enums import (
    ChannelTypeUseType,
    ChannelTypeSubSectionType,
    ChannelApplicationMode,
    ChannelTypeFunctionName,
    DeviceBatteryState
)
from .enet_data.channel_mapping import CHANNEL_TYPE_CONFIGURATION
from .enet_data.utils import getitem_from_dict

log = logging.getLogger(__name__)

URL_MANAGEMENT = "/jsonrpc/management"
URL_VIZ = "/jsonrpc/visualization"
URL_COM = "/jsonrpc/commissioning"
URL_TELEGRAM = URL_COM + "/app_telegrams"
URL_SCENE = "/jsonrpc/visualization/app_scene"
ID_FILTER_ALL="*"


class AuthError(Exception):
    "Authentication error"


def auth_if_needed(func):
    "Decorator used to reauthenticate if we get a AuthError"

    @functools.wraps(func)
    def auth_wrapper(self, *args, **kwargs):
        "Perform re-authentication"
        try:
            return func(self, *args, **kwargs)
        except AuthError:
            log.warning("Trying to re-authenticate...")
            self.simple_login()

        return func(self, *args, **kwargs)

    return auth_wrapper


class EnetClient:
    """Client for communicating with a Enet Smart Home Server from Jung / Gira"""

    def __init__(self, url, user, passwd):
        self.user = user
        self.passwd = passwd
        if url.endswith("/"):
            url = url[:-1]
        self.baseurl = url.strip()
        jar = aiohttp.CookieJar(unsafe=True)
        self._session = aiohttp.ClientSession(cookie_jar=jar)
        self._debug_requests = False
        self._api_counter = 1
        self._cookie = ""
        self._last_telegram_ts = {}
        self._raw_json = {}
        self._projectuid = None
        self._subscribers = []
        self.function_uid_map = {}
        self.devices = []

    async def initialize(self):
        """Initialize the client"""
        await self.simple_login()
        self.devices = await self.get_devices()
        await self.initialize_events()
        asyncio.create_task(self.__read_events())

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._session.close()
        if exc_val:
            raise exc_val
        return exc_type

    async def initialize_events(self):
        """Initialize events for all output functions"""
        log.debug("Setting up event listners")
        await self.setup_event_subscription_battery_state()
        for device in self.devices:
            func_uids = device.get_function_uids_for_event()
            self.function_uid_map.update(func_uids)
            await device.register_events()


    def subscribe(self, callback, id_filter=ID_FILTER_ALL):
        """
        Subscribe to status changes
        """
        self._subscribers.append(callback)

        # unsubscribe logic
        def unsubscribe():
            self._subscribers.remove(callback)

        return unsubscribe

    async def __read_events(self):
        """
        Process events from Enet server
        """
        while True:
            event = await self.get_events()
            if event:
                await self.__handle_event(event)


    async def __handle_event(self, events):
        """Handle events from Enet Server. Either update value of actuator or
        forward event from sensor
        """
        for event in events["events"]:
            log.debug("Handling event: %s", event)
            if event["event"] == "outputDeviceFunctionCalled":
                data = event["eventData"]
                function_uid = data["deviceFunctionUID"]
                device = self.function_uid_map.get(function_uid)
                if not device:
                    log.warning(
                        "Function %s does not map to device",
                        function_uid,
                    )
                    continue
                values = data["values"]
                if isinstance(device, ActuatorChannel):
                    if len(values) != 1:
                        log.warning(
                            "Event for device '%s' has multiple values: %s, expected 1.",
                            device,
                            values,
                        )
                    for value_spec in values:
                        value = value_spec["value"]
                        log.debug("Updating value of %s to %s", device, value)
                        device.state = value


                for callback in self._subscribers:
                    callback(data, device)


    @auth_if_needed
    async def request(self, url, method, params, get_raw=False):
        "Request data from the Enet Server"
        return await self._do_request(url, method, params, get_raw)

    async def _do_request(self, url, method, params, get_raw=False):
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": str(self._api_counter),
        }
        self._api_counter += 1
        log.debug("Requesting %s%s %s", self.baseurl, url, method)
        response = await self._session.post(f"{self.baseurl}{url}", json=req, ssl=False)
        if get_raw:
            return response
        if response.status >= 400:
            log.warning(
                "Request to %s failed with status %s",
                response.request_info.url,
                response.status,
            )
            return response

        json = await response.json()
        if "error" in json:
            returned_error = json["error"]
            error_msg = f"-> {url} {method} returned error: {returned_error}"
            if returned_error["code"] in (-29998, -29997):
                log.warning("Got auth error: %s", json["error"])
                raise AuthError
            elif returned_error["code"] == -29999:
                raise aiohttp.ServerTimeoutError
            else:
                log.warning(error_msg)
                raise Exception(error_msg)
        else:
            if self._debug_requests:
                log.debug("-> %s %s returned: %s", url, method, json["result"])
        return json["result"]

    async def simple_login(self):
        """Login to the Enet Server"""
        params = dict(userName=self.user, userPassword=self.passwd)
        await self._do_request(URL_MANAGEMENT, "userLogin", params)
        await self._do_request(
            URL_MANAGEMENT, "setClientRole", dict(clientRole="CR_VISU")
        )

    async def simple_logout(self):
        """Logout of the Enet Server"""
        await self.request(URL_MANAGEMENT, "userLogout", None)
        await self._session.close()

    async def ping(self):
        """Ping server to keep connection alive"""
        return await self.request(URL_MANAGEMENT, "ping", None)

    async def get_project(self):
        """Get the project which included the projectUID and the projectName"""
        result = await self.request(URL_VIZ, "getCurrentProject", None)
        self._projectuid = result["projectUID"]
        return result

    async def get_project_information(self):
        """Get the project information which includes version numbers,
        creation and modification dates and other general information
        about the server and its configuration."""
        if self._projectuid is None:
            await self.get_project()

        params = {"projectUID": self._projectuid}
        return await self.request(URL_VIZ, "getProjectInformation", params)

    def get_account(self):
        """Return the current logged in user account"""
        return self.request(URL_MANAGEMENT, "getAccount", {})

    async def get_devices(self, device_uids=None):
        """Get all the devices registered on the server"""
        device_locations = await self.get_device_locations()
        if device_uids is None:
            device_uids = list(device_locations.keys())
        params = {
            "deviceUIDs": device_uids,
            "filter": ".+\\.(SCV1|SCV2|SNA|PSN)\\[(.|1.|2.|3.|4.|5.)\\]+",
        }
        result = await self.request(URL_VIZ, "getDevicesWithParameterFilter", params)
        raw_devices = result["devices"]
        self._raw_json = raw_devices
        devices = []
        for raw_device in raw_devices:
            device = create_device(self, raw_device)
            if not device:
                continue
            elif device.serial_number == "" or device.serial_number is None:
                # Ignore catalog devices, since they are just placeholders until devices are installed (missing serial number)
                log.info("Ignoring catalog device %s", device.name)
                continue
            device.location = device_locations.get(device.uid, "")
            devices.append(device)

        return devices

    async def get_locations(self):
        """Get all locations"""
        params = {"locationUIDs": []}
        result = await self.request(URL_VIZ, "getLocations", params)
        return result["locations"]

    async def get_all_location_uids(self):
        """Return a dict of all locations to location uid"""
        locations = await self.get_locations()
        all_location_uids = {}

        def recurse_locations(locations, parent, level=0):
            for location in locations:
                name = location["name"]
                hier_name = ":".join(parent) + ":" + name
                if level > 0:
                    all_location_uids[location["uid"]] = hier_name
                for device in location["deviceUIDs"]:
                    all_location_uids[device["deviceUID"]] = hier_name
                if location["childLocations"]:
                    parent.append(name)
                    recurse_locations(location["childLocations"], parent, level + 1)
            if parent:
                parent.pop()

        recurse_locations(locations, [])
        return all_location_uids

    async def get_device_locations(self):
        """Return a dict of locations to device"""
        locations = await self.get_locations()
        device_to_loc = {}

        def recurse_locations(locations, parent, level=0):
            for location in locations:
                name = location["name"]
                # print(":".join(parent), name, level)
                hier_name = ":".join(parent) + ":" + name
                for device in location["deviceUIDs"]:
                    # print("  ", device)
                    device_to_loc[device["deviceUID"]] = hier_name
                if location["childLocations"]:
                    parent.append(name)
                    recurse_locations(location["childLocations"], parent, level + 1)
            if parent:
                parent.pop()

        recurse_locations(locations, [])
        return device_to_loc

    async def get_scenes(self, only_libenet=True):
        """Get all scene names and corresponding uid from the server"""
        result = await self.request(URL_SCENE, "getSceneActionUIDs", None)
        scenes = {}
        for scene in result["sceneActionUIDs"]:
            scene_uid = scene["sceneActionUID"]
            scene_name = scene["sceneActionName"]
            if only_libenet and "libenet" not in scene_name:
                continue
            scenes[scene_name] = scene_uid
        return scenes

    async def activate_scene(self, scene_uid):
        """Activate the specified scene UID"""
        params = {"actionUID": scene_uid}
        await self.request(URL_VIZ, "executeAction", params)

    async def setup_event_subscription(self, func_uid):
        """Subscribe for outputDeviceFunction events"""
        result = await self.request(
            URL_VIZ,
            "registerEventOutputDeviceFunctionCalled",
            {"deviceFunctionUID": func_uid},
        )
        return result

    async def setup_event_subscription_battery_state(self):
        """Subscribe for batteryStateChanged events"""
        result = await self.request(
            URL_VIZ,
            "registerEventDeviceBatteryStateChanged",
            None,
        )
        return result

    async def get_events(self):
        """Poll Enet server for events"""
        try:
            result = await self.request(URL_VIZ, "requestEvents", None)
            return result
        except aiohttp.ServerTimeoutError:
            return


def create_device(client, raw):
    """Create an enet Actuator or Sensor depending on its type"""
    device_type = raw["typeID"]
    info = enet_data.get_device_type_by_id(device_type)
    if info is None:
        log.warning(
            "Unknown device: typeID=%s name=%s", raw["typeID"], raw["installationArea"]
        )
    try:
        return Device(client, raw)
    except Exception as e:
        log.exception("Failed to create device: typeID=%s name=%s", raw["typeID"], raw["installationArea"])



class Device:
    """Physical Enet Device. Can contain any combination of actuator and sensor channels"""

    def __init__(self, client, raw):
        self.client = client
        self._raw = raw
        self.channels = []
        self.location = ""
        self.uid = self._raw["uid"]
        self.name = self._raw["installationArea"]
        self.device_type = self._raw["typeID"]
        self.battery_state = self._raw["batteryState"]
        self.serial_number = self._raw["metaData"]["serialNumber"]
        self.software_update_available = self._raw["isSoftwareUpdateAvailable"]
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
                        log.warning("Unsupported enet channel type: %s", channel_type_id)
                        continue

                log.debug(
                    "  Channel no: %s Type: %s Area: %s Meta: %s",
                    device_channel["no"],
                    device_channel["channelTypeID"],
                    device_channel["effectArea"],
                    CHANNEL_TYPE_CONFIGURATION.get(channel_type_id),
                )

    def get_channel_use_type(self, device_channel):
        """Determine if the channel is an actuator or a sensor"""
        channel_type_id = device_channel["channelTypeID"]
        channel_type_config = CHANNEL_TYPE_CONFIGURATION.get(channel_type_id)

        if channel_type_id in CHANNEL_TYPES_IGNORED:
            return ChannelUseType.IGNORED

        # CT_1F01_DUMMY has type None, but needs to be supported as well
        channel_meta_data =  enet_data.get_channel_meta_data_from_channel_type(channel_type_id)
        if channel_meta_data.get("useTypeID", "") == ChannelTypeUseType.ACTUATOR:
            if channel_meta_data.get("subSectionTypeID", "") in {ChannelTypeSubSectionType.BLINDS, ChannelTypeSubSectionType.LIGHT}:
                return ChannelUseType.ACTUATOR
            else: # Special case for CT_1F19 energy sensor
                return ChannelUseType.SENSOR
        elif channel_meta_data.get("useTypeID", "") == ChannelTypeUseType.SENSOR:
            return ChannelUseType.SENSOR

        if channel_type_config is None:
            return ChannelUseType.UNSUPPORTED

        return ChannelUseType.UNSUPPORTED

    def get_battery_state(self):
        return self.battery_state

    def update_battery_state(self, battery_state):
        if battery_state in [state.value for state in DeviceBatteryState]:
            log.debug("Updating battery state for %s from %s to %s", self.name, self.battery_state, battery_state)
            self.battery_state = battery_state
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
        for function_uid in self.get_function_uids_for_event():
            log.debug(
                "Register event subscription for %s - %s", self.name, function_uid
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
        self._find_output_functions()
        self._find_input_functions()
        self._find_device_parameters()
        self._set_application_mode()
        self._set_active()

    def _find_output_functions(self):
        device_output_function_list = self._get_mapped_type_ids("outputDeviceFunctions")
        for output_function in self.channel["outputDeviceFunctions"]:
            if output_function.get("typeID") in device_output_function_list.keys():
                if output_function.get("active", False) is True:
                    type_name = enet_data.get_output_device_function_name(output_function.get("typeID"))
                    name = f"Channel {self.name} - {type_name}"

                    self.output_functions[output_function.get("typeID")] = dict(
                        uid=output_function["uid"],
                        typeID=output_function["typeID"],
                        name=name
                    )

                    self.current_values[output_function.get("typeID")] = self._get_current_value_from_dict(output_function)

    def _find_input_functions(self) -> None:
        device_input_function_list = self._get_mapped_type_ids("inputDeviceFunctions")
        for input_function in self.channel["inputDeviceFunctions"]:
            if input_function.get("typeID") in device_input_function_list.keys():
                if input_function.get("active", False) is True:
                    type_name = enet_data.get_input_device_function_name(input_function.get("typeID"))
                    input_template = enet_data.get_value_template_from_input_device_function(input_function.get("typeID"))
                    name = f"Channel {self.name} - {type_name}"

                    self.input_functions[input_function.get("typeID")] = {
                        "uid": input_function["uid"],
                        "typeID": input_function["typeID"],
                        "name": name,
                        "template": input_template,
                    }

    def _get_output_function_by_uid(self, uid: str) -> Union[dict, None]:
        for output_function in self.output_functions.values():
            if output_function["uid"] == uid:
                return output_function
        return None

    def get_function_uids_for_event(self) -> dict:
        """Return a list of function uids we should setup event listeners for"""
        function_uids = {}
        for output_function in self.output_functions.values():
            if "uid" in output_function:
                function_uids[output_function["uid"]] = self
        return function_uids

    def _get_mapped_type_ids(self, list_name) -> dict:
        channel_map = getitem_from_dict(CHANNEL_TYPE_CONFIGURATION.get(self.channel_type), [list_name])
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
                    type_name = enet_data.get_device_parameter_name(device_parameter.get("typeID"))
                    parameter_template = enet_data.get_value_template_from_device_parameter (device_parameter.get("typeID"))
                    name = f"Parameter {self.name} - {type_name}"

                    self.device_parameters[device_parameter.get("typeID")] = {
                        "uid": device_parameter.get("uid"),
                        "typeID": device_parameter.get("typeID"),
                        "name": name,
                        "template": parameter_template
                    }

                    self.parameter_values[device_parameter.get("typeID")] = self._get_current_value_from_dict(device_parameter)

    def _get_device_parameter(self, channel_param_name: ChannelTypeFunctionName) -> Union[Dict, None]:
        device_param_id = self.get_channel_configuration_entry("deviceParameters", channel_param_name)
        if device_param_id is not None:
            return self.device_parameters.get(device_param_id, None)
        else:
            return None

    def _get_parameter_value(self, channel_param_name: ChannelTypeFunctionName) -> Any:
        device_param_id = self.get_channel_configuration_entry("deviceParameters", channel_param_name)
        if device_param_id is not None:
            param_value = self.parameter_values.get(device_param_id, None)
            if param_value is not None:
                return param_value.get("value")

        return None

    def _get_current_value_from_dict(self, function_object) -> Union[dict, list]:
        current_values = getitem_from_dict(function_object, ["currentValues"])
        if len(current_values) == 1:
            return self._parse_value(current_values[0])
        else:
            return [self._parse_value(value) for value in current_values]

    def _parse_value(self, value_to_parse: dict) -> dict:
        value = value_to_parse.get("value", None)
        value_type_id = value_to_parse.get("valueTypeID", "")

        if value is not None and value_type_id!= "":
            return {
                "value": value,
                "valueTypeID": value_type_id
            }
        else:
            log.error("Invalid value: %s", value_type_id)

    def _set_application_mode(self):
        application_mode_value = self._get_parameter_value(ChannelTypeFunctionName.APPLICATION_MODE)
        if application_mode_value is not None:
            self.application_mode = ChannelApplicationMode[application_mode_value]

    def _set_active(self) -> bool:
        if self.application_mode != ChannelApplicationMode.UNUSED and (len(self.output_functions) > 0 or len(self.input_functions) > 0):
                self.active = True

    def get_channel_configuration_entry(self, config_group: str, config_param: ChannelTypeFunctionName) -> str:
        """Return the configuration for a specific config parameter"""
        return getitem_from_dict(CHANNEL_TYPE_CONFIGURATION, [self.channel_type, config_group, config_param])

    def update_values(self, function_uid: str, values: Dict) -> None:
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

    def get_current_value(self, channel_function_name: ChannelTypeFunctionName) -> Any:
        """Set channel to new value"""
        channel_config_id = self.get_channel_configuration_entry("outputDeviceFunctions", channel_function_name)
        value_container = self.current_values.get(channel_config_id, {})
        value = value_container.get("value", None)

        if value is not None:
            return value
        else:
            raise Exception("No value found for channel %s and function %s" % (self.name, channel_config_id))

    def set_current_value(self, channel_function_name: ChannelTypeFunctionName, value = None) -> None:
        """Set channel to new value"""
        channel_config_id = self.get_channel_configuration_entry("outputDeviceFunctions", channel_function_name)
        if value is not None:
            self.current_values[channel_config_id]["value"] = value

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
        channel_config_id = self.get_channel_configuration_entry("outputDeviceFunctions", channel_function_name)
        channel_config = self.output_functions.get(channel_config_id)
        if channel_config is not None:
            params = {"deviceFunctionUID": channel_config["uid"]}
            result = await self.device.client.request(
                URL_VIZ, "getCurrentValuesFromOutputDeviceFunction", params
            )

            current_value = self._get_current_value_from_dict(result)
            log.info("%s get_value() returned %s", self.name, current_value)
            return current_value.get("value")

    async def set_value(self, channel_function_name: ChannelTypeFunctionName, value = None) -> None:
        """Set channel to new value"""
        channel_config_id = self.get_channel_configuration_entry("inputDeviceFunctions", channel_function_name)
        channel_config = self.input_functions.get(channel_config_id)
        input_function_uid = channel_config.get("uid", "")

        params = {
            "deviceFunctionUID": input_function_uid,
            "values": []
        }

        value_template = channel_config.get("template", []).copy()

        if value is not None:
            # try to cast to correct type...
            _type = type(value_template[0]["value"])
            casted_value = _type(value)
            value_template[0]["value"] = casted_value
            self.set_current_value(channel_function_name, casted_value)

        params["values"] = value_template

        await self.device.client.request(URL_VIZ, "callInputDeviceFunction", params)

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
