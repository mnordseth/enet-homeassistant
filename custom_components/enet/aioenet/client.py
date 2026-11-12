"""Client for the JSON-RPC API of an Enet Smart Home server from Jung / Gira"""

import logging
import functools
import time
import json
import random

from dataclasses import dataclass

import asyncio
import aiohttp

from ..enums import ButtonEventType
from ..enet_data.enums import ChannelTypeFunctionName

from .const import URL
from .devices import Device, DeviceChannel, create_device
from .errors import AuthError, EnetServerError
from .scenes import EnetScene

log = logging.getLogger(__name__)


ID_FILTER_ALL = "*"

EVENT_DEVICE_BATTERY_STATE_CHANGED = "deviceBatteryStateChanged"
EVENT_OUTPUT_DEVICE_FUNCTION_CALLED = "outputDeviceFunctionCalled"
EVENT_WRITE_VALUES_TO_DEVICE_PARAMETER_COMPLETED = "writeValuesToDeviceParameterCompleted"
# The values of a parameter read from the device: always after a read,
# changed or not. deviceParameterChanged also comes when the value changed.
EVENT_DEVICE_PARAMETER_VALUES_RECEIVED = "deviceParameterValuesReceived"
EVENT_DEVICE_PARAMETER_CHANGED = "deviceParameterChanged"
# Links written to a device, for some channels or the whole device
EVENT_WRITE_CHANNEL_LINKS_COMPLETED = "writeChannelToBeLinksToDeviceCompleted"
EVENT_WRITE_LINKS_COMPLETED = "writeLinksToDeviceCompleted"
# Seconds to wait until the server has programmed a parameter into the device
PARAMETER_WRITE_TIMEOUT = 60
# Seconds to wait until the server has programmed the links into the device
LINK_WRITE_TIMEOUT = 60
# Seconds to wait until the server has read a parameter from the device
PARAMETER_READ_TIMEOUT = 15
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


