"""async library for communicating with a Enet Smart Home server from Jung / Gira"""

import logging
import functools
import aiohttp

from .enet_devices import device_info, channel_config

log = logging.getLogger(__name__)

URL_MANAGEMENT = "/jsonrpc/management"
URL_VIZ = "/jsonrpc/visualization"
URL_COM = "/jsonrpc/commissioning"
URL_TELEGRAM = URL_COM + "/app_telegrams"
URL_SCENE = "/jsonrpc/visualization/app_scene"


class AuthError(Exception):
    "Authentication error"


def auth_if_needed(func):
    "Decorator used to reauthenticate if we get a AuthError"

    @functools.wraps(func)
    def auth_wrapper(self, *args, **kwargs):
        "Perfor reauthentication"
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
        self.baseurl = url
        jar = aiohttp.CookieJar(unsafe=True)
        self._session = aiohttp.ClientSession(cookie_jar=jar)
        self._debug_requests = False
        self._api_counter = 1
        self._cookie = ""
        self._last_telegram_ts = {}
        self._raw_json = {}
        self.devices = []

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
        response = await self._session.post(f"{self.baseurl}{url}", json=req)
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
            "filter": ".+\\\\.(SCV1|SCV2|SNA|PSN)\\\\[(.|1.|2.|3.)\\\\]+",
        }
        result = await self.request(URL_VIZ, "getDevicesWithParameterFilter", params)
        raw_devices = result["devices"]
        self._raw_json = raw_devices
        devices = []
        for raw_device in raw_devices:
            device = create_device(self, raw_device)
            if not device:
                continue
            device.location = device_locations.get(device.uid, "")
            devices.append(device)

        return devices

    async def get_locations(self):
        """Get all locations"""
        params = {"locationUIDs": []}
        result = await self.request(URL_VIZ, "getLocations", params)
        return result["locations"]

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
        # func_uid = "83ec3031-f8f0-4972-a92b-2df300000c6a"  # peis dimmer
        result = await self.request(
            URL_VIZ,
            "registerEventOutputDeviceFunctionCalled",
            {"deviceFunctionUID": func_uid},
        )
        return result

    async def get_events(self):
        """Poll Enet server for events"""
        try:
            result = await self.request(URL_VIZ, "requestEvents", None)
            return result
        except Exception as err:
            return


def create_device(client, raw):
    """Create an enet Actuator or Sensor depending on its type"""
    device_type = raw["typeID"]
    info = device_info.get(device_type)
    if info is None:
        log.warning(
            "Unknown device: typeID=%s name=%s", raw["typeID"], raw["installationArea"]
        )

    if info["device_class"] == "Actuator":
        print("Actuator added: " + raw["typeID"])
        return Actuator(client, raw)
    else:
        print("Sensor added: " + raw["typeID"])
        return Sensor(client, raw)


class BaseEnetDevice:
    """The base class for all Enet Devices, both sensors and actuators"""

    def __init__(self, client, raw):
        self.client = client
        self._raw = raw
        self.channels = []
        self.location = ""
        self.uid = self._raw["uid"]
        self.name = self._raw["installationArea"]
        self.device_type = self._raw["typeID"]
        self.battery_state = self._raw["batteryState"]
        self.software_update_available = self._raw["isSoftwareUpdateAvailable"]

    def __repr__(self):
        return f"{self.__class__.__name__} Name: {self.name} Type: {self.device_type}"


class Sensor(BaseEnetDevice):
    """Class representing a Enet Sensor or Wall switch"""

    def __init__(self, client, raw):
        super().__init__(client, raw)
        self.channels = []
        self.create_channels()

    def get_function_uids_for_event(self):
        """Return a list of function uids we should setup event listeners for"""
        function_uids = {}
        for channel in self.channels:
            for output_function in channel["output_functions"]:
                function_uids[output_function["uid"]] = self
        return function_uids

    async def register_events(self):
        """Setup event subscription for all outputfunctions"""
        for channel in self.channels:
            for output_function in channel["output_functions"]:
                log.debug(
                    "Register event subscription for %s - %s",
                    self.name,
                    output_function["uid"],
                )
                await self.client.setup_event_subscription(output_function["uid"])

    def create_channels(self):
        """Create channels"""
        log.debug(
            "Enet Device %s type %s has the following channels:",
            self.name,
            self.device_type,
        )

        for channel_config_group_index, channel_config_group in enumerate(
            self._raw["deviceChannelConfigurationGroups"]
        ):
            for device_channel_index, device_channel in enumerate(
                channel_config_group["deviceChannels"]
            ):
                log.debug(
                    "  ccg: %s dc: %s Channel no: %s Type: %s Area: %s",
                    channel_config_group_index,
                    device_channel_index,
                    device_channel["no"],
                    device_channel["channelTypeID"],
                    device_channel["effectArea"],
                )
                output_functions = []
                for outputfunc in device_channel["outputDeviceFunctions"]:
                    log.debug(
                        "    opf uid: %s type: %s active: %s",
                        outputfunc["uid"],
                        outputfunc["typeID"],
                        outputfunc["active"],
                    )
                    if outputfunc["active"]:
                        typenames = {
                            "FT_INScS.SC": "Scene",
                            "FT_INScS.MD": "Master dim",
                            "FT_INGBRS.GBR": "Rocker",
                        }
                        type_name = typenames.get(outputfunc["typeID"], "Unknown")
                        name = f"Channel {device_channel} - {type_name}"

                        output_functions.append(
                            dict(
                                uid=outputfunc["uid"],
                                typeID=outputfunc["typeID"],
                                name=name,
                            )
                        )
                if device_channel["channelTypeID"] != "CT_DEVICE":
                    self.channels.append(
                        dict(
                            no=device_channel["no"],
                            channel_type=device_channel["channelTypeID"],
                            area=device_channel["effectArea"],
                            output_functions=output_functions,
                        )
                    )


