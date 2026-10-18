# /// script
# requires-python = ">=3.14"
# dependencies = ["aiohttp", "voluptuous"]
# ///
"""Check that zeroconf and SSDP discovery steps return a flow result.

Home Assistant is replaced by minimal stubs; the flow only needs to know
whether an entry already exists and how to abort.
"""

import asyncio
import importlib
from pathlib import Path
import sys
import types
import unittest


def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


class ConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self, entries=()):
        self._entries = list(entries)

    def _async_current_entries(self):
        return self._entries

    def async_abort(self, *, reason):
        return {"type": "abort", "reason": reason}


_stub("homeassistant")
_stub("homeassistant.components")
_stub("homeassistant.components.zeroconf")
_stub("homeassistant.config_entries", ConfigFlow=ConfigFlow)
_stub("homeassistant.core", HomeAssistant=object)
_stub("homeassistant.data_entry_flow", FlowResult=dict)
_stub("homeassistant.exceptions", HomeAssistantError=Exception)
sys.modules["homeassistant"].config_entries = sys.modules["homeassistant.config_entries"]
sys.modules["homeassistant.components"].zeroconf = sys.modules[
    "homeassistant.components.zeroconf"
]

source = Path(__file__).resolve().parents[1] / "custom_components" / "enet"
package = types.ModuleType("enet_under_test")
package.__path__ = [str(source.resolve())]
sys.modules[package.__name__] = package
config_flow = importlib.import_module("enet_under_test.config_flow")


class DiscoveryFlowTests(unittest.TestCase):
    def run_steps(self, entries):
        flow = config_flow.ConfigFlow(entries)
        return [
            asyncio.run(flow.async_step_zeroconf(object())),
            asyncio.run(flow.async_step_ssdp(object())),
        ]

    def test_discovery_aborts_when_already_configured(self):
        for result in self.run_steps(entries=["existing entry"]):
            self.assertEqual(result, {"type": "abort", "reason": "already_configured"})

    def test_discovery_aborts_when_not_configured(self):
        for result in self.run_steps(entries=[]):
            self.assertEqual(
                result, {"type": "abort", "reason": "discovery_not_supported"}
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
