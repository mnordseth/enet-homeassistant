""" Handle diagnostics for enet smart home."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    hub = config_entry.runtime_data
    diagnostics = {"config_entry": config_entry.as_dict(),
                   "enet_data":hub._raw_json}

    return diagnostics
