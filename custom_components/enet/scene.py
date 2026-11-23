"""Support for Enet scenes"""

from collections import Counter
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components.scene import Scene as SceneEntity
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo

from .aioenet import (
    DEFAULT_SCENE_ICON,
    SCENE_ICONS,
    EnetScene,
    EnetServerError,
    SceneMember,
    SceneValueKind,
)
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
SERVICE_ADD_SCENE_MEMBER = "add_scene_member"
SERVICE_REMOVE_SCENE_MEMBER = "remove_scene_member"
SERVICE_CREATE_SCENE = "create_scene"
SERVICE_DELETE_SCENE = "delete_scene"
ATTR_NAME = "name"
ATTR_MEMBERS = "members"
ATTR_ICON = "icon"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

PERCENT = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))
SET_SCENE_LEVEL_SCHEMA = {
    vol.Required(ATTR_MEMBER): cv.entity_id,
    vol.Optional(ATTR_BRIGHTNESS_PCT): PERCENT,
    vol.Optional(ATTR_STATE): vol.In([STATE_ON, STATE_OFF]),
    vol.Optional(ATTR_POSITION): PERCENT,
    vol.Optional(ATTR_TILT_POSITION): PERCENT,
}
# The level is optional: without one, the member is added at its current state
ADD_SCENE_MEMBER_SCHEMA = SET_SCENE_LEVEL_SCHEMA
REMOVE_SCENE_MEMBER_SCHEMA = {vol.Required(ATTR_MEMBER): cv.entity_id}
LIGHT_LEVEL_FIELDS = (ATTR_BRIGHTNESS_PCT, ATTR_STATE)
COVER_LEVEL_FIELDS = (ATTR_POSITION, ATTR_TILT_POSITION)

CREATE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional(ATTR_MEMBERS, default=[]): cv.entity_ids,
        vol.Optional(ATTR_ICON, default=DEFAULT_SCENE_ICON): vol.In(SCENE_ICONS),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)

MEMBER_DOMAINS = ("light", "switch", "cover")
# Set in the entity registry options of a scene once it was put in the area
# of its room, so an area the user removes stays removed
OPTION_ROOM_AREA_SET = "room_area_set"


def channel_for_entity(hass: HomeAssistant, hub, entity_id: str):
    """The Enet actuator channel of a light, switch or cover entity, or None"""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.platform != DOMAIN or entry.domain not in MEMBER_DOMAINS:
        return None
    return next(
        (c for d in hub.devices for c in d.channels if c.uid == entry.unique_id), None
    )


