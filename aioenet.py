#!/usr/bin/env python

import logging
import aiohttp

log = logging.getLogger(__name__)

URL_MANAGEMENT = "/jsonrpc/management"
URL_VIZ = "/jsonrpc/visualization"
URL_COM = "/jsonrpc/commissioning"
URL_TELEGRAM = URL_COM + "/app_telegrams"
URL_SCENE = "/jsonrpc/visualization/app_scene"


class AuthError(Exception):
    pass


class EnetClient:
    def __init__(self, url, user, passwd, sslverify=True):
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
        self.devices = []

    def auth_if_needed(func):
        def auth_wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except AuthError:
                log.warning("Trying to re-authenticate...")
                self.simple_login()

            return func(self, *args, **kwargs)
        return auth_wrapper

    @auth_if_needed
    async def request(self, url, method, params, get_raw=False):
        return await self._do_request(url, method, params, get_raw)

    async def _do_request(self, url, method, params, get_raw=False):
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": str(self._api_counter),
        }
        self._api_counter += 1
        log.info("Requesting %s%s %s", self.baseurl, url, method)
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
            e = "-> {} {} returned error: {}".format(url, method, json["error"])
            print(json["error"])
            if json["error"]["code"] in (-29998, -29997):
                raise (AuthError)
            else:
                raise (Exception(e))
        else:
            if self._debug_requests:
                print("-> {} {} returned: {}".format(url, method, json["result"]))
        return json["result"]

    async def simple_login(self):
        params = dict(userName=self.user, userPassword=self.passwd)
        await self._do_request(URL_MANAGEMENT, "userLogin", params)
        await self._do_request(
            URL_MANAGEMENT, "setClientRole", dict(clientRole="CR_VISU")
        )

    def get_account(self):
        return self.request(URL_MANAGEMENT, "getAccount", {})

    async def get_devices(self):
        device_locations = await self.get_device_locations()
        deviceUIDs = list(device_locations.keys())
        params = {
            "deviceUIDs": deviceUIDs,
            "filter": ".+\\\\.(SCV1|SCV2|SNA|PSN)\\\\[(.|1.|2.|3.)\\\\]+",
        }
        result = await self.request(URL_VIZ, "getDevicesWithParameterFilter", params)
        devices = result["devices"]
        devices = [Device(self, dev) for dev in devices if dev]
        for device in devices:
            device.location = device_locations[device.uid]
        return devices

    async def get_locations(self):
        params = {"locationUIDs": []}
        result = await self.request(URL_VIZ, "getLocations", params)
        return result["locations"]


    async def get_device_locations(self):
        locations = await self.get_locations()
        device_to_loc = {}

        def recurse_locations(locations, parent=[], level=0):
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

        recurse_locations(locations)
        return device_to_loc


known_actuators = [
    "DVT_DA1M",  # Jung 1 channel dimming actuator
    "DVT_SV1M",  # Jung 1 channel 1-10V dimming actuator
    "DVT_DA4R",  # 4 channel dimming actuator rail mount
    "DVT_DA1R",  # 1 channel dimming actuator rail mount
    "DVT_SJAR",  # 8 channel switch actuator
    "DVT_SA2M",  # Gira 2-gang switching actuator https://katalog.gira.de/en/datenblatt.html?id=635918
]

known_sensors = [
    "DVT_TADO",
    "DVT_WS2BJF50",
    "DVT_WS2BJF50CL",
    "DVT_WS3BJF50",
    "DVT_WS4BJF50",
    "DVT_US2M",
    "DVT_WS1BG",
    "DVT_SA1M",
    "DVT_WS3BG",
    "DVT_RPZS",
    "DVT_SJA1",
    "DVT_S2A1",
    "DVT_HS2",
    "DVT_HS4",
    "DVT_WS3BJF50CL",
    "DVT_WS4BJF50CL",  #
    "DVT_BS1BP",  # eNet motion detector
    "DVT_SF1S",  # eNet light sensor
    "DVT_WS4BJ",
]  # eNet radio transmitter module 4-gang

channelconfig = {'CT_1F01': {'control': 'FT_INSA.SOO',   # Switch
                             'info': 'FT_INSA.IOO'},
                 'CT_1F01_DUMMY': {'control': 'FT_INSA.SOO',
                                   'info': 'FT_INSA.IOO'},
                 'CT_1F02': {'control': 'FT_INDA.ASC', # Dimmer
                             'info': 'FT_INDA.ADV'},
                 'CT_1F03': {'control': 'FT_INBA.SAPBP', # Blinds
                             'info': 'FT_INBA.CAPBP'},
                 'CT_1F05': {'control': 'FT_INSArGO.SOO',
                             'info': 'FT_INSArGO.IOO'},
                 'CT_1F08': {'control': 'FT_INSAv2.SOO',
                             'info': 'FT_INSAv2.IOO'},
                 'CT_1F09': {'control': 'FT_INSAv3.SOO',
                             'info': 'FT_INSAv3.IOO'},
                 'CT_1F0B': {'control': 'FT_INLAv2_SA.SOO',
                             'info': 'FT_INLAv2_SA.IOO'},
                 'CT_1F0C': {'control': 'FT_INLAv2_DA.ASC',
                             'info': 'FT_INLAv2_DA.ADV'},
                 'CT_1F0D': {'control': 'FT_INLAv2_SAS.SOO',
                             'info': 'FT_INLAv2_SAS.IOO'},
                 'CT_1F0E': {'control':'FT_INLNS3.ASC',
                             'info': ''},
                 'CT_1F0E_SA': {'control': 'FT_INLNS3_SA.SOO',
                                'info': ''},
                 'CT_1F10': {'control': 'FT_INBAv3.SAPBP',
                             'info': 'FT_INBAv3.CAPBP'},
                 'CT_1F18': {'control': '',
                             'info': 'FT_INThScS.CAVTH1'},
                 'CT_1F19': {'control': '',
                             'info': 'FT_INES.ABAE'},
                 'CT_1F1B': {'control': '',
                             'info': 'FT_INMOVS.BA'},
                 'CT_TADO_ZH': {'control': 'FT_TADOZH.SET_OVERLAY',
                                'info': 'FT_TADOZH.INSIDE_TEMP'},
                 'CT_TADO_ZAC': {'control': 'FT_TADOZAC.SET_OVERLAY',
                                 'info': 'FT_TADOZAC.INSIDE_TEMP'},
                 'CT_TADO_ZHW': {'control': 'FT_TADOZHW.SET_OVERLAY',
                                 'info': 'FT_TADOZHW.INSIDE_TEMP'}}


