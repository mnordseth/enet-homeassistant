"""Buttons to save the current state of the lights in an Enet scene"""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo

from .aioenet import EnetScene
from .scene import (
    async_remove_stale_entities,
    async_store_current_values,
    scene_name,
    server_device_info,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add a save button for every Enet scene"""
    hub = entry.runtime_data
    buttons = [EnetSceneSaveButton(scene) for scene in hub.scenes]
    async_remove_stale_entities(
        hass, entry, "button", {button.unique_id for button in buttons}
    )
    async_add_entities(buttons)


class EnetSceneSaveButton(ButtonEntity):
    """Store the current state of the scene members in the scene"""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "save_scene"

    def __init__(self, scene: EnetScene):
        self.scene = scene
        self._attr_unique_id = f"{scene.uid}-save"
        self._attr_translation_placeholders = {"scene": scene_name(scene)}

    @property
    def device_info(self) -> DeviceInfo:
        return server_device_info()

    async def async_press(self) -> None:
        await async_store_current_values(self.scene)
