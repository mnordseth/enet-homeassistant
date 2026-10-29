"""async library for communicating with a Enet Smart Home server from Jung / Gira"""

import logging
import functools
import time
import json
import random

from dataclasses import dataclass
from typing import Any, Dict, Union

import asyncio
import aiohttp

from .enums import ButtonEventType, ChannelUseType
from .enet_data.data import enet_data
from .enet_data.constants import CHANNEL_TYPES_IGNORED
from .enet_data.enums import (
    ChannelTypeUseType,
    ChannelTypeSubSectionType,
    ChannelApplicationMode,
    ChannelTypeFunctionName,
    DeviceBatteryState,
)
from .enet_data.channel_mapping import CHANNEL_TYPE_CONFIGURATION
from .enet_data.utils import getitem_from_dict
from enum import StrEnum

log = logging.getLogger(__name__)


class URL(StrEnum):
    """URLs for communicating with Enet server"""

    MANAGEMENT = "/jsonrpc/management"
    VISUALIZATION = "/jsonrpc/visualization"
    COMMISSIONING = "/jsonrpc/commissioning"
    TELEGRAM = "/jsonrpc/commissioning/app_telegrams"
    SCENE = "/jsonrpc/visualization/app_scene"


ID_FILTER_ALL = "*"

EVENT_DEVICE_BATTERY_STATE_CHANGED = "deviceBatteryStateChanged"
EVENT_OUTPUT_DEVICE_FUNCTION_CALLED = "outputDeviceFunctionCalled"
VALUE_TYPE_ROCKER_STATE = "VT_ROCKER_STATE"
VALUE_TYPE_ROCKER_SWITCH_TIME = "VT_ROCKER_SWITCH_TIME"
VALUE_DOWN_BUTTON = "DOWN_BUTTON"
EVENT_RETRY_BASE_DELAY = 2
PING_INTERVAL = 28


@dataclass
class ButtonEvent:
    "A button / rocker / scene control on a sensor channel was pressed or released"

    channel: "DeviceChannel"
    subtype: int
    type: ButtonEventType


@dataclass
class BatteryStateEvent:
    "The battery state of a device changed"

    device: "Device"
    battery_state: str


@dataclass
class ValueUpdateEvent:
    "The current values of a channel were updated"

    channel: "DeviceChannel"
    function_uid: str
    values: list


class AuthError(Exception):
    "Authentication error"


class EnetServerError(Exception):
    "Enet server error"


def auth_if_needed(func):
    "Decorator used to reauthenticate if we get a AuthError"

    @functools.wraps(func)
    async def auth_wrapper(self, *args, **kwargs):
        "Perform re-authentication"
        auth_generation = self._auth_generation
        try:
            return await func(self, *args, **kwargs)
        except AuthError:
            async with self._auth_lock:
                # Another request may already have refreshed the same session.
                if auth_generation == self._auth_generation:
                    log.warning("Trying to re-authenticate...")
                    await self.simple_login()
                    await self._restore_event_subscriptions()

        # Retry only once: bad credentials or another auth failure must propagate.
        return await func(self, *args, **kwargs)

    return auth_wrapper