def Device(client, raw):
    device_type = raw["typeID"]
    if device_type in known_actuators:
        print("Actuator added: " + raw["typeID"])
        return Light(client, raw)
    elif device_type in known_sensors:
        print("Sensor added: " + raw["typeID"])
        return Switch(client, raw)
    else:
        log.warning(
            "Unknown device: typeID=%s name=%s", raw["typeID"], raw["installationArea"]
        )


class BaseEnetDevice:
    def __init__(self, client, raw):
        self.client = client
        self._raw = raw
        self.channels = []
        self.uid = self._raw["uid"]
        self.name = self._raw["installationArea"]
        self.device_type = self._raw["typeID"]
        self.battery_state = self._raw["batteryState"]
        self.software_update_available = self._raw["isSoftwareUpdateAvailable"]

    def __repr__(self):
        return "{}(Name: {} Type: {})".format(
            self.__class__.__name__, self.name, self.device_type
        )


class Switch(BaseEnetDevice):
    pass


class Light(BaseEnetDevice):
    def __init__(self, client, raw):
        super().__init__(client, raw)
        self.create_channels()

    def create_channels(self):
        print(
            f"Enet Device {self.name} type {self.device_type} has the following channels:"
        )
        for ccg, channel_config_group in enumerate(
            self._raw["deviceChannelConfigurationGroups"]
        ):
            for dc, device_channel in enumerate(channel_config_group["deviceChannels"]):
                print(
                    f"  ccg: {ccg} dc: {dc} Channel type: {device_channel['channelTypeID']} area: {device_channel['effectArea']}"
                )
                if device_channel["channelTypeID"] != "CT_DEVICE":
                    c = Channel(self, device_channel)
                    self.channels.append(c)

                for odf, output_func in enumerate(
                    device_channel["outputDeviceFunctions"]
                ):
                    type_id = output_func["currentValues"][0]["valueTypeID"]
                    value = output_func["currentValues"][0]["value"]
                    # print(f"    odf: {odf} type: {type_id} value: {value}")

    def __repr__(self):
        return "{}(Name: {} Type: {})".format(
            self.__class__.__name__, self.name, self.device_type
        )


class Channel:
    def __init__(self, device, raw_channel):  
        self._device = device
        self.channel = raw_channel
        self.has_brightness = False
        self.uid = f"{self._device.uid}-{self.channel['no']}"
        self.channel_type = self.channel["channelTypeID"]
        self._output_device_function = self._find_output_function()
        self._input_device_function = self._find_input_function()
        self._value_template = self._build_value_template()
        self.name = self.channel["effectArea"]

        self.state = 0


    def _build_value_template(self):
        value_template = self._output_device_function["currentValues"][0]
        del value_template["valueUID"]
        return value_template


    def _find_output_function(self):
        main_func = None
        for odf, output_func in enumerate(self.channel["outputDeviceFunctions"]):
            type_id = output_func["typeID"]
            value_type_id = output_func['currentValues'][0]['valueTypeID']
            value = output_func['currentValues'][0]['value']
            main = channelconfig.get(self.channel_type).get("info") == type_id

            if value_type_id == "VT_SCALING_RANGE_0_100_DEF_0":
                self.has_brightness = True
                self.state = value
            if main:
                main_func = output_func
                print(f"    odf: {odf} {type_id} value type: {value_type_id} value: {value} main: {main}")

        return main_func

    
    def _find_input_function(self):
        main_func = None
        for idf, input_func in enumerate(self.channel["inputDeviceFunctions"]):
            type_id = input_func['typeID']
            main = channelconfig.get(self.channel_type).get("control") == type_id
            if main:
                print(f"    idf: {idf} type: {type_id} main: {main}")
                main_func = input_func
        return main_func


    async def get_value(self):
        params = {"deviceFunctionUID": self._output_device_function["uid"]}
        current_value = await self._device.client.request(
            URL_VIZ, "getCurrentValuesFromOutputDeviceFunction", params
        )

        value = current_value["currentValues"][0]["value"]
        log.info("%s get_value() returned %s", self.name, value)
        self._last_value = value
        return value


    async def set_value(self, value):
        input_function = self._input_device_function["uid"]
        value_param = self._value_template.copy()
        # try to cast to correct type...
        _type = type(value_param["value"])
        casted_value =  _type(value)
        value_param["value"] = casted_value
        params = {
            "deviceFunctionUID": input_function,
            "values": [value_param],
        }

        self.state = casted_value
        print("set_value()", params)
        await self._device.client.request(URL_VIZ, "callInputDeviceFunction", params)


    async def turn_off(self):
        log.info("%s turn_off()", self.name)
        await self.set_value(0)


    async def turn_on(self):
        log.info("%s turn_on()", self.name)
        await self.set_value(100)


    def __repr__(self):
        return "{}(Name: {} Type: {} Value: {})".format(
            self.__class__.__name__, self.name, self.channel_type, self.state
        )

