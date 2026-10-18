# /// script
# requires-python = ">=3.14"
# dependencies = ["aiohttp"]
# ///
"""Check that unloading a config entry stops everything setup started.

Home Assistant is replaced by minimal stubs. The stubbed config entry follows
Home Assistant's unload order: async_unload_entry() runs first, background
tasks of the entry are cancelled afterwards. Requests to the Enet server are
answered by a fake that counts them per client.
"""

import asyncio
import enum
import importlib.util
import logging
from pathlib import Path
import sys
import types
import unittest

import aiohttp


def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


class Platform(enum.StrEnum):
    LIGHT = "light"
    SCENE = "scene"
    COVER = "cover"
    SENSOR = "sensor"
    SWITCH = "switch"
    BINARY_SENSOR = "binary_sensor"


class ConfigEntryNotReady(Exception):
    pass


class DataUpdateCoordinator:
    def __init__(self, hass, logger, *, name, update_interval=None):
        self.hass = hass
        self.logger = logger
        self.name = name

    async def async_refresh(self):
        # Like Home Assistant: exceptions are logged, cancellation propagates.
        try:
            self.data = await self._async_update_data()
        except Exception:
            self.logger.exception("Unexpected error fetching %s data", self.name)

    def async_update_listeners(self):
        pass


class DeviceRegistry:
    def async_get_or_create(self, **kwargs):
        return types.SimpleNamespace(id="device-id")


_stub("homeassistant")
_stub("homeassistant.helpers")
_stub("homeassistant.config_entries", ConfigEntry=object,
      ConfigEntryNotReady=ConfigEntryNotReady)
_stub("homeassistant.const", Platform=Platform)
_stub("homeassistant.core", HomeAssistant=object, callback=lambda func: func)
_stub("homeassistant.helpers.entity", DeviceInfo=dict)
_stub("homeassistant.helpers.device_registry", DeviceEntry=object,
      async_get=lambda hass: DeviceRegistry(),
      async_get_device_id_by_identifier=lambda *args: None)
_stub("homeassistant.helpers.update_coordinator",
      DataUpdateCoordinator=DataUpdateCoordinator)

source = Path(__file__).resolve().parents[1] / "custom_components" / "enet"
spec = importlib.util.spec_from_file_location(
    "enet_under_test", source / "__init__.py", submodule_search_locations=[str(source)]
)
enet = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = enet
spec.loader.exec_module(enet)
aioenet = sys.modules["enet_under_test.aioenet"]


class FakeConfigEntry:
    """Config entry with Home Assistant's background task semantics."""

    def __init__(self, url):
        self.entry_id = "entry"
        self.data = {"url": url, "username": "user", "password": "secret"}
        self._background_tasks = set()

    def async_create_background_task(self, hass, target, name, eager_start=True):
        task = asyncio.create_task(target, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def cancel_background_tasks(self):
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class FakeHass:
    def __init__(self):
        self.data = {}
        self.loop = asyncio.get_running_loop()
        self.config_entries = types.SimpleNamespace(
            async_forward_entry_setups=self._ok, async_unload_platforms=self._ok
        )

    async def _ok(self, *args):
        return True


class EntryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = {}  # client -> list of JSON-RPC methods it sent
        self.clients = []
        self.login_error = None
        test = self

        async def fake_request(client, url, method, params, *args, **kwargs):
            test.calls[client].append(method)
            if method == "userLogin" and test.login_error:
                raise test.login_error
            if method == "requestEvents":
                await asyncio.sleep(0.01)  # the real server blocks up to 30 s
                raise aiohttp.ServerTimeoutError
            return {}

        async def fake_get_devices(client, device_uids=None):
            return []

        original_init = aioenet.EnetClient.__init__

        def init(client, *args, **kwargs):
            original_init(client, *args, **kwargs)
            test.calls[client] = []
            test.clients.append(client)

        self._patches = {
            "__init__": init,
            "_do_request": fake_request,
            "get_devices": fake_get_devices,
        }
        self._originals = {
            name: aioenet.EnetClient.__dict__[name] for name in self._patches
        }
        for name, func in self._patches.items():
            setattr(aioenet.EnetClient, name, func)

        self.hass = FakeHass()

    async def asyncTearDown(self):
        for name, func in self._originals.items():
            setattr(aioenet.EnetClient, name, func)
        for task in asyncio.all_tasks() - {asyncio.current_task()}:
            task.cancel()

    async def setup(self, url="http://enet.invalid"):
        entry = FakeConfigEntry(url)
        self.assertTrue(await enet.async_setup_entry(self.hass, entry))
        return entry

    async def unload(self, entry):
        self.assertTrue(await enet.async_unload_entry(self.hass, entry))
        await entry.cancel_background_tasks()

    def count(self, client, method):
        return self.calls[client].count(method)

    async def assert_silent(self, client):
        """Assert that the client sends no more requests."""
        before = len(self.calls[client])
        await asyncio.sleep(0.1)
        self.assertEqual(self.calls[client][before:], [])

    async def test_unload_stops_event_loop(self):
        entry = await self.setup()
        await asyncio.sleep(0.05)
        client = self.clients[0]
        self.assertGreater(self.count(client, "requestEvents"), 1)

        await self.unload(entry)
        await self.assert_silent(client)

    async def test_unload_stops_keepalive_ping_on_https(self):
        entry = await self.setup("https://enet.invalid")
        await asyncio.sleep(0.05)
        client = self.clients[0]
        self.assertEqual(self.count(client, "ping"), 1)

        await self.unload(entry)
        ping_task = [t for t in asyncio.all_tasks() if "ping_forever" in repr(t)]
        self.assertEqual(ping_task, [])

    async def test_reload_keeps_one_event_loop(self):
        for _ in range(3):
            await self.unload(await self.setup())
        await self.setup()
        await asyncio.sleep(0.05)

        polling = [c for c in self.clients if self.count(c, "requestEvents")]
        before = {c: self.count(c, "requestEvents") for c in polling}
        await asyncio.sleep(0.1)
        still_polling = [
            c for c in polling if self.count(c, "requestEvents") > before[c]
        ]
        self.assertEqual(still_polling, [self.clients[-1]])

    async def test_unload_logs_out_and_closes_session(self):
        entry = await self.setup()
        client = self.clients[0]
        await self.unload(entry)

        self.assertEqual(self.calls[client][-1], "userLogout")
        self.assertNotIn("userLogin", self.calls[client][1:])
        self.assertTrue(client._session.closed)

    async def test_failed_login_starts_nothing_and_closes_session(self):
        self.login_error = aiohttp.ClientError("connection refused")
        entry = FakeConfigEntry("https://enet.invalid")
        with self.assertRaises(ConfigEntryNotReady):
            await enet.async_setup_entry(self.hass, entry)
        client = self.clients[0]

        await self.assert_silent(client)
        self.assertEqual(self.calls[client], ["userLogin"])
        self.assertTrue(client._session.closed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main(verbosity=2)