class EnetClient:
    """Client for communicating with a Enet Smart Home Server from Jung / Gira"""

    def __init__(self, url, user, passwd, noconnect=False, load_file=""):
        self.user = user
        self.passwd = passwd
        if url.endswith("/"):
            url = url[:-1]
        self.baseurl = url.strip()
        self._offline = noconnect
        if self._offline:
            self._session = None
        else:
            jar = aiohttp.CookieJar(unsafe=True)
            connector = aiohttp.TCPConnector(keepalive_timeout=30)
            self._session = aiohttp.ClientSession(cookie_jar=jar, connector=connector)
        self._debug_requests = False
        self._api_counter = 1
        self._cookie = ""
        self._last_event = {}
        self._last_event_ts = 0.0
        self._bg_tasks = []
        self._raw_json = {}
        self._subscribers = []
        self.function_uid_map = {}
        self.devices = []
        self._auth_lock = asyncio.Lock()
        self._auth_generation = 0
        self._event_subscriptions = set()
        self._battery_events_subscribed = False

        if load_file:
            with open(load_file) as fp:
                config = json.load(fp)
                self._raw_json = config["data"]["enet_data"]

    async def initialize(self):
        """Login, fetch devices, subscribe to events and start the background
        tasks reading events (and pinging the server when using https).
        The background tasks run until close() is called.
        """
        await self.simple_login()
        self.devices = await self.get_devices()
        await self.initialize_events()
        self._bg_tasks.append(
            asyncio.create_task(
                self.read_events(), name=f"enet event reader {self.baseurl}"
            )
        )
        if self.baseurl.startswith("https://"):
            self._bg_tasks.append(
                asyncio.create_task(
                    self.ping_forever(), name=f"enet keepalive ping {self.baseurl}"
                )
            )

    async def stop(self):
        """Cancel the background tasks and wait for them to finish"""
        for task in self._bg_tasks:
            task.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
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
        Subscribe to events. The callback is called with a ButtonEvent,
        BatteryStateEvent or ValueUpdateEvent.
        """
        self._subscribers.append(callback)

        # unsubscribe logic
        def unsubscribe():
            self._subscribers.remove(callback)

        return unsubscribe

    def _notify_subscribers(self, event):
        for callback in self._subscribers:
            callback(event)

    async def read_events(self):
        """Fetch and handle events from the Enet server, forever

        requestEvents blocks for 30s if no events are available or returns
        immediately when there are events. Failed requests are retried with
        an increasing delay.
        """
        if self._offline:
            await self._simulate_events()
            return

        failcount = 0
        while True:
            try:
                events = await self.get_events()
                failcount = 0
            except Exception as e:
                failcount += 1
                delay = EVENT_RETRY_BASE_DELAY * failcount
                log.warning("Failed to fetch events: (%s), retrying in: %s", e, delay)
                await asyncio.sleep(delay)
                continue
            if events:
                try:
                    await self.handle_events(events)
                except Exception as e:
                    log.exception("Failed to handle events: %s (%s)", events, e)

    async def _simulate_events(self):
        """Simulate events when offline by randomly generating events - only for debugging"""
        while True:
            await asyncio.sleep(10)
            if random.random() < 0.3:
                event = {
                    "sequenceNumber": 42,
                    "event": EVENT_OUTPUT_DEVICE_FUNCTION_CALLED,
                    "eventData": {
                        "deviceUID": "1fb68d33-bc85-43aa-b459-34510bb08648",
                        "channelNumber": 1,
                        "deviceFunctionUID": "1fb68d33-bc85-43aa-b459-34510bb0866c",
                        "values": [
                            {
                                "value": 6.79,
                                "valueTypeID": "VT_VALUE_TIME1_RANGE_0.0_86400.0_DEF_0.0",
                            }
                        ],
                    },
                }
                await self.handle_events({"events": [event]})

    async def handle_events(self, events):
        """Handle events from Enet Server. Either update value of actuator or
        forward event from sensor

        Example event:

        {'sequenceNumber': 0,
         'event': 'outputDeviceFunctionCalled',
         'eventData': {'deviceUID': '83ec3031-f8f0-4972-a92b-2df300001ced',
                       'channelNumber': 1,
                       'deviceFunctionUID': '83ec3031-f8f0-4972-a92b-2df30050025d',
                       'values': [{'value': 'ACTIVATE', 'valueTypeID': 'VT_IN_SCENE_CONTROL'},
                                  {'value': 0, 'valueTypeID': 'VT_SCENE_NUMBER'}]
                      }
        }
        """
        for event in events["events"]:
            log.debug("Handling event: %s", event)
            if (
                time.time() - self._last_event_ts < 0.5
                and event["event"] == self._last_event.get("event")
                and event["eventData"] == self._last_event.get("eventData")
            ):
                # This is a duplicate of last event, happes with scene activitation
                log.debug("Received duplicate event")
                continue

            self._last_event = event
            self._last_event_ts = time.time()

            if event["event"] == EVENT_OUTPUT_DEVICE_FUNCTION_CALLED:
                await self._handle_output_device_function_called(event)
            elif event["event"] == EVENT_DEVICE_BATTERY_STATE_CHANGED:
                self._handle_battery_state_changed(event)

    async def _handle_output_device_function_called(self, event):
        data = event["eventData"]
        function_uid = data["deviceFunctionUID"]
        channel = self.function_uid_map.get(function_uid)
        if not channel:
            log.warning("Function %s does not map to device", function_uid)
            return

        values = data["values"]
        channel_type = channel.get_channel_type_function_name_from_output_function_uid(
            function_uid
        )

        if channel_type not in [
            ChannelTypeFunctionName.BUTTON_ROCKER,
            ChannelTypeFunctionName.SCENE_CONTROL,
        ]:
            await channel.update_values(function_uid, values)
            self._notify_subscribers(ValueUpdateEvent(channel, function_uid, values))
            return

        # Decode sensor / button events
        subtype = data["channelNumber"]
        if len(values) != 2:
            log.warning("Expected 2 values: %s", event)
            return

        event_type = ButtonEventType.INITIAL_PRESS
        if values[0]["valueTypeID"] == VALUE_TYPE_ROCKER_STATE:
            # If a button is configured as a rocker, you have the UP and Down
            # button on the same channel.
            if values[0]["value"] == VALUE_DOWN_BUTTON:
                subtype += 1
        if values[1]["valueTypeID"] == VALUE_TYPE_ROCKER_SWITCH_TIME:
            # Switch time is 0 on press and a number for release.
            switch_time = values[1]["value"]
            if switch_time > 0:
                event_type = ButtonEventType.SHORT_RELEASE
            if switch_time > 60:
                event_type = ButtonEventType.LONG_RELEASE

        self._notify_subscribers(ButtonEvent(channel, subtype, event_type))

    def _handle_battery_state_changed(self, event):
        data = event["eventData"]
        device_uid = data.get("deviceUID", None)
        battery_state = data.get("batteryState", None)
        device = next((d for d in self.devices if d.uid == device_uid), None)
        if device:
            device.update_battery_state(battery_state)
            self._notify_subscribers(BatteryStateEvent(device, battery_state))

    @auth_if_needed
    async def request(self, url, method, params, raise_on_error=False, get_raw=False):
        "Request data from the Enet Server"
        if self._offline:
            log.debug(
                "Offline mod, skipping request to %s%s %s", self.baseurl, url, method
            )
            return None

        return await self._do_request(url, method, params, raise_on_error, get_raw)

    async def _do_request(
        self, url, method, params, raise_on_error=False, get_raw=False
    ):
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
            if raise_on_error:
                response.raise_for_status()

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
                raise EnetServerError(error_msg)
        else:
            if self._debug_requests:
                log.debug("-> %s %s returned: %s", url, method, json["result"])
        return json["result"]

    async def simple_login(self):
        """Login to the Enet Server"""
        if self._offline:
            return None
        params = dict(userName=self.user, userPassword=self.passwd)
        # Login must bypass the auth decorator to avoid recursive login retries.
        response = await self._do_request(
            URL.MANAGEMENT, "userLogin", params, raise_on_error=True
        )
        response = await self._do_request(
            URL.MANAGEMENT, "setClientRole", dict(clientRole="CR_VISU")
        )
        self._auth_generation += 1
        return response

    async def _restore_event_subscriptions(self):
        """Restore subscriptions belonging to the expired server session."""
        # Use raw requests while holding the auth lock; a failure must propagate.
        if self._battery_events_subscribed:
            await self._do_request(
                URL.VISUALIZATION, "registerEventDeviceBatteryStateChanged", None
            )
        for func_uid in sorted(self._event_subscriptions):
            await self._do_request(
                URL.VISUALIZATION,
                "registerEventOutputDeviceFunctionCalled",
                {"deviceFunctionUID": func_uid},
            )

    async def simple_logout(self):
        """Logout of the Enet Server and close the session"""
        if self._offline:
            return
        try:
            # Only logout if a login succeeded, and bypass the auth decorator
            # so a failed logout never triggers a re-login.
            if self._auth_generation:
                await self._do_request(URL.MANAGEMENT, "userLogout", None)
        except (
            AuthError,
            EnetServerError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            log.debug("Logout failed: %s", err)
        finally:
            await self._session.close()

    async def close(self, logout=True):
        """Stop the background tasks, log out of the Enet Server and close
        the HTTP session"""
        # Stop reading events before logging out, otherwise a pending
        # requestEvents call fails with an auth error and logs in again.
        await self.stop()
        if self._session is None or self._session.closed:
            return
        try:
            if logout and self._auth_generation:
                # Bypass the auth decorator: no point in logging in to log out,
                # and an unreachable server must not block unloading.
                async with asyncio.timeout(5):
                    await self._do_request(URL.MANAGEMENT, "userLogout", None)
        except Exception as e:
            log.debug("Logout failed: %s", e)
        finally:
            await self._session.close()

    async def ping(self):
        """Ping server to keep connection alive"""
        return await self.request(URL.MANAGEMENT, "ping", None)

    async def ping_forever(self):
        """Ping server to keep connection alive, forever"""
        while True:
            try:
                await self.ping()
            except Exception as e:
                log.warning(
                    "Failed to ping server: (%s), retrying in: %s", e, PING_INTERVAL
                )

            await asyncio.sleep(PING_INTERVAL)

    async def get_devices(self, device_uids=None):
        """Get all the devices registered on the server"""
        device_locations = {}
        if not self._raw_json:
            device_locations = await self.get_device_locations()
            if device_uids is None:
                device_uids = list(device_locations.keys())
            params = {
                "deviceUIDs": device_uids,
                "filter": ".+\\.(SCV1|SCV2|SNA|PSN)\\[(.|1.|2.|3.|4.|5.)\\]+",
            }
            result = await self.request(
                URL.VISUALIZATION, "getDevicesWithParameterFilter", params
            )
            self._raw_json = result["devices"]

        devices = []
        for raw_device in self._raw_json:
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
        result = await self.request(URL.VISUALIZATION, "getLocations", params)
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
        result = await self.request(URL.SCENE, "getSceneActionUIDs", None)
        if not result:
            return {}
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
        await self.request(URL.VISUALIZATION, "executeAction", params)

    async def setup_event_subscription(self, func_uid):
        """Subscribe for outputDeviceFunction events"""
        result = await self.request(
            URL.VISUALIZATION,
            "registerEventOutputDeviceFunctionCalled",
            {"deviceFunctionUID": func_uid},
        )
        self._event_subscriptions.add(func_uid)
        return result

    async def setup_event_subscription_battery_state(self):
        """Subscribe for batteryStateChanged events"""
        result = await self.request(
            URL.VISUALIZATION,
            "registerEventDeviceBatteryStateChanged",
            None,
        )
        self._battery_events_subscribed = True
        return result

    async def get_events(self):
        """Poll Enet server for events"""
        try:
            result = await self.request(URL.VISUALIZATION, "requestEvents", None)
            return result
        except aiohttp.ServerTimeoutError:
            return None


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
        self.location = ""
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
