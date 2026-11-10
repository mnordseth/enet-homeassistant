"""Support for Enet scenes"""

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components.scene import Scene as SceneEntity
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo

from .aioenet import EnetScene, EnetServerError, SceneValueKind
from .const import DOMAIN, NAME_ENET_CONTROLLER, NAME_ENET_SERVER

_LOGGER = logging.getLogger(__name__)

ATTR_LEVELS = "levels"
ATTR_MEMBER = "member"
ATTR_BRIGHTNESS_PCT = "brightness_pct"
ATTR_STATE = "state"
ATTR_POSITION = "position"
ATTR_TILT_POSITION = "tilt_position"

SERVICE_SAVE_SCENE = "save_scene"
SERVICE_SET_SCENE_LEVEL = "set_scene_level"

PERCENT = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))
SET_SCENE_LEVEL_SCHEMA = {
    vol.Required(ATTR_MEMBER): cv.entity_id,
    vol.Optional(ATTR_BRIGHTNESS_PCT): PERCENT,
    vol.Optional(ATTR_STATE): vol.In([STATE_ON, STATE_OFF]),
    vol.Optional(ATTR_POSITION): PERCENT,
    vol.Optional(ATTR_TILT_POSITION): PERCENT,
}

MEMBER_DOMAINS = ("light", "switch", "cover")


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet scenes"""
    hub = entry.runtime_data
    async_add_entities([EnetSceneEntity(scene) for scene in hub.scenes])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_SAVE_SCENE, None, "async_save_scene")
    platform.async_register_entity_service(
        SERVICE_SET_SCENE_LEVEL, SET_SCENE_LEVEL_SCHEMA, "async_set_scene_level"
    )
    _LOGGER.debug("Finished async setup()")


def scene_name(scene: EnetScene) -> str:
    """Return the name of the scene without the [libenet...] prefix"""
    name = scene.name
    if name.startswith("[libenet") and "]" in name:
        name = name.split("]", 1)[-1].strip()
    return name


def server_device_info() -> DeviceInfo:
    """Scenes belong to the Enet server"""
    return DeviceInfo(
        identifiers={(DOMAIN, NAME_ENET_CONTROLLER)},
        name=NAME_ENET_SERVER,
    )


async def async_store_current_values(scene: EnetScene) -> None:
    """Store the current state of every member in the scene"""
    try:
        changed = await scene.store_current_values()
    except ValueError as e:
        raise ServiceValidationError(str(e)) from e
    except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
        raise HomeAssistantError(
            f"Failed to save scene {scene_name(scene)}: {e}"
        ) from e
    _LOGGER.debug("Saved scene %s, %s members changed", scene.name, changed)


class EnetSceneEntity(SceneEntity):
    """Representation of a Scene entity."""

    _attr_has_entity_name = True
    # The levels can be long, and are read from the Enet server anyway
    _unrecorded_attributes = frozenset({ATTR_LEVELS})

    def __init__(self, scene: EnetScene):
        self.scene = scene
        self.uid = scene.uid
        _LOGGER.debug("EnetSceneEntity.init()  done %s", self.name)

    @property
    def name(self):
        """Return the name of the scene."""
        return scene_name(self.scene)

    @property
    def unique_id(self):
        """Return the unique id of the scene."""
        return self.uid

    @property
    def device_info(self) -> DeviceInfo:
        """Return device (service) info."""
        return server_device_info()

    async def async_added_to_hass(self) -> None:
        """Update the attributes when the scene values change."""
        self.async_on_remove(self.scene.subscribe(self.async_write_ha_state))

    async def async_activate(self, **kwargs):
        """Activate Enet scene."""
        await self.scene.activate()

    def _member_entity_id(self, member) -> str | None:
        ent_reg = er.async_get(self.hass)
        for domain in MEMBER_DOMAINS:
            if entity_id := ent_reg.async_get_entity_id(domain, DOMAIN, member.uid):
                return entity_id
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The members of the scene and their levels.

        Levels are in Home Assistant terms: brightness in percent for dimmers
        (0 is off), on/off for switches, position and tilt position for covers
        (100 is open). A level is None until it has been read from the server.
        """
        entity_ids = []
        levels = {}
        for member in self.scene.members:
            entity_id = self._member_entity_id(member)
            if entity_id:
                entity_ids.append(entity_id)
            levels[entity_id or member.name] = _level_attribute(member)
        return {ATTR_ENTITY_ID: entity_ids, ATTR_LEVELS: levels}

    async def async_save_scene(self) -> None:
        """Store the current state of the members in the scene."""
        await async_store_current_values(self.scene)

    async def async_set_scene_level(self, **kwargs) -> None:
        """Change the level of one member of the scene."""
        member_entity_id = kwargs[ATTR_MEMBER]
        entry = er.async_get(self.hass).async_get(member_entity_id)
        member = None
        if entry is not None and entry.platform == DOMAIN:
            member = self.scene.get_member(entry.unique_id)
        if member is None:
            raise ServiceValidationError(
                f"{member_entity_id} is not part of the scene {self.name}"
            )

        try:
            if member.kind is None:
                await member.load_values()
            level, slats = _member_values(member, member_entity_id, kwargs)
            await self.scene.set_member_values(member, level, slats)
        except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
            raise HomeAssistantError(
                f"Failed to change {member_entity_id} in scene {self.name}: {e}"
            ) from e


def _level_attribute(member):
    """Return the scene level of a member in Home Assistant terms"""
    level = member.level.value if member.level else None
    match member.kind:
        case SceneValueKind.COVER:
            slats = member.slats.value if member.slats else None
            return {
                ATTR_POSITION: None if level is None else 100 - level,
                ATTR_TILT_POSITION: None if slats is None else 100 - slats,
            }
        case SceneValueKind.SWITCH:
            return STATE_ON if level == "ON" else STATE_OFF
        case SceneValueKind.LEVEL:
            return level
    return None


def _member_values(member, entity_id, data):
    """Return the (level, slats) Enet values for the service data"""
    brightness = data.get(ATTR_BRIGHTNESS_PCT)
    state = data.get(ATTR_STATE)
    position = data.get(ATTR_POSITION)
    tilt = data.get(ATTR_TILT_POSITION)

    if member.kind == SceneValueKind.COVER:
        if brightness is not None or state is not None:
            raise ServiceValidationError(
                f"{entity_id} is a cover, set its position or tilt_position"
            )
        if position is None and tilt is None:
            raise ServiceValidationError(f"Set the position or tilt_position of {entity_id}")
        if tilt is not None and member.slats is None:
            raise ServiceValidationError(f"{entity_id} has no tilt position in scenes")
        return (
            None if position is None else 100 - position,
            None if tilt is None else 100 - tilt,
        )

    if position is not None or tilt is not None:
        raise ServiceValidationError(f"{entity_id} is not a cover, set its state or brightness_pct")
    if brightness is None and state is None:
        raise ServiceValidationError(f"Set the state or brightness_pct of {entity_id}")
    if member.kind is None:
        raise HomeAssistantError(f"The scene values of {entity_id} could not be read")
    if brightness is not None:
        return member.encode(level=brightness), None
    return member.encode(on=state == STATE_ON), None