def _entry_for_call(hass: HomeAssistant, call: ServiceCall):
    """The loaded config entry an action is for: the one given, or the only one"""
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        entries = [e for e in entries if e.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(f"No loaded Enet Smart Home entry {entry_id}")
    if len(entries) != 1:
        raise ServiceValidationError(
            "There is no loaded Enet Smart Home entry" if not entries
            else "There are several Enet servers, give the config_entry_id"
        )
    return entries[0]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Actions that are not for an existing scene"""

    async def create_scene(call: ServiceCall) -> None:
        entry = _entry_for_call(hass, call)
        hub = entry.runtime_data
        name = call.data[ATTR_NAME]
        channels = []
        for entity_id in call.data[ATTR_MEMBERS]:
            channel = channel_for_entity(hass, hub, entity_id)
            if channel is None:
                raise ServiceValidationError(
                    f"{entity_id} is not a light, switch or cover of the Enet server"
                )
            if channel not in channels:
                channels.append(channel)
        try:
            await EnetScene.create(hub, name, channels, call.data[ATTR_ICON])
        except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
            raise HomeAssistantError(f"Failed to create the scene {name}: {e}") from e
        # The scenes are read when the entry is set up
        hass.config_entries.async_schedule_reload(entry.entry_id)

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_SCENE, create_scene, schema=CREATE_SCENE_SCHEMA
    )


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet scenes"""
    hub = entry.runtime_data
    scenes = unlinked_scenes(hub)
    async_remove_stale_entities(hass, entry, "scene", {scene.uid for scene in scenes})
    room_areas = {}
    if any(scene.location_uid for scene in scenes):
        room_areas = await _async_room_areas(hass, entry.entry_id, hub)
    async_add_entities(
        [EnetSceneEntity(scene, room_areas.get(scene.location_uid)) for scene in scenes]
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_SAVE_SCENE, None, "async_save_scene")
    platform.async_register_entity_service(
        SERVICE_SET_SCENE_LEVEL, SET_SCENE_LEVEL_SCHEMA, "async_set_scene_level"
    )
    platform.async_register_entity_service(
        SERVICE_ADD_SCENE_MEMBER, ADD_SCENE_MEMBER_SCHEMA, "async_add_scene_member"
    )
    platform.async_register_entity_service(
        SERVICE_REMOVE_SCENE_MEMBER, REMOVE_SCENE_MEMBER_SCHEMA, "async_remove_scene_member"
    )
    platform.async_register_entity_service(SERVICE_DELETE_SCENE, None, "async_delete_scene")
    _LOGGER.debug("Finished async setup()")


async def _async_room_areas(hass: HomeAssistant, entry_id: str, hub) -> dict[str, str]:
    """Location uid of each room -> the area most of its devices are in"""
    try:
        rooms = await hub.get_room_devices()
    except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
        _LOGGER.warning("Failed to read the rooms of the Enet server: %s", e)
        return {}
    dev_reg = dr.async_get(hass)

    def device(uid):
        if hasattr(dev_reg, "async_get_device_by_identifier"):  # Home Assistant 2026.11
            return dev_reg.async_get_device_by_identifier((DOMAIN, uid), entry_id)
        return dev_reg.async_get_device(identifiers={(DOMAIN, uid)})

    areas = {}
    for location_uid, device_uids in rooms.items():
        counts = Counter(
            found.area_id
            for uid in device_uids
            if (found := device(uid)) and found.area_id
        ).most_common(2)
        if counts and (len(counts) == 1 or counts[0][1] > counts[1][1]):
            areas[location_uid] = counts[0][0]
    return areas


def unlinked_scenes(hub) -> list[EnetScene]:
    """The scenes that are entities of the integration: not the ones that are
    Home Assistant scenes (scene_sync.py)"""
    sync = getattr(hub, "scene_sync", None)
    linked = sync.linked_scene_uids if sync is not None else set()
    return [scene for scene in hub.scenes if scene.uid not in linked]


def scene_name(scene: EnetScene) -> str:
    """Return the name of the scene shown in Home Assistant"""
    return scene.display_name


@callback
def async_remove_stale_entities(hass, entry, domain, unique_ids) -> None:
    """Remove the entities of scenes that are no longer added, ie after the
    scenes option was changed. Only when the scenes could be read."""
    if not entry.runtime_data.scenes_read:
        return
    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity.domain == domain and entity.unique_id not in unique_ids:
            _LOGGER.debug("Removing %s, its scene is no longer added", entity.entity_id)
            ent_reg.async_remove(entity.entity_id)


def server_device_info() -> DeviceInfo:
    """Scenes belong to the Enet server"""
    return DeviceInfo(
        identifiers={(DOMAIN, NAME_ENET_CONTROLLER)},
        name=NAME_ENET_SERVER,
    )


def check_changeable(scene: EnetScene) -> None:
    """The scenes the server makes for locations can't be changed"""
    if scene.automatic:
        raise ServiceValidationError(
            f"{scene_name(scene)} is made by the Enet server for a location and can't be changed"
        )


async def async_store_current_values(scene: EnetScene) -> None:
    """Store the current state of every member in the scene"""
    check_changeable(scene)
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

    def __init__(self, scene: EnetScene, room_area: str | None = None):
        self.scene = scene
        self._room_area = room_area  # the area of the room of a room scene
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
        self._async_set_room_area()

    @callback
    def _async_set_room_area(self) -> None:
        """Put the scene of a room in the area of the room's devices, once, and
        only when it has no area"""
        if not self._room_area:
            return
        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(self.entity_id)
        if entry is None or entry.options.get(DOMAIN, {}).get(OPTION_ROOM_AREA_SET):
            return
        if entry.area_id is None:
            ent_reg.async_update_entity(self.entity_id, area_id=self._room_area)
        ent_reg.async_update_entity_options(
            self.entity_id, DOMAIN, {**entry.options.get(DOMAIN, {}), OPTION_ROOM_AREA_SET: True}
        )

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
        check_changeable(self.scene)
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

    def _channel(self, entity_id):
        """The Enet actuator channel of a light, switch or cover entity, or None"""
        return channel_for_entity(self.hass, self.scene.client, entity_id)

    async def async_delete_scene(self) -> None:
        """Delete the scene on the Enet server."""
        if self.scene.automatic:
            raise ServiceValidationError(
                f"{self.name} is made by the Enet server for a location and can't be deleted"
            )
        try:
            await self.scene.delete()
        except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
            raise HomeAssistantError(f"Failed to delete the scene {self.name}: {e}") from e
        # The scenes are read when the entry is set up: removes this entity
        self.hass.config_entries.async_schedule_reload(self.platform.config_entry.entry_id)

    async def async_add_scene_member(self, **kwargs) -> None:
        """Add a light, switch or cover to the scene, at the given level or at
        its current state."""
        check_changeable(self.scene)
        entity_id = kwargs[ATTR_MEMBER]
        channel = self._channel(entity_id)
        if channel is None:
            raise ServiceValidationError(
                f"{entity_id} is not a light, switch or cover of the Enet server"
            )
        if self.scene.get_member(channel.uid) is not None:
            raise ServiceValidationError(f"{entity_id} is already part of the scene {self.name}")
        is_cover = entity_id.split(".", 1)[0] == "cover"
        wrong_fields = LIGHT_LEVEL_FIELDS if is_cover else COVER_LEVEL_FIELDS
        if any(kwargs.get(field) is not None for field in wrong_fields):
            raise ServiceValidationError(
                f"{entity_id} is a cover, set its position or tilt_position"
                if is_cover
                else f"{entity_id} is not a cover, set its state or brightness_pct"
            )
        given = any(
            kwargs.get(field) is not None for field in LIGHT_LEVEL_FIELDS + COVER_LEVEL_FIELDS
        )

        # Before adding: programming the links can restart the actuator, and
        # some come back off
        state = SceneMember.channel_state(channel)
        try:
            # Programmed twice (links, then the level): switched back on once
            async with self.scene.keep_light_states([channel]):
                member = await self.scene.add_member(channel)
                if given:
                    level, slats = _member_values(member, entity_id, kwargs)
                else:
                    try:
                        level, slats = member.current_values(state)
                    except ValueError as e:
                        _LOGGER.warning(
                            "Added %s to the scene %s with its default level: %s",
                            entity_id, self.name, e,
                        )
                        return
                    # Not programmed when the default of the scene slot is the state
                    if member.level is not None and member.level.value == level:
                        level = None
                    if member.slats is not None and member.slats.value == slats:
                        slats = None
                await self.scene.set_member_values(member, level, slats)
        except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
            raise HomeAssistantError(
                f"Failed to add {entity_id} to the scene {self.name}: {e}"
            ) from e

    async def async_remove_scene_member(self, **kwargs) -> None:
        """Remove a light, switch or cover from the scene."""
        check_changeable(self.scene)
        entity_id = kwargs[ATTR_MEMBER]
        channel = self._channel(entity_id)
        member = self.scene.get_member(channel.uid) if channel is not None else None
        if member is None:
            raise ServiceValidationError(f"{entity_id} is not part of the scene {self.name}")
        try:
            await self.scene.remove_member(member)
        except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
            raise HomeAssistantError(
                f"Failed to remove {entity_id} from the scene {self.name}: {e}"
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
