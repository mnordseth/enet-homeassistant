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
        {"sceneActionUID": "scene-2", "sceneActionName": "Not for Home Assistant"},
    ]
}


SCENE_SLOT = 5


class OfflineEnetClient(aioenet.EnetClient):
    """Client reading the devices from the fixture and recording requests.

    The scene "Evening" has a dimmer, a switch and a blind with slats from the
    fixture as members. Scene parameters are stored, and a write is reported
    with a writeValuesToDeviceParameterCompleted event like the real server.
    """

    def __init__(self, url, user, passwd):
        super().__init__(url, user, passwd, noconnect=True, load_file=str(DEVICES))
        self.requests = []
        self.parameters = {}  # uid -> {"toBe": value dict, "current": value dict}
        self.write_success = True

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

    def scene_actions(self):
        dimmer, switch, blind = self.scene_channels()
        members = [
            (dimmer, [("PT_INDA.SCV1", "VT_SCALING_RANGE_0_100_DEF_100", 60)]),
            (switch, [("PT_INSA.SCV1", "VT_SCV_SWITCH_DEF_ON", "ON")]),
            (blind, [("PT_INBA.SCV1", "VT_SCALING_RANGE_0_100_DEF_0", 30),
                     ("PT_INBA.SCV2", "VT_SCALING_RANGE_0_100_DEF_0", 10)]),
        ]
        device_channels = []
        for channel, params in members:
            scene_parameters = []
            for type_id, value_type, value in params:
                uid = f"{channel.uid}-{type_id}"
                value = {"valueTypeID": value_type, "value": value}
                self.parameters.setdefault(uid, {"toBe": value, "current": value})
                scene_parameters.append(
                    {"deviceParameterUID": uid, "deviceParameterTypeID": f"{type_id}[{SCENE_SLOT}]"}
                )
            device_channels.append({
                "deviceUID": channel.device.uid,
                "channelNumber": channel.channel["no"],
                "sceneParameters": scene_parameters,
            })
        return {"sceneActions": [{"uid": "scene-1", "name": "[libenet] Evening",
                                  "deviceChannels": device_channels}]}

    async def request(self, url, method, params, *args, **kwargs):
        self.requests.append((method, params))
        uid = (params or {}).get("deviceParameterUID")
        match method:
            case "getSceneActionUIDs":
                return SCENES
            case "getSceneActions":
                assert params == {"sceneActionUIDs": ["scene-1"]}, params
                return self.scene_actions()
            case "getToBeValuesFromDeviceParameter":
                return {"toBeValues": [self.parameters[uid]["toBe"]]}
            case "getCurrentValuesFromDeviceParameter":
                return {"currentValues": [self.parameters[uid]["current"]]}
            case "setToBeValuesToDeviceParameter":
                self.parameters[uid]["toBe"] = params["toBeValues"][0]
            case "writeToBeValuesToDeviceParameter":
                if self.write_success:
                    self.parameters[uid]["current"] = self.parameters[uid]["toBe"]
                event = {"sequenceNumber": 0,
                         "event": "writeValuesToDeviceParameterCompleted",
                         "eventData": {"deviceParameterUID": uid,
                                       "success": self.write_success,
                                       "errorCode": 0 if self.write_success else 7,
                                       "errorData": ""}}
                asyncio.get_running_loop().call_soon(
                    asyncio.ensure_future, self.handle_events({"events": [event]})
                )
        return None


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

    async def test_only_libenet_scenes_are_added_and_activated(self):
        scenes = self.entities("scene")
        self.assertEqual([e.unique_id for e in scenes], ["scene-1"])
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

    async def test_save_scene_stores_the_current_state(self):
        light, switch, cover = self.scene_members()
        dimmer, switch_channel, blind = self.hub.scene_channels()
        functions = aioenet.ChannelTypeFunctionName
        dimmer.set_current_value(functions.ON_OFF, True)
        dimmer.set_current_value(functions.BRIGHTNESS, 35)
        switch_channel.current_values[
            switch_channel.get_channel_configuration_entry("outputDeviceFunctions", functions.ON_OFF)
        ] = {"value": False}
        blind.set_current_value(functions.COVER_POSITION, 30)  # as in the scene
        blind.set_current_value(functions.TILT_POSITION, 10)

        self.hub.requests.clear()
        button = self.entity_id("button", "Enet Smart Home Server Save Evening")
        await self.hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
        # Only the changed members are written
        self.assertEqual(self.written(), [(f"{dimmer.uid}-PT_INDA.SCV1", 35),
                                          (f"{switch_channel.uid}-PT_INSA.SCV1", "OFF")])
        self.assertEqual(self.scene_state().attributes["levels"][light], 35)

        # Nothing changed since, nothing is written
        self.hub.requests.clear()
        await self.hass.services.async_call(
            "enet", "save_scene", {"entity_id": self.scene_state().entity_id}, blocking=True
        )
        self.assertEqual(self.written(), [])
        self.assert_no_warnings()

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
