# /// script
# requires-python = ">=3.14"
# dependencies = ["homeassistant"]
# ///
"""Set up the integration in a real Home Assistant and use every platform.

Unlike the other tests, Home Assistant is not stubbed: a minimal instance is
started in a temporary config directory with the installed homeassistant
package. Run it against a new Home Assistant release or beta to find
breaking changes before users do, e.g.:

    uv run --with "homeassistant==2026.12.0b0" tests/test_home_assistant.py

The Enet server is replaced by the client in offline mode, loading its devices
from fixtures/enet_devices.json (an anonymized server dump, one device per
device type). Requests the integration sends are recorded. To add devices
from a user's diagnostics file, see scripts/make_test_fixture.py.

Set UPDATE_EXPECTED=1 to rewrite fixtures/expected_entities.json after an
intended change of the entities.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEVICES = FIXTURES / "enet_devices.json"
EXPECTED = FIXTURES / "expected_entities.json"

sys.path.insert(0, str(REPO))

from homeassistant import bootstrap, config_entries, core, loader  # noqa: E402
from homeassistant.components.cover import CoverEntityFeature  # noqa: E402
from homeassistant.const import ATTR_FRIENDLY_NAME, STATE_UNAVAILABLE  # noqa: E402
from homeassistant.core_config import async_process_ha_core_config  # noqa: E402
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402

# Imported before an event loop runs: enet_data loads its data with asyncio.run()
import custom_components.enet as enet  # noqa: E402
from custom_components.enet import aioenet, device_trigger, diagnostics  # noqa: E402

SCENES = {
    "sceneActionUIDs": [
        {"sceneActionUID": "scene-1", "sceneActionName": "[libenet] Evening"},
        {"sceneActionUID": "scene-2", "sceneActionName": "Made in the Enet app"},
        {"sceneActionUID": "scene-3", "sceneActionName": "~LocSceneLightsOn_Kitchen"},
        {"sceneActionUID": "scene-4", "sceneActionName": "~LocSceneBlindsOn_Kitchen"},
    ]
}
SCENE_NAMES = {s["sceneActionUID"]: s["sceneActionName"] for s in SCENES["sceneActionUIDs"]}


SCENE_SLOT = 5


class OfflineEnetClient(aioenet.EnetClient):
    """Client reading the devices from the fixture and recording requests.

    The scene "Evening" has a dimmer, a switch and a blind with slats from the
    fixture as members. Scene parameters are stored, and a write is reported
    with a writeValuesToDeviceParameterCompleted event like the real server.

    storeActualSceneValues makes the actuators learn the values in `learns`
    (parameter uid -> value), and readValuesFromDeviceParameter copies the
    value in the actuator to the server and reports it with a
    deviceParameterValuesReceived event, like the real server.
    """

    def __init__(self, url, user, passwd):
        super().__init__(url, user, passwd, noconnect=True, load_file=str(DEVICES))
        self.requests = []
        # uid -> {"toBe": value dict, "current": value dict, "device": value dict}
        self.parameters = {}
        self.write_success = True
        self.learns = {}
        self.read_fails = False
        self.added = {}  # scene uid -> channels added to the scene
        self.removed = set()  # (scene uid, channel uid) removed from the scene
        self.link_result = "OK"
        self.programming_turns_off = False  # programming restarts the actuator, it comes back off

    def scene_channels(self):
        """The dimmer, switch and blind of the scene"""
        mode = aioenet.ChannelApplicationMode
        channels = [c for d in self.devices for c in d.channels]
        dimmer = next(c for c in channels if c.application_mode == mode.LIGHT_DIMMING)
        switch = next(c for c in channels if c.application_mode == mode.SWITCHING)
        blind = next(
            c for c in channels
            if c.application_mode == mode.BLINDS and c.get_operation_mode() == "BLINDS"
        )
        return dimmer, switch, blind

    def scene_members(self, scene_uid):
        """(slot, [(channel, [(parameter type, value type, value)])]) of a scene"""
        slot, members = self._initial_scene_members(scene_uid)
        members = [(c, p) for c, p in members if (scene_uid, c.uid) not in self.removed]
        for channel in self.added.get(scene_uid, []):
            # A new member has the default values of the scene slot
            mode = aioenet.ChannelApplicationMode
            match channel.application_mode:
                case mode.LIGHT_DIMMING:
                    params = [("PT_INDA.SCV1", "VT_SCALING_RANGE_0_100_DEF_100", 100)]
                case mode.BLINDS:
                    params = [("PT_INBA.SCV1", "VT_SCALING_RANGE_0_100_DEF_0", 0),
                              ("PT_INBA.SCV2", "VT_SCALING_RANGE_0_100_DEF_0", 0)]
                case _:
                    params = [("PT_INSA.SCV1", "VT_SCV_SWITCH_DEF_ON", "ON")]
            members.append((channel, params))
        return slot, members

    def _initial_scene_members(self, scene_uid):
        dimmer, switch, blind = self.scene_channels()
        dimmer_level = ("PT_INDA.SCV1", "VT_SCALING_RANGE_0_100_DEF_100", 60)
        match scene_uid:
            case "scene-1":
                return SCENE_SLOT, [
                    (dimmer, [dimmer_level]),
                    (switch, [("PT_INSA.SCV1", "VT_SCV_SWITCH_DEF_ON", "ON")]),
                    (blind, [("PT_INBA.SCV1", "VT_SCALING_RANGE_0_100_DEF_0", 30),
                             ("PT_INBA.SCV2", "VT_SCALING_RANGE_0_100_DEF_0", 10)]),
                ]
            case "scene-3":  # automatic "lights on" scene of a location
                return 16, [(dimmer, [dimmer_level])]
        return SCENE_SLOT, []  # scene-2 and scene-4 have no members

    def channel(self, device_uid, channel_number):
        return self.get_channel(device_uid, channel_number)

    def scene_actions(self, scene_uids):
        actions = []
        for scene_uid in scene_uids:
            slot, members = self.scene_members(scene_uid)
            device_channels = []
            for channel, params in members:
                scene_parameters = []
                for type_id, value_type, value in params:
                    uid = f"{channel.uid}-{type_id}"
                    if slot != SCENE_SLOT:
                        uid += f"-{slot}"
                    value = {"valueTypeID": value_type, "value": value}
                    self.parameters.setdefault(uid, {"toBe": value, "current": value})
                    scene_parameters.append(
                        {"deviceParameterUID": uid, "deviceParameterTypeID": f"{type_id}[{slot}]"}
                    )
                device_channels.append({
                    "deviceUID": channel.device.uid,
                    "channelNumber": channel.channel["no"],
                    "sceneParameters": scene_parameters,
                })
            actions.append({"uid": scene_uid, "name": SCENE_NAMES[scene_uid],
                            "deviceChannels": device_channels})
        return {"sceneActions": actions}

    async def request(self, url, method, params, *args, **kwargs):
        self.requests.append((method, params))
        uid = (params or {}).get("deviceParameterUID")
        match method:
            case "getSceneActionUIDs":
                return SCENES
            case "getSceneActions":
                return self.scene_actions(params["sceneActionUIDs"])
            case "getToBeValuesFromDeviceParameter":
                return {"toBeValues": [self.parameters[uid]["toBe"]]}
            case "getCurrentValuesFromDeviceParameter":
                return {"currentValues": [self.parameters[uid]["current"]]}
            case "setToBeValuesToDeviceParameter":
                self.parameters[uid]["toBe"] = params["toBeValues"][0]
            case "writeToBeValuesToDeviceParameter":
                if self.programming_turns_off:
                    self._turn_off(lambda channel: uid.startswith(f"{channel.uid}-"))
                if self.write_success:
                    self.parameters[uid]["current"] = self.parameters[uid]["toBe"]
                    self.parameters[uid]["device"] = self.parameters[uid]["toBe"]
                self._send_event("writeValuesToDeviceParameterCompleted",
                                 {"deviceParameterUID": uid,
                                  "success": self.write_success,
                                  "errorCode": 0 if self.write_success else 7,
                                  "errorData": ""})
            case "storeActualSceneValues":
                for learned_uid, value in self.learns.items():
                    param = self.parameters[learned_uid]
                    param["device"] = {**param["current"], "value": value}
            case "readValuesFromDeviceParameter":
                if self.read_fails:
                    raise aioenet.EnetServerError("readValuesFromDeviceParameter failed")
                param = self.parameters[uid]
                value = param.get("device", param["current"])
                param["toBe"] = param["current"] = value
                self._send_event("deviceParameterValuesReceived",
                                 {"deviceParameterUID": uid, "values": [value]})
            case "addActorChannelToSceneAction":
                if self.link_result == "OK":
                    channel = self.channel(params["deviceUID"], params["channelNumber"])
                    self.removed.discard((params["sceneActionUID"], channel.uid))
                    self.added.setdefault(params["sceneActionUID"], []).append(channel)
                return {"linkResult": self.link_result}
            case "removeActorChannelFromSceneAction":
                channel = self.channel(params["deviceUID"], params["channelNumber"])
                self.removed.add((params["sceneActionUID"], channel.uid))
                added = self.added.get(params["sceneActionUID"], [])
                if channel in added:
                    added.remove(channel)
                # The scene values are reset to the defaults, on the server only
                for param_uid, param in self.parameters.items():
                    if param_uid.startswith(f"{channel.uid}-"):
                        value_type = param["toBe"]["valueTypeID"]
                        default = value_type.rsplit("_DEF_", 1)[1]
                        param["toBe"] = {"valueTypeID": value_type,
                                         "value": int(default) if default.isdigit() else default}
            case "writeChannelToBeLinksToDevice" | "writeToBeLinksToDevice":
                if self.programming_turns_off:
                    self._turn_off(lambda channel: channel.device.uid == params["deviceUID"])
                event = ("writeChannelToBeLinksToDeviceCompleted"
                         if method == "writeChannelToBeLinksToDevice" else "writeLinksToDeviceCompleted")
                self._send_event(event, {"deviceUID": params["deviceUID"], "success": True,
                                         "errorCode": 0, "errorData": ""})
        return None

    def _turn_off(self, matches):
        """The channels restart and report that they are off"""
        for channel in (c for d in self.devices for c in d.channels if matches(c)):
            if hasattr(channel, "set_current_value"):
                channel.set_current_value(aioenet.ChannelTypeFunctionName.ON_OFF, False)
                channel.set_current_value(aioenet.ChannelTypeFunctionName.BRIGHTNESS, 0)

    def _send_event(self, name, data):
        event = {"sequenceNumber": 0, "event": name, "eventData": data}
        asyncio.get_running_loop().call_soon(
            asyncio.ensure_future, self.handle_events({"events": [event]})
        )


class RecordingHandler(logging.Handler):
    """Collect the warnings and errors logged while Home Assistant runs."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        # Logged for every custom integration
        if "which has not been tested by Home Assistant" in record.getMessage():
            return
        # Slow steps reported by the event loop in debug mode depend on the
        # machine. Blocking calls are still found: Home Assistant logs them.
        if record.name == "asyncio" and record.getMessage().startswith("Executing <"):
            return
        self.records.append(record)