def normalize_url(url):
    """Return the url of an Enet server in one form, to compare servers."""
    return url.strip().rstrip("/")


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
        self.scenes = []
        self.scenes_read = False  # set when the scenes were read from the server
        self._parameter_writes = {}
        self._parameter_reads = {}
        self._link_writes = {}
        # Parameter reads and writes and link writes go one at a time, the devices are
        # programmed and read by radio
        self._write_lock = asyncio.Lock()
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
            if event["event"] == EVENT_WRITE_VALUES_TO_DEVICE_PARAMETER_COMPLETED:
                # Not a duplicate when the same parameter is written twice
                self._handle_parameter_write_completed(event)
                continue
            if event["event"] in (
                EVENT_DEVICE_PARAMETER_VALUES_RECEIVED,
                EVENT_DEVICE_PARAMETER_CHANGED,
            ):
                self._handle_parameter_read(event)
                continue
            if event["event"] in (EVENT_WRITE_CHANNEL_LINKS_COMPLETED, EVENT_WRITE_LINKS_COMPLETED):
                self._handle_link_write_completed(event)
                continue
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

    def _handle_parameter_write_completed(self, event):
        data = event.get("eventData") or {}
        future = self._parameter_writes.get(data.get("deviceParameterUID"))
        if future is not None and not future.done():
            future.set_result(data)

    def _handle_parameter_read(self, event):
        data = event.get("eventData") or {}
        future = self._parameter_reads.get(data.get("deviceParameterUID"))
        if future is not None and not future.done():
            future.set_result(data.get("values") or [])

    def _handle_link_write_completed(self, event):
        data = event.get("eventData") or {}
        future = self._link_writes.get(data.get("deviceUID"))
        if future is not None and not future.done():
            future.set_result(data)

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

    def get_channel(self, device_uid, channel_number):
        """Return the channel of a device, None if it is not known"""
        uid = f"{device_uid}-{channel_number}"
        for device in self.devices:
            if device.uid == device_uid:
                return next((c for c in device.channels if c.uid == uid), None)
        return None

    async def get_scene_actions(self, only_libenet=True, automatic_scenes=False):
        """Return the scenes of the server as EnetScene objects with their
        members. The scene values are not loaded, see EnetScene.load_values().

        only_libenet: only scenes with libenet in the name.
        automatic_scenes: also the scenes the server creates itself, named
        like ~LocSceneLightsOn_<location>. Those without members are left out.
        """
        result = await self.request(URL.SCENE, "getSceneActionUIDs", None)
        names = {}
        for scene in (result or {}).get("sceneActionUIDs", []):
            name = scene["sceneActionName"]
            if only_libenet:
                if "libenet" not in name:
                    continue
            elif EnetScene.is_automatic_name(name) and not automatic_scenes:
                continue
            names[scene["sceneActionUID"]] = name
        if not names:
            return []

        result = await self.request(
            URL.SCENE, "getSceneActions", {"sceneActionUIDs": list(names)}
        )
        details = {
            action["uid"]: action for action in (result or {}).get("sceneActions", [])
        }
        scenes = [
            EnetScene(self, details.get(uid, {"uid": uid, "name": name}))
            for uid, name in names.items()
        ]
        return [scene for scene in scenes if scene.members or not scene.automatic]

    async def load_scene_values(self):
        """Load the values of all scenes, one scene after the other"""
        for scene in self.scenes:
            try:
                await scene.load_values()
            except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
                log.warning("Failed to load the values of scene %s: %s", scene.name, e)

    async def get_parameter_to_be_values(self, parameter_uid):
        """Return the values configured on the server for a device parameter"""
        result = await self.request(
            URL.VISUALIZATION,
            "getToBeValuesFromDeviceParameter",
            {"deviceParameterUID": parameter_uid},
        )
        return (result or {}).get("toBeValues", [])

    async def get_parameter_current_values(self, parameter_uid):
        """Return the values programmed into the device for a device parameter"""
        result = await self.request(
            URL.VISUALIZATION,
            "getCurrentValuesFromDeviceParameter",
            {"deviceParameterUID": parameter_uid},
        )
        return (result or {}).get("currentValues", [])

    async def write_device_parameter(
        self, parameter_uid, values, timeout=PARAMETER_WRITE_TIMEOUT
    ):
        """Set a device parameter on the server and program it into the device.

        Waits until the server reports that the device has been programmed.
        Raises EnetServerError if programming failed or did not finish in time.
        Writes are sent one at a time, the devices are programmed by radio.
        """
        async with self._write_lock:
            future = asyncio.get_running_loop().create_future()
            self._parameter_writes[parameter_uid] = future
            params = {"deviceParameterUID": parameter_uid}
            events = (
                "EventDeviceParameterChanged",
                "EventSynchronisationCompleted",
            )
            try:
                for event in events:
                    await self.request(URL.VISUALIZATION, f"register{event}", params)
                await self.request(
                    URL.VISUALIZATION,
                    "setToBeValuesToDeviceParameter",
                    {**params, "toBeValues": values},
                )
                await self.request(
                    URL.VISUALIZATION, "writeToBeValuesToDeviceParameter", params
                )
                try:
                    async with asyncio.timeout(timeout):
                        result = await future
                except TimeoutError:
                    # The event may have been lost, ie after a new login
                    current = await self.get_parameter_current_values(parameter_uid)
                    if current and current[0].get("value") == values[0]["value"]:
                        return
                    raise EnetServerError(
                        f"The device was not programmed within {timeout} seconds"
                    ) from None
                if not result.get("success", False):
                    raise EnetServerError(
                        "Programming the device failed: "
                        f"error {result.get('errorCode')} {result.get('errorData', '')}".strip()
                    )
            finally:
                self._parameter_writes.pop(parameter_uid, None)
                for event in events:
                    try:
                        await self.request(
                            URL.VISUALIZATION, f"unregister{event}", params
                        )
                    except Exception as e:  # noqa: BLE001 - best effort cleanup
                        log.debug("Failed to unregister %s: %s", event, e)

    async def read_device_parameter(self, parameter_uid, timeout=PARAMETER_READ_TIMEOUT):
        """Read a device parameter from the device into the server, so both its
        configured and its programmed value are the value in the device.

        Returns the values read. Raises EnetServerError if the device did not
        answer in time.
        """
        async with self._write_lock:
            future = asyncio.get_running_loop().create_future()
            self._parameter_reads[parameter_uid] = future
            params = {"deviceParameterUID": parameter_uid}
            events = (
                "EventDeviceParameterChanged",
                "EventSynchronisationCompleted",
            )
            try:
                for event in events:
                    await self.request(URL.VISUALIZATION, f"register{event}", params)
                await self.request(
                    URL.VISUALIZATION, "readValuesFromDeviceParameter", params
                )
                try:
                    async with asyncio.timeout(timeout):
                        return await future
                except TimeoutError:
                    raise EnetServerError(
                        f"The device did not answer within {timeout} seconds"
                    ) from None
            finally:
                self._parameter_reads.pop(parameter_uid, None)
                for event in events:
                    try:
                        await self.request(
                            URL.VISUALIZATION, f"unregister{event}", params
                        )
                    except Exception as e:  # noqa: BLE001 - best effort cleanup
                        log.debug("Failed to unregister %s: %s", event, e)

    async def write_links(self, device_uid, channel_numbers=None, timeout=LINK_WRITE_TIMEOUT):
        """Program the links the server has configured for channels of a device,
        or for the whole device when channel_numbers is None, into the device.
        Adding a channel to a scene or removing it only configures its links on
        the server.

        Needs the commissioning client role, which is set for this request
        only. Waits until the server reports that the device has been
        programmed, and raises EnetServerError if that failed or did not finish
        in time.
        """
        async with self._write_lock:
            future = asyncio.get_running_loop().create_future()
            self._link_writes[device_uid] = future
            try:
                await self.request(
                    URL.MANAGEMENT, "setClientRole", {"clientRole": "CR_IBN"}
                )
                try:
                    if channel_numbers is None:
                        await self.request(
                            URL.COMMISSIONING, "writeToBeLinksToDevice", {"deviceUID": device_uid}
                        )
                    else:
                        await self.request(
                            URL.COMMISSIONING,
                            "writeChannelToBeLinksToDevice",
                            {"deviceUID": device_uid, "channelNumbers": channel_numbers},
                        )
                finally:
                    await self.request(
                        URL.MANAGEMENT, "setClientRole", {"clientRole": "CR_VISU"}
                    )
                try:
                    async with asyncio.timeout(timeout):
                        result = await future
                except TimeoutError:
                    raise EnetServerError(
                        f"The links were not programmed within {timeout} seconds"
                    ) from None
                if not result.get("success", False):
                    raise EnetServerError(
                        "Programming the links failed: "
                        f"error {result.get('errorCode')} {result.get('errorData', '')}".strip()
                    )
            finally:
                self._link_writes.pop(device_uid, None)

    async def get_scene_action(self, scene_uid):
        """Return a scene of the server with its members, None if it is gone"""
        result = await self.request(
            URL.SCENE, "getSceneActions", {"sceneActionUIDs": [scene_uid]}
        )
        actions = (result or {}).get("sceneActions") or []
        return actions[0] if actions else None

    async def add_scene_member(self, scene_uid, device_uid, channel_number):
        """Add an actuator channel to a scene and program its links"""
        result = await self.request(
            URL.SCENE,
            "addActorChannelToSceneAction",
            {"sceneActionUID": scene_uid, "deviceUID": device_uid,
             "channelNumber": channel_number},
        )
        link_result = (result or {}).get("linkResult")
        if link_result != "OK":
            raise EnetServerError(f"The channel could not be added: {link_result}")
        await self.write_links(device_uid, [channel_number])

    async def remove_scene_member(self, scene_uid, device_uid, channel_number):
        """Remove an actuator channel from a scene and remove its links from the device"""
        await self.request(
            URL.SCENE,
            "removeActorChannelFromSceneAction",
            {"sceneActionUID": scene_uid, "deviceUID": device_uid,
             "channelNumber": channel_number},
        )
        await self.write_links(device_uid, [channel_number])

    async def store_scene_values(self, scene_uid):
        """Make the members of a scene store their current state in the scene.

        The actuators learn it from one scene telegram; the values configured
        on the server are not changed, see read_device_parameter().
        """
        await self.request(
            URL.SCENE, "storeActualSceneValues", {"sceneActionUID": scene_uid}
        )

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
