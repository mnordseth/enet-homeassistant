"""Repairs: the scenes left behind when the integration is removed"""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .config_flow import leftover_scenes_schema
from .const import CONF_LEFTOVER_SCENES, LEFTOVER_DELETE


class LeftoverScenesFlow(RepairsFlow):
    """Keep or delete the Home Assistant scenes made from the scenes of the
    server of a removed config entry"""

    def __init__(self, ha_ids: list[str], title: str) -> None:
        self._ha_ids = ha_ids
        self._title = title

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            if user_input[CONF_LEFTOVER_SCENES] == LEFTOVER_DELETE:
                from .scene_sync import async_delete_mirrored_scenes  # noqa: PLC0415

                await async_delete_mirrored_scenes(self.hass, self._ha_ids)
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="init",
            data_schema=leftover_scenes_schema(),
            description_placeholders={"count": str(len(self._ha_ids)), "title": self._title},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    data = data or {}
    return LeftoverScenesFlow(list(data.get("ha_ids", [])), str(data.get("title", "")))