class HomeAssistantTests(unittest.IsolatedAsyncioTestCase):
    maxDiff = None

    async def asyncSetUp(self):
        self._config_dir = tempfile.TemporaryDirectory()
        self.log = RecordingHandler()
        logging.getLogger().addHandler(self.log)
        self._patch = patch.object(enet, "EnetClient", OfflineEnetClient)
        self._patch.start()
        self._settle_patch = patch.object(aioenet.scenes, "LEARN_SETTLE_SECONDS", 0)
        self._settle_patch.start()
        self._restore_patch = patch.object(aioenet.scenes, "RESTORE_DELAY_SECONDS", 0)
        self._restore_patch.start()

        hass = self.hass = core.HomeAssistant(self._config_dir.name)
        hass.config.skip_pip = True
        hass.config_entries = config_entries.ConfigEntries(hass, {})
        loader.async_setup(hass)
        self.assertTrue(await bootstrap.async_load_base_functionality(hass))
        await async_process_ha_core_config(hass, {})
        hass.set_state(core.CoreState.running)

        self.entry = config_entries.ConfigEntry(
            domain="enet",
            title="Enet Smart Home",
            data={"url": "http://enet.invalid", "username": "admin", "password": "secret"},
            source=config_entries.SOURCE_USER,
            version=1,
            minor_version=1,
            options={},
            unique_id=None,
            discovery_keys={},
            subentries_data=None,
        )
        await hass.config_entries.async_add(self.entry)
        await hass.async_block_till_done()
        self.assertEqual(self.entry.state, config_entries.ConfigEntryState.LOADED)
        self.hub = self.entry.runtime_data

    async def asyncTearDown(self):
        if self.entry.state is config_entries.ConfigEntryState.LOADED:
            await self.hass.config_entries.async_unload(self.entry.entry_id)
        await self.hass.async_stop(force=True)
        self._patch.stop()
        self._settle_patch.stop()
        self._restore_patch.stop()
        logging.getLogger().removeHandler(self.log)
        self._config_dir.cleanup()

    def entities(self, domain=None):
        ent_reg = er.async_get(self.hass)
        return [
            entry
            for entry in er.async_entries_for_config_entry(ent_reg, self.entry.entry_id)
            if domain is None or entry.domain == domain
        ]

    def entity_id(self, domain, name):
        """Return the entity id of the entity with the friendly name."""
        for entry in self.entities(domain):
            state = self.hass.states.get(entry.entity_id)
            if state.attributes.get(ATTR_FRIENDLY_NAME) == name:
                return entry.entity_id
        self.fail(f"No {domain} entity named {name!r}")

    def device(self, uid):
        """Return the device entry of an Enet device uid."""
        dev_reg = dr.async_get(self.hass)
        if hasattr(dev_reg, "async_get_device_by_identifier"):
            return dev_reg.async_get_device_by_identifier(("enet", uid), self.entry.entry_id)
        return dev_reg.async_get_device(identifiers={("enet", uid)})

    def requests(self, method):
        return [params for m, params in self.hub.requests if m == method]

    async def call(self, domain, service, entity_id, **data):
        self.hub.requests.clear()
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity_id, **data}, blocking=True
        )
        return self.requests("callInputDeviceFunction")

    def assert_no_warnings(self):
        messages = [f"{r.name}: {r.getMessage()}" for r in self.log.records]
        self.assertEqual(messages, [], "Home Assistant logged warnings or errors")

    async def test_setup_adds_all_entities_without_warnings(self):
        """Every entity is added, with a device and a state, and nothing is logged.

        A change in Home Assistant that makes it reject an entity, like the
        DeviceInfo of scenes in 2026.11, fails here.
        """
        self.assert_no_warnings()

        actual = {}
        for entry in self.entities():
            state = self.hass.states.get(entry.entity_id)
            self.assertIsNotNone(state, entry.entity_id)
            self.assertNotEqual(state.state, STATE_UNAVAILABLE, entry.entity_id)
            self.assertIsNotNone(entry.device_id, entry.entity_id)
            actual[entry.unique_id] = {
                "entity_id": entry.entity_id,
                "name": state.attributes.get(ATTR_FRIENDLY_NAME),
            }

        if os.environ.get("UPDATE_EXPECTED"):
            EXPECTED.write_text(
                json.dumps(dict(sorted(actual.items())), indent=2, ensure_ascii=False)
                + "\n"
            )
        expected = json.loads(EXPECTED.read_text())
        self.assertEqual(
            actual, expected, "Entities changed, set UPDATE_EXPECTED=1 if intended"
        )

    async def test_devices_are_registered_below_the_controller(self):
        dev_reg = dr.async_get(self.hass)
        controller = self.device("Enet Controller")
        self.assertIsNotNone(controller)
        devices = dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id)
        self.assertEqual(len(devices), len(self.hub.devices) + 1)
        for device in devices:
            if device.id != controller.id:
                self.assertEqual(device.via_device_id, controller.id, device.name)
                self.assertTrue(device.serial_number, device.name)

    async def test_light_actions(self):
        dimmer = next(
            e for e in self.entities("light")
            if "brightness" in self.hass.states.get(e.entity_id).attributes["supported_color_modes"]
        ).entity_id

        # Only the brightness is sent, the server reports the light turning on
        sent = await self.call("light", "turn_on", dimmer, brightness=128)
        self.assertEqual(sent[0]["values"][0]["value"], 50)

        sent = await self.call("light", "turn_on", dimmer)
        self.assertIs(sent[0]["values"][0]["value"], True)
        self.assertEqual(self.hass.states.get(dimmer).state, "on")

        sent = await self.call("light", "turn_off", dimmer)
        self.assertIs(sent[0]["values"][0]["value"], False)
        self.assertEqual(self.hass.states.get(dimmer).state, "off")
        self.assert_no_warnings()

    async def test_switch_actions(self):
        switch = self.entities("switch")[0].entity_id
        self.assertIs((await self.call("switch", "turn_on", switch))[0]["values"][0]["value"], True)
        self.assertEqual(self.hass.states.get(switch).state, "on")
        self.assertIs((await self.call("switch", "turn_off", switch))[0]["values"][0]["value"], False)
        self.assertEqual(self.hass.states.get(switch).state, "off")
        self.assert_no_warnings()

    async def test_battery_state(self):
        """Battery states are lower case, the translation keys Home Assistant requires."""
        entry = next(e for e in self.entities("sensor") if e.unique_id.endswith("_battery_state"))
        state = self.hass.states.get(entry.entity_id)
        self.assertEqual(state.attributes["options"], ["no_state", "battery_ok", "battery_weak"])
        self.assertIn(state.state, state.attributes["options"])

        await self.hub.handle_events({"events": [{
            "sequenceNumber": 0,
            "event": "deviceBatteryStateChanged",
            "eventData": {"deviceUID": entry.unique_id.removesuffix("_battery_state"),
                          "batteryState": "BATTERY_WEAK"},
        }]})
        await self.hass.async_block_till_done()
        self.assertEqual(self.hass.states.get(entry.entity_id).state, "battery_weak")
        self.assert_no_warnings()

    async def test_cover_actions(self):
        covers = [e.entity_id for e in self.entities("cover")]
        tilt = [
            c for c in covers
            if self.hass.states.get(c).attributes["supported_features"]
            & CoverEntityFeature.SET_TILT_POSITION
        ]
        self.assertTrue(tilt, "the fixture has no blind with tilt")

        for cover in covers:
            self.assertEqual(len(await self.call("cover", "open_cover", cover)), 1)
            self.assertEqual(len(await self.call("cover", "close_cover", cover)), 1)
            self.assertEqual(len(await self.call("cover", "stop_cover", cover)), 1)
            sent = await self.call("cover", "set_cover_position", cover, position=30)
            self.assertEqual(sent[0]["values"][0]["value"], 70)  # Enet counts from open
            self.assertEqual(self.hass.states.get(cover).attributes["current_position"], 30)

        for cover in tilt:
            sent = await self.call("cover", "set_cover_tilt_position", cover, tilt_position=20)
            self.assertEqual(sent[0]["values"][0]["value"], 80)
            self.assertEqual(len(await self.call("cover", "open_cover_tilt", cover)), 1)
            self.assertEqual(len(await self.call("cover", "close_cover_tilt", cover)), 1)
            self.assertEqual(len(await self.call("cover", "stop_cover_tilt", cover)), 1)
        self.assert_no_warnings()

    async def set_scenes_option(self, value):
        """Change the scenes option in the options flow, the entry reloads"""
        result = await self.hass.config_entries.options.async_init(self.entry.entry_id)
        self.assertEqual(result["type"], "form")
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"], {"scenes": value}
        )
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.state, config_entries.ConfigEntryState.LOADED)
        self.hub = self.entry.runtime_data  # the reload creates a new client

    def scene_entities(self):
        return (
            sorted(e.unique_id for e in self.entities("scene")),
            sorted(e.unique_id for e in self.entities("button")),
        )

    async def test_scenes_option_adds_and_removes_scenes(self):
        await self.set_scenes_option("all")
        self.assertEqual(self.scene_entities(), (
            ["scene-1", "scene-2"], ["scene-1-save", "scene-2-save"]))

        # The automatic scenes too, but not the one without members
        await self.set_scenes_option("all_with_location")
        self.assertEqual(self.scene_entities(), (
            ["scene-1", "scene-2", "scene-3"], ["scene-1-save", "scene-2-save", "scene-3-save"]))
        self.entity_id("scene", "Enet Smart Home Server Kitchen lights on")
        self.entity_id("scene", "Enet Smart Home Server Made in the Enet app")

        await self.set_scenes_option("libenet")
        self.assertEqual(self.scene_entities(), (["scene-1"], ["scene-1-save"]))
        self.assert_no_warnings()

    async def test_scenes_are_kept_when_they_cannot_be_read(self):
        await self.set_scenes_option("all")
        with patch.object(OfflineEnetClient, "get_scene_actions",
                          side_effect=aioenet.EnetServerError("busy")):
            await self.set_scenes_option("libenet")
        self.assertEqual(self.scene_entities(), (
            ["scene-1", "scene-2"], ["scene-1-save", "scene-2-save"]))
        self.assertEqual(
            [r.getMessage() for r in self.log.records],
            ["Failed to read the scenes of the Enet server: busy"],
        )

    async def test_only_libenet_scenes_are_added_and_activated(self):
        scenes = self.entities("scene")
        self.assertEqual([e.unique_id for e in scenes], ["scene-1"])
        self.assertEqual(self.requests("getSceneActions"), [{"sceneActionUIDs": ["scene-1"]}])
        scene = self.entity_id("scene", "Enet Smart Home Server Evening")

        self.hub.requests.clear()
        await self.hass.services.async_call(
            "scene", "turn_on", {"entity_id": scene}, blocking=True
        )
        self.assertEqual(self.requests("executeAction"), [{"actionUID": "scene-1"}])
        self.assert_no_warnings()

    def scene_members(self):
        """Entity ids of the dimmer, switch and blind of the scene"""
        ent_reg = er.async_get(self.hass)
        dimmer, switch, blind = self.hub.scene_channels()
        return (
            ent_reg.async_get_entity_id("light", "enet", dimmer.uid),
            ent_reg.async_get_entity_id("switch", "enet", switch.uid),
            ent_reg.async_get_entity_id("cover", "enet", blind.uid),
        )

    def scene_state(self):
        return self.hass.states.get(self.entity_id("scene", "Enet Smart Home Server Evening"))

    def written(self):
        """(parameter uid, value) of every setToBeValuesToDeviceParameter"""
        return [
            (p["deviceParameterUID"], p["toBeValues"][0]["value"])
            for p in self.requests("setToBeValuesToDeviceParameter")
        ]

    async def set_scene_level(self, **data):
        self.hub.requests.clear()
        await self.hass.services.async_call(
            "enet", "set_scene_level",
            {"entity_id": self.scene_state().entity_id, **data}, blocking=True,
        )

    async def test_scene_lists_members_and_levels(self):
        light, switch, cover = self.scene_members()
        attributes = self.scene_state().attributes
        self.assertEqual(attributes["entity_id"], [light, switch, cover])
        self.assertEqual(
            attributes["levels"],
            {light: 60, switch: "on", cover: {"position": 70, "tilt_position": 90}},
        )
        self.assert_no_warnings()

    async def test_set_scene_level_writes_and_programs_the_device(self):
        light, switch, cover = self.scene_members()
        dimmer, _, blind = self.hub.scene_channels()

        await self.set_scene_level(member=light, brightness_pct=23)
        uid = f"{dimmer.uid}-PT_INDA.SCV1"
        self.assertEqual(self.written(), [(uid, 23)])
        self.assertEqual(
            self.requests("setToBeValuesToDeviceParameter")[0]["toBeValues"],
            [{"valueTypeID": "VT_SCALING_RANGE_0_100_DEF_100", "value": 23}],
        )
        self.assertEqual(self.requests("writeToBeValuesToDeviceParameter"),
                         [{"deviceParameterUID": uid}])
        self.assertEqual(self.scene_state().attributes["levels"][light], 23)

        # The same parameter again: the second completion event is not a duplicate
        await self.set_scene_level(member=light, state="off")
        self.assertEqual(self.written(), [(uid, 0)])

        await self.set_scene_level(member=cover, position=40, tilt_position=100)
        self.assertEqual(self.written(), [(f"{blind.uid}-PT_INBA.SCV1", 60),
                                          (f"{blind.uid}-PT_INBA.SCV2", 0)])
        self.assertEqual(self.scene_state().attributes["levels"][cover],
                         {"position": 40, "tilt_position": 100})

        await self.set_scene_level(member=switch, state="off")
        self.assertEqual(self.scene_state().attributes["levels"][switch], "off")
        self.assert_no_warnings()

    async def test_set_scene_level_rejects_invalid_values(self):
        light, switch, cover = self.scene_members()
        other_light = next(
            e.entity_id for e in self.entities("light") if e.entity_id != light
        )
        for data in (
            {"member": cover, "brightness_pct": 50},
            {"member": light, "position": 50},
            {"member": light},
            {"member": other_light, "brightness_pct": 50},
        ):
            with self.subTest(data=data), self.assertRaises(ServiceValidationError):
                await self.set_scene_level(**data)
        self.assertEqual(self.written(), [])

    def set_scene_member_states(self):
        """Dim the light to 35 and switch the switch off; the blind is as in
        the scene. Returns the scene parameter uids of the dimmer and switch."""
        dimmer, switch_channel, blind = self.hub.scene_channels()
        functions = aioenet.ChannelTypeFunctionName
        dimmer.set_current_value(functions.ON_OFF, True)
        dimmer.set_current_value(functions.BRIGHTNESS, 35)
        switch_channel.current_values[
            switch_channel.get_channel_configuration_entry("outputDeviceFunctions", functions.ON_OFF)
        ] = {"value": False}
        blind.set_current_value(functions.COVER_POSITION, 30)  # as in the scene
        blind.set_current_value(functions.TILT_POSITION, 10)
        return f"{dimmer.uid}-PT_INDA.SCV1", f"{switch_channel.uid}-PT_INSA.SCV1"

    async def save_scene(self):
        self.hub.requests.clear()
        button = self.entity_id("button", "Enet Smart Home Server Save Evening")
        await self.hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )

    def read_back(self):
        """Parameter uids read back from the actuators"""
        return [p["deviceParameterUID"] for p in self.requests("readValuesFromDeviceParameter")]

    async def test_save_scene_learns_and_reads_back_the_current_state(self):
        light, switch, _ = self.scene_members()
        dimmer_uid, switch_uid = self.set_scene_member_states()
        self.hub.learns = {dimmer_uid: 35, switch_uid: "OFF"}

        await self.save_scene()
        # One scene telegram makes the members learn, the changed ones are
        # read back, and nothing is programmed
        self.assertEqual(self.requests("storeActualSceneValues"), [{"sceneActionUID": "scene-1"}])
        self.assertEqual(self.read_back(), [dimmer_uid, switch_uid])
        self.assertEqual(self.written(), [])
        self.assertEqual(self.hub.parameters[dimmer_uid]["toBe"]["value"], 35)
        levels = self.scene_state().attributes["levels"]
        self.assertEqual((levels[light], levels[switch]), (35, "off"))

        # Nothing changed since, nothing is sent
        self.hub.requests.clear()
        await self.hass.services.async_call(
            "enet", "save_scene", {"entity_id": self.scene_state().entity_id}, blocking=True
        )
        self.assertEqual(self.requests("storeActualSceneValues"), [])
        self.assertEqual(self.read_back(), [])
        self.assert_no_warnings()

    async def test_save_scene_programs_a_member_that_learned_another_value(self):
        light, switch, _ = self.scene_members()
        dimmer_uid, switch_uid = self.set_scene_member_states()
        # The switch did not get the telegram and still has the scene value
        self.hub.learns = {dimmer_uid: 35}

        await self.save_scene()
        self.assertEqual(self.read_back(), [dimmer_uid, switch_uid])
        self.assertEqual(self.written(), [(switch_uid, "OFF")])
        levels = self.scene_state().attributes["levels"]
        self.assertEqual((levels[light], levels[switch]), (35, "off"))
        self.assertIn("learned ON instead of OFF", self.log.records[-1].getMessage())

    async def test_save_scene_programs_the_members_when_reading_fails(self):
        light, switch, _ = self.scene_members()
        dimmer_uid, switch_uid = self.set_scene_member_states()
        self.hub.learns = {dimmer_uid: 35, switch_uid: "OFF"}
        self.hub.read_fails = True

        await self.save_scene()
        self.assertEqual(self.written(), [(dimmer_uid, 35), (switch_uid, "OFF")])
        levels = self.scene_state().attributes["levels"]
        self.assertEqual((levels[light], levels[switch]), (35, "off"))
        self.assertTrue(self.log.records)

    def other_dimmer(self):
        """(entity id, channel) of a dimmer that is not in the scene"""
        dimmer, _, _ = self.hub.scene_channels()
        channel = next(
            c for d in self.hub.devices for c in d.channels
            if c.application_mode == aioenet.ChannelApplicationMode.LIGHT_DIMMING
            and c.uid != dimmer.uid
        )
        ent_reg = er.async_get(self.hass)
        return ent_reg.async_get_entity_id("light", "enet", channel.uid), channel

    async def change_member(self, service, **data):
        self.hub.requests.clear()
        await self.hass.services.async_call(
            "enet", service, {"entity_id": self.scene_state().entity_id, **data}, blocking=True
        )

    def link_requests(self):
        """The requests adding/removing members and writing their links, in order"""
        return [
            (method, params) for method, params in self.hub.requests
            if method in ("addActorChannelToSceneAction", "removeActorChannelFromSceneAction",
                          "setClientRole", "writeChannelToBeLinksToDevice",
                          "writeToBeLinksToDevice")
        ]

    async def test_add_scene_member_at_its_current_state(self):
        entity_id, channel = self.other_dimmer()
        functions = aioenet.ChannelTypeFunctionName
        channel.set_current_value(functions.ON_OFF, True)
        channel.set_current_value(functions.BRIGHTNESS, 45)
        # The state before adding is stored, not the state after the restart
        self.hub.programming_turns_off = True

        await self.change_member("add_scene_member", member=entity_id)
        device_uid, number = channel.device.uid, channel.channel["no"]
        self.assertEqual(self.link_requests(), [
            ("addActorChannelToSceneAction",
             {"sceneActionUID": "scene-1", "deviceUID": device_uid, "channelNumber": number}),
            ("setClientRole", {"clientRole": "CR_IBN"}),
            ("writeChannelToBeLinksToDevice", {"deviceUID": device_uid, "channelNumbers": [number]}),
            ("setClientRole", {"clientRole": "CR_VISU"}),
        ])
        self.assertEqual(self.written(), [(f"{channel.uid}-PT_INDA.SCV1", 45)])
        attributes = self.scene_state().attributes
        self.assertIn(entity_id, attributes["entity_id"])
        self.assertEqual(attributes["levels"][entity_id], 45)
        # Programmed twice, switched back on to its level once, at the end
        self.assertEqual(self.light_commands(channel), [45])
        self.assert_no_warnings()

    def light_commands(self, channel):
        """Values sent to switch or dim a channel"""
        uids = {f["uid"] for f in channel.input_functions.values()}
        return [
            p["values"][0]["value"] for p in self.requests("callInputDeviceFunction")
            if p["deviceFunctionUID"] in uids
        ]

    async def test_removed_member_is_switched_back_on(self):
        light, _, _ = self.scene_members()
        dimmer, _, _ = self.hub.scene_channels()
        functions = aioenet.ChannelTypeFunctionName
        dimmer.set_current_value(functions.ON_OFF, True)
        dimmer.set_current_value(functions.BRIGHTNESS, 70)
        self.hub.programming_turns_off = True

        await self.change_member("remove_scene_member", member=light)
        # The default the server reset its scene value to is programmed too,
        # and the light is switched back on once
        uid = f"{dimmer.uid}-PT_INDA.SCV1"
        self.assertEqual(self.written(), [(uid, 100)])
        self.assertEqual(self.hub.parameters[uid]["current"]["value"], 100)
        self.assertEqual(self.light_commands(dimmer), [70])
        self.assert_no_warnings()

    async def test_lights_that_were_off_stay_off_after_programming(self):
        light, _, _ = self.scene_members()
        dimmer, _, _ = self.hub.scene_channels()
        dimmer.set_current_value(aioenet.ChannelTypeFunctionName.ON_OFF, False)
        self.hub.programming_turns_off = True

        await self.set_scene_level(member=light, brightness_pct=23)
        self.assertEqual(self.written(), [(f"{dimmer.uid}-PT_INDA.SCV1", 23)])
        self.assertEqual(self.light_commands(dimmer), [])

        # On before setting the level: switched back on
        dimmer.set_current_value(aioenet.ChannelTypeFunctionName.ON_OFF, True)
        dimmer.set_current_value(aioenet.ChannelTypeFunctionName.BRIGHTNESS, 55)
        await self.set_scene_level(member=light, brightness_pct=30)
        self.assertEqual(self.light_commands(dimmer), [55])
        self.assert_no_warnings()

    async def test_add_scene_member_at_a_level(self):
        entity_id, channel = self.other_dimmer()
        await self.change_member("add_scene_member", member=entity_id, brightness_pct=20)
        self.assertEqual(self.written(), [(f"{channel.uid}-PT_INDA.SCV1", 20)])
        self.assertEqual(self.scene_state().attributes["levels"][entity_id], 20)

        self.assert_no_warnings()

    async def test_add_scene_member_at_the_default_level_programs_nothing(self):
        entity_id, channel = self.other_dimmer()
        functions = aioenet.ChannelTypeFunctionName
        channel.set_current_value(functions.ON_OFF, True)
        channel.set_current_value(functions.BRIGHTNESS, 100)  # the default of the slot

        await self.change_member("add_scene_member", member=entity_id)
        self.assertEqual(self.requests("writeChannelToBeLinksToDevice"),
                         [{"deviceUID": channel.device.uid, "channelNumbers": [channel.channel["no"]]}])
        self.assertEqual(self.written(), [])
        self.assertEqual(self.scene_state().attributes["levels"][entity_id], 100)
        self.assert_no_warnings()

    async def test_remove_scene_member(self):
        light, switch, cover = self.scene_members()
        _, switch_channel, _ = self.hub.scene_channels()

        await self.change_member("remove_scene_member", member=switch)
        device_uid, number = switch_channel.device.uid, switch_channel.channel["no"]
        self.assertEqual(self.link_requests(), [
            ("removeActorChannelFromSceneAction",
             {"sceneActionUID": "scene-1", "deviceUID": device_uid, "channelNumber": number}),
            ("setClientRole", {"clientRole": "CR_IBN"}),
            ("writeChannelToBeLinksToDevice", {"deviceUID": device_uid, "channelNumbers": [number]}),
            ("setClientRole", {"clientRole": "CR_VISU"}),
        ])
        attributes = self.scene_state().attributes
        self.assertEqual(attributes["entity_id"], [light, cover])
        self.assertNotIn(switch, attributes["levels"])
        # Its scene value was the default already, nothing to program
        self.assertEqual(self.written(), [])
        self.assert_no_warnings()

    async def test_scene_member_changes_are_validated(self):
        light, _, cover = self.scene_members()
        other, _ = self.other_dimmer()
        for service, data in (
            ("add_scene_member", {"member": light}),  # already a member
            ("add_scene_member", {"member": other, "position": 50}),
            ("add_scene_member", {"member": cover.replace("cover.", "light.")}),
            ("remove_scene_member", {"member": other}),  # not a member
        ):
            with self.subTest(service=service, data=data), self.assertRaises(ServiceValidationError):
                await self.change_member(service, **data)
            self.assertEqual(self.link_requests(), [])

        # The scenes the server makes for locations can't be changed
        await self.set_scenes_option("all_with_location")
        kitchen = self.entity_id("scene", "Enet Smart Home Server Kitchen lights on")
        with self.assertRaises(ServiceValidationError):
            await self.hass.services.async_call(
                "enet", "add_scene_member", {"entity_id": kitchen, "member": other}, blocking=True
            )

    async def test_add_scene_member_rejected_by_the_server(self):
        entity_id, _ = self.other_dimmer()
        self.hub.link_result = "TOO_MANY_LINKS"
        with self.assertRaisesRegex(HomeAssistantError, "TOO_MANY_LINKS"):
            await self.change_member("add_scene_member", member=entity_id)
        self.assertEqual(self.requests("writeChannelToBeLinksToDevice"), [])
        self.assertNotIn(entity_id, self.scene_state().attributes["entity_id"])

    async def test_failed_write_raises_an_error(self):
        light, _, _ = self.scene_members()
        self.hub.write_success = False
        with self.assertRaisesRegex(HomeAssistantError, "error 7"):
            await self.set_scene_level(member=light, brightness_pct=10)
        self.assertEqual(self.scene_state().attributes["levels"][light], 60)

    async def test_button_events(self):
        """A press reaches the event entity, enet_event on the bus and the device trigger."""
        bus = []
        self.hass.bus.async_listen("enet_event", lambda event: bus.append(event.data))
        fired = []

        async def action(run_variables, context=None):
            fired.append(run_variables["trigger"]["event"].data)

        rockers = [
            (channel, function)
            for device in self.hub.devices
            for channel in device.channels
            for function in channel.output_functions.values()
            if function["function"] == "buttonRocker"
        ]
        self.assertTrue(rockers, "the fixture has no rocker")

        for sequence, (channel, function) in enumerate(rockers):
            device = self.device(channel.device.uid)
            number = channel.channel["no"]
            trigger = device_trigger.TRIGGER_SCHEMA(
                {
                    "platform": "device",
                    "domain": "enet",
                    "device_id": device.id,
                    "type": "short_release",
                    "subtype": str(number + 1),
                    "unique_id": channel.device.uid,
                }
            )
            detach = await device_trigger.async_attach_trigger(
                self.hass, trigger, action,
                {"trigger_data": {}, "variables": {}, "name": "test",
                 "home_assistant_start": False, "domain": "enet"},
            )
            bus.clear()
            fired.clear()

            # Short press on the lower button of the rocker
            await self.hub.handle_events({"events": [{
                "sequenceNumber": sequence,
                "event": "outputDeviceFunctionCalled",
                "eventData": {
                    "deviceUID": channel.device.uid,
                    "channelNumber": number,
                    "deviceFunctionUID": function["uid"],
                    "values": [
                        {"valueTypeID": "VT_ROCKER_STATE", "value": "DOWN_BUTTON"},
                        {"valueTypeID": "VT_ROCKER_SWITCH_TIME", "value": 30},
                    ],
                },
            }]})
            await self.hass.async_block_till_done()
            detach()

            ent_reg = er.async_get(self.hass)
            event_entity = ent_reg.async_get_entity_id(
                "event", "enet", f"{channel.uid}-button-{number + 1}"
            )
            state = self.hass.states.get(event_entity)
            self.assertEqual(state.attributes["event_type"], "short_release")
            self.assertEqual(
                [(e["subtype"], e["type"]) for e in bus], [(str(number + 1), "short_release")]
            )
            self.assertEqual(len(fired), 1)
        self.assert_no_warnings()

    async def test_device_triggers_match_event_entities(self):
        buttons = {}
        for entry in self.entities("event"):
            buttons.setdefault(entry.device_id, set()).add(
                entry.unique_id.rsplit("-button-", 1)[1]
            )
        self.assertTrue(buttons)
        for device_id, subtypes in buttons.items():
            triggers = await device_trigger.async_get_triggers(self.hass, device_id)
            self.assertEqual({t["subtype"] for t in triggers}, subtypes)

    async def test_diagnostics_redact_credentials(self):
        result = await diagnostics.async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertEqual(result["config_entry"]["data"]["password"], "**REDACTED**")
        self.assertEqual(result["config_entry"]["data"]["username"], "**REDACTED**")
        self.assertEqual(len(result["enet_data"]), len(json.loads(DEVICES.read_text())["data"]["enet_data"]))

    async def test_entry_without_unique_id_gets_the_url(self):
        self.assertEqual(self.entry.unique_id, "http://enet.invalid")

    async def test_user_flow_aborts_for_a_configured_server(self):
        result = await self.hass.config_entries.flow.async_init(
            "enet", context={"source": config_entries.SOURCE_USER}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"url": " http://enet.invalid/ ", "username": "admin", "password": "secret"},
        )
        self.assertEqual((result["type"], result["reason"]), ("abort", "already_configured"))
        self.assert_no_warnings()

    async def test_unload_and_reload(self):
        entity_ids = {e.entity_id for e in self.entities()}
        self.assertTrue(await self.hass.config_entries.async_reload(self.entry.entry_id))
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.state, config_entries.ConfigEntryState.LOADED)
        self.assertEqual({e.entity_id for e in self.entities()}, entity_ids)

        self.assertTrue(await self.hass.config_entries.async_unload(self.entry.entry_id))
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.state, config_entries.ConfigEntryState.NOT_LOADED)
        tasks = [t for t in asyncio.all_tasks() if "enet" in t.get_name()]
        self.assertEqual(tasks, [])
        self.assert_no_warnings()


if __name__ == "__main__":
    unittest.main(verbosity=2)