class Actuator(BaseEnetDevice):
    """Class representing a enet actuator, ie a dimmer, switch or blind. Each actuator has one or more channels"""

    def __init__(self, client, raw):
        super().__init__(client, raw)
        self.create_channels()

    def get_function_uids_for_event(self):
        """Return a list of function uids we should setup event listeners for"""
        return {
            channel.output_device_function["uid"]: channel for channel in self.channels
        }

    async def register_events(self):
        """Setup event subscription for all outputfunctions"""
        for channel in self.channels:
            log.debug(
                "Register event subscription for %s - %s",
                self.name,
                channel.output_device_function["uid"],
            )
            await self.client.setup_event_subscription(
                channel.output_device_function["uid"]
            )

    def create_channels(self):
        """Create channels"""
        log.debug(
            "Enet Device %s type %s has the following channels:",
            self.name,
            self.device_type,
        )
        for channel_config_group_number, channel_config_group in enumerate(
            self._raw["deviceChannelConfigurationGroups"]
        ):
            for device_channel_number, device_channel in enumerate(
                channel_config_group["deviceChannels"]
            ):
                log.debug(
                    "  ccg: %s dc: %s Channel type: %s area: %s",
                    channel_config_group_number,
                    device_channel_number,
                    device_channel["channelTypeID"],
                    device_channel["effectArea"],
                )
                if device_channel["channelTypeID"] != "CT_DEVICE":
                    self.channels.append(Channel(self, device_channel))

    def __repr__(self):
        return f"{self.__class__.__name__} (Name: {self.name} Type: {self.device_type}"


class Channel:
    """A class representing a actuator channel that can dim or switch a load"""

    def __init__(self, device, raw_channel):
        self.device = device
        self.channel = raw_channel
        self.has_brightness = False
        self.uid = f"{self.device.uid}-{self.channel['no']}"
        self.channel_type = self.channel["channelTypeID"]
        self.output_device_function = self._find_output_function()
        self._input_device_function = self._find_input_function()
        self._value_template = self._build_value_template()
        self.name = self.channel["effectArea"]

        self.state = self.output_device_function["currentValues"][0]["value"]

    def _build_value_template(self):
        value_template = self.output_device_function["currentValues"][0]
        if "valueUID" in value_template:
            del value_template["valueUID"]
        return value_template

    def _find_output_function(self):
        main_func = None
        for odf, output_func in enumerate(self.channel["outputDeviceFunctions"]):
            type_id = output_func["typeID"]
            value_type_id = output_func["currentValues"][0]["valueTypeID"]
            value = output_func["currentValues"][0]["value"]
            main = channel_config.get(self.channel_type).get("info") == type_id

            if value_type_id == "VT_SCALING_RANGE_0_100_DEF_0":
                self.has_brightness = True
                self.state = value
            if main:
                main_func = output_func
        return main_func

    def _find_input_function(self):
        main_func = None
        for idf, input_func in enumerate(self.channel["inputDeviceFunctions"]):
            type_id = input_func["typeID"]
            main = channel_config.get(self.channel_type).get("control") == type_id
            if main:
                print(f"    idf: {idf} type: {type_id} main: {main}")
                main_func = input_func
        return main_func

    async def get_value(self):
        """Fetch the updated state from the sever"""
        params = {"deviceFunctionUID": self.output_device_function["uid"]}
        current_value = await self.device.client.request(
            URL_VIZ, "getCurrentValuesFromOutputDeviceFunction", params
        )

        value = current_value["currentValues"][0]["value"]
        log.info("%s get_value() returned %s", self.name, value)
        return value

    async def set_value(self, value):
        """Set channel to new value"""
        input_function = self._input_device_function["uid"]
        value_param = self._value_template.copy()
        # try to cast to correct type...
        _type = type(value_param["value"])
        casted_value = _type(value)
        value_param["value"] = casted_value
        params = {
            "deviceFunctionUID": input_function,
            "values": [value_param],
        }

        self.state = casted_value
        await self.device.client.request(URL_VIZ, "callInputDeviceFunction", params)

    async def turn_off(self):
        "Turn off device"
        log.info("%s turn_off()", self.name)
        await self.set_value(0)

    async def turn_on(self):
        "Turn on device"
        log.info("%s turn_on()", self.name)
        await self.set_value(100)

    def __repr__(self):
        return f"{self.__class__.__name__} (Name: {self.name} Type: {self.channel_type} Value: {self.state})"
