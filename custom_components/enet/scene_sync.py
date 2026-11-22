"""Edit the Enet scenes in Home Assistant's scene editor.

With the option on, the scenes of the Enet server are Home Assistant scenes
in scenes.yaml, and every Home Assistant scene with Enet lights, switches or
covers has a scene on the Enet server with them as members: the two are
"linked". The scene editor and the Enet app (and the scene buttons) then use
the same scenes.

The links are stored with the content both sides last agreed on, which tells
which side changed:

- Home Assistant -> server: when the scenes are reloaded (saving in the scene
  editor), members are added or removed, the levels set (learned when the
  lights are at the levels, as after live edit, otherwise programmed) and the
  name changed. A deleted Home Assistant scene deletes its Enet scene.
- Server -> Home Assistant: on the server's scene events (the Enet app, the
  actions of this integration) and at startup, the Enet part of the Home
  Assistant scene is updated. A scene deleted on the server deletes the
  Home Assistant scene. When both changed, Home Assistant wins.

Running a linked scene (scene.turn_on) sends the Enet scene: one telegram
for its Enet members, which the Enet server sends. The commands Home
Assistant then sends to those lights and switches are not sent when they are
the levels of the scene; a few seconds later, members that are not at their
level are switched individually (the scene telegram can be lost by radio).
With a transition, Home Assistant switches them itself.

The Enet part of a Home Assistant scene is its Enet lights, switches and
covers; other entities stay in the Home Assistant scene only. Scenes of the
server that can't be a Home Assistant scene stay scenes of the integration:
the ones it makes for locations, scenes without members or with other
actions, and scenes with a member that is not an entity.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

import aiohttp

from homeassistant.const import (
    CONF_ID,
    CONF_NAME,
    EVENT_CALL_SERVICE,
    STATE_CLOSED,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.yaml import dump, load_yaml

from .aioenet import (
    ChannelApplicationMode,
    ChannelTypeFunctionName,
    EnetScene,
    EnetServerError,
    SceneChangedEvent,
    app_scene_name,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

EVENT_SCENE_RELOADED = "scene_reloaded"  # homeassistant/components/homeassistant/scene.py
SCENES_FILE = "scenes.yaml"
# The id of a Home Assistant scene made from a scene of the server
HA_ID_PREFIX = "enet_"
# Seconds to wait for more events of a scene before syncing it
SYNC_DELAY = 2
# Seconds after running a scene in which commands to its members at their
# level are not sent, and after which members not at their level are switched
LOCK_SECONDS = 4
CORRECT_DELAY = 5
MEMBER_DOMAINS = ("light", "switch", "cover")
CONF_ENTITIES = "entities"
ATTR_BRIGHTNESS = "brightness"
ATTR_CURRENT_POSITION = "current_position"
ATTR_CURRENT_TILT_POSITION = "current_tilt_position"


async def async_read_scenes(hass: HomeAssistant) -> list[dict]:
    """The scenes of scenes.yaml"""
    path = hass.config.path(SCENES_FILE)

    def read():
        try:
            return load_yaml(path) or []
        except FileNotFoundError:
            return []

    data = await hass.async_add_executor_job(read)
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


async def async_write_scenes(hass: HomeAssistant, config: list[dict]) -> None:
    """Write scenes.yaml and reload the scenes"""
    path = hass.config.path(SCENES_FILE)
    # Copied, so the same scene object twice never becomes a YAML alias
    await hass.async_add_executor_job(
        write_utf8_file_atomic, path, dump([copy.deepcopy(s) for s in config])
    )
    if hass.services.has_service("scene", "reload"):
        await hass.services.async_call("scene", "reload", blocking=True)


def links_store(hass: HomeAssistant, entry_id: str) -> Store:
    return Store(hass, 1, f"{DOMAIN}_scene_links_{entry_id}")


async def async_mirrored_scenes(hass: HomeAssistant, entry_id: str) -> list[str]:
    """The ids of the Home Assistant scenes made from scenes of the server that
    are still in scenes.yaml"""
    links = await links_store(hass, entry_id).async_load() or {}
    in_config = {str(s.get(CONF_ID)) for s in await async_read_scenes(hass)}
    return [ha_id for ha_id in links
            if ha_id.startswith(HA_ID_PREFIX) and ha_id in in_config]


async def async_delete_mirrored_scenes(hass: HomeAssistant, ha_ids: list[str]) -> None:
    """Delete these Home Assistant scenes (made from scenes of the server) from
    scenes.yaml, and their entity ids, so the integration's scene entities get
    them back. The scenes on the server are not changed."""
    wanted = set(ha_ids)
    config = await async_read_scenes(hass)
    kept = [s for s in config if str(s.get(CONF_ID)) not in wanted]
    if len(kept) != len(config):
        _LOGGER.info("Deleting %d scenes made from Enet scenes from the scene editor",
                     len(config) - len(kept))
        await async_write_scenes(hass, kept)
    ent_reg = er.async_get(hass)
    for ha_id in wanted:
        if entity_id := ent_reg.async_get_entity_id("scene", "homeassistant", ha_id):
            ent_reg.async_remove(entity_id)


def value_from_state(channel, state: Any) -> tuple[Any, Any]:
    """(level, slats) for the scene, from a state in a Home Assistant scene"""
    if not isinstance(state, dict):
        state = {"state": state}
    if channel.application_mode == ChannelApplicationMode.BLINDS:
        position = state.get(ATTR_CURRENT_POSITION)
        if position is None:
            position = 0 if state.get("state") == STATE_CLOSED else 100
        tilt = state.get(ATTR_CURRENT_TILT_POSITION)
        # The Enet server counts from open
        return 100 - int(position), None if tilt is None else 100 - int(tilt)
    on = state.get("state") == STATE_ON
    if channel.application_mode == ChannelApplicationMode.LIGHT_DIMMING:
        brightness = state.get(ATTR_BRIGHTNESS)
        if not on:
            return 0, None
        return (100 if brightness is None else max(1, round(int(brightness) * 100 / 255))), None
    return ("ON" if on else "OFF"), None


def state_from_value(channel, level: Any, slats: Any) -> dict[str, Any]:
    """The state in a Home Assistant scene for the level and slats of a member"""
    if channel.application_mode == ChannelApplicationMode.BLINDS:
        position = 100 - int(level or 0)
        state = {"state": STATE_OPEN if position > 0 else STATE_CLOSED,
                 ATTR_CURRENT_POSITION: position}
        if slats is not None:
            state[ATTR_CURRENT_TILT_POSITION] = 100 - int(slats)
        return state
    if channel.application_mode == ChannelApplicationMode.LIGHT_DIMMING:
        if not level:
            return {"state": STATE_OFF}
        return {"state": STATE_ON, ATTR_BRIGHTNESS: round(int(level) * 255 / 100)}
    on = str(level).upper() == "ON" if isinstance(level, str) else bool(level)
    return {"state": STATE_ON if on else STATE_OFF}


def same_content(a: dict, b: dict) -> bool:
    """True if two contents (entity id -> [level, slats]) are equal, except for rounding"""
    if set(a) != set(b):
        return False
    for entity_id, (level, slats) in a.items():
        other_level, other_slats = b[entity_id]
        if isinstance(level, str) or isinstance(other_level, str):
            if str(level).upper() != str(other_level).upper():
                return False
        elif level is None or other_level is None:
            if level != other_level:
                return False
        elif abs(level - other_level) > 1:
            return False
        if (slats is None) != (other_slats is None) or (
            slats is not None and abs(slats - other_slats) > 1
        ):
            return False
    return True


class SceneSync:
    """Keeps the scenes of the Enet server and Home Assistant's scenes in sync"""

    def __init__(self, hass: HomeAssistant, entry, hub) -> None:
        self.hass = hass
        self.entry = entry
        self.hub = hub
        self._store = links_store(hass, entry.entry_id)
        # Home Assistant scene id -> {"enet": scene uid, "name": name, "content": {...}}
        self.links: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._pending: set[str] = set()  # scene uids to sync after SYNC_DELAY
        self._pending_handle: asyncio.TimerHandle | None = None
        self._unsubs: list = []
        self.started = False
        # Channel uid -> the scene call: {"level", "until", "time", "suppressed"}
        self._calls: dict[str, dict[str, Any]] = {}
        self._commands: dict[str, float] = {}  # channel uid -> time of the last command sent

    @property
    def linked_scene_uids(self) -> set[str]:
        return {link["enet"] for link in self.links.values()}

    async def async_load(self) -> None:
        self.links = await self._store.async_load() or {}

    @callback
    def async_start(self) -> None:
        """Sync when Home Assistant has started, and on changes on either side"""
        self._unsubs.append(self.hass.bus.async_listen(EVENT_SCENE_RELOADED, self._reloaded))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._service_called))
        self._unsubs.append(self.hub.subscribe(self._server_event))
        self._unsubs.append(async_at_started(self.hass, self._started))

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._pending_handle:
            self._pending_handle.cancel()

    @callback
    def _started(self, _hass: HomeAssistant) -> None:
        self.started = True
        self._schedule()

    @callback
    def _reloaded(self, _event: Event) -> None:
        if self.started:
            self._schedule()

    @callback
    def _server_event(self, event) -> None:
        if not isinstance(event, SceneChangedEvent) or not self.started:
            return
        self._pending.add(event.scene_uid)
        if self._pending_handle:
            self._pending_handle.cancel()
        self._pending_handle = self.hass.loop.call_later(SYNC_DELAY, self._sync_pending)

    @callback
    def _sync_pending(self) -> None:
        self._pending_handle = None
        uids, self._pending = self._pending, set()
        self._schedule(uids)

    @callback
    def _schedule(self, scene_uids: set[str] | None = None) -> None:
        self.entry.async_create_background_task(
            self.hass, self._safe_sync(scene_uids), "enet scene sync"
        )

    async def _safe_sync(self, scene_uids) -> None:
        try:
            await self.async_sync(scene_uids)
        except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
            _LOGGER.warning("Syncing the scenes with the Enet server failed: %s", e)
        except Exception:  # noqa: BLE001 - never take the integration down with it
            _LOGGER.exception("Syncing the scenes with the Enet server failed")

    # --- Helpers ------------------------------------------------------------

    def _entities(self) -> dict[str, str]:
        """Channel uid -> entity id of the Enet lights, switches and covers"""
        ent_reg = er.async_get(self.hass)
        return {
            e.unique_id: e.entity_id
            for e in er.async_entries_for_config_entry(ent_reg, self.entry.entry_id)
            if e.domain in MEMBER_DOMAINS
        }

    def _channels(self) -> dict[str, Any]:
        """Entity id -> channel of the Enet lights, switches and covers"""
        channels = {c.uid: c for d in self.hub.devices for c in d.channels}
        return {
            entity_id: channels[uid]
            for uid, entity_id in self._entities().items()
            if uid in channels
        }

    def _enet_content(self, scene: EnetScene, entities: dict[str, str]):
        """Entity id -> [level, slats] of the members, None if the scene can't be
        a Home Assistant scene"""
        if not scene.members or scene.additional_actions:
            return None
        content = {}
        for member in scene.members:
            entity_id = entities.get(member.uid)
            if entity_id is None or member.level is None:
                return None
            slats = member.slats.value if member.slats is not None else None
            content[entity_id] = [member.level.value, slats]
        return content

    @staticmethod
    def _ha_content(config: dict, channels: dict[str, Any]) -> dict[str, list]:
        """Entity id -> [level, slats] of the Enet entities of a Home Assistant scene"""
        return {
            entity_id: list(value_from_state(channels[entity_id], state))
            for entity_id, state in (config.get(CONF_ENTITIES) or {}).items()
            if entity_id in channels
        }

    async def _read_config(self) -> list[dict]:
        return await async_read_scenes(self.hass)

    @staticmethod
    def _remove_duplicates(config: list[dict]) -> bool:
        """Remove later scenes with the id of an earlier one (Home Assistant
        ignores them, and the scene editor would only delete the first).
        Returns True if any were removed."""
        seen: set[str] = set()
        removed = False
        for scene in list(config):
            scene_id = scene.get(CONF_ID)
            if scene_id is None:
                continue
            if str(scene_id) in seen:
                _LOGGER.info("Removing the duplicate of the scene %s", scene.get(CONF_NAME))
                # Not config.remove(): that would remove the first, equal one
                del config[next(i for i in range(len(config) - 1, -1, -1)
                                if config[i] is scene)]
                removed = True
            seen.add(str(scene_id))
        return removed

    async def _write_config(self, config: list[dict]) -> None:
        await async_write_scenes(self.hass, config)

    # --- Sync ---------------------------------------------------------------

    async def async_sync(self, scene_uids: set[str] | None = None) -> None:
        """Sync the scenes, or only the scenes of the server with these uids
        (after their events)"""
        if not self.hass.services.has_service("scene", "reload"):
            _LOGGER.warning(
                "The Enet scenes can't be edited in the scene editor: scenes.yaml is "
                "not loaded (add 'scene: !include scenes.yaml' to configuration.yaml)"
            )
            return
        async with self._lock:
            config = await self._read_config()
            changed_config = self._remove_duplicates(config)
            by_id = {str(s[CONF_ID]): s for s in config if s.get(CONF_ID) is not None}
            entities = self._entities()
            channels = self._channels()
            server = {s.uid: s for s in await self.hub.get_scene_actions(only_libenet=False)}
            for scene in server.values():
                if scene_uids is None or scene.uid in scene_uids:
                    await scene.load_values()

            # A scene mirrored before (by an earlier config entry, or before the
            # links were lost) is linked again instead of being mirrored twice
            linked_enet = self.linked_scene_uids
            for uid in server:
                ha_id = f"{HA_ID_PREFIX}{uid}"
                ha_scene = by_id.get(ha_id)
                if ha_scene is None or ha_id in self.links or uid in linked_enet:
                    continue
                _LOGGER.info("Linking the scene %s to the Enet scene again",
                             ha_scene.get(CONF_NAME))
                # Its content counts as the last synced one: the server wins
                self.links[ha_id] = {
                    "enet": uid,
                    "name": str(ha_scene.get(CONF_NAME) or ""),
                    "content": self._ha_content(ha_scene, channels),
                }
                await self._save()
                linked_enet.add(uid)

            # A Home Assistant scene with an Enet scene made for it before, by name
            unlinked_server = {}
            for uid, scene in server.items():
                if uid not in linked_enet:
                    unlinked_server.setdefault(scene.display_name, []).append(uid)
            for ha_id, ha_scene in by_id.items():
                if ha_id in self.links or ha_id.startswith(HA_ID_PREFIX):
                    continue
                content = self._ha_content(ha_scene, channels)
                uids = unlinked_server.get(str(ha_scene.get(CONF_NAME) or ""), [])
                if not content or len(uids) != 1:
                    continue
                _LOGGER.info("Linking the scene %s to the Enet scene with its name again",
                             ha_scene.get(CONF_NAME))
                self.links[ha_id] = {
                    "enet": uids[0], "name": str(ha_scene[CONF_NAME]), "content": content
                }
                linked_enet.add(uids[0])
                await self._save()

            linked = {link["enet"]: ha_id for ha_id, link in self.links.items()}

            # Linked scenes
            for ha_id, link in list(self.links.items()):
                uid = link["enet"]
                if scene_uids is not None and uid not in scene_uids:
                    continue
                ha_scene, enet_scene = by_id.get(ha_id), server.get(uid)
                if ha_scene is None and enet_scene is None:
                    del self.links[ha_id]
                elif ha_scene is None:
                    _LOGGER.info("Scene %s was deleted in Home Assistant, deleting it on "
                                 "the Enet server", enet_scene.display_name)
                    await enet_scene.delete()
                    del self.links[ha_id]
                elif enet_scene is None:
                    _LOGGER.info("Scene %s was deleted on the Enet server, deleting it in "
                                 "Home Assistant", ha_scene.get(CONF_NAME))
                    config.remove(ha_scene)
                    changed_config = True
                    del self.links[ha_id]
                else:
                    changed_config |= await self._sync_linked(
                        ha_id, link, ha_scene, enet_scene, entities, channels
                    )
                await self._save()

            # Scenes of the server that are not linked yet
            for uid, scene in server.items():
                if uid in linked or (scene_uids is not None and uid not in scene_uids):
                    continue
                content = self._enet_content(scene, entities)
                if content is None:
                    continue
                ha_id = f"{HA_ID_PREFIX}{uid}"
                self._take_over_entity_id(uid, ha_id)
                config.append({
                    CONF_ID: ha_id,
                    CONF_NAME: scene.display_name,
                    CONF_ENTITIES: {
                        entity_id: state_from_value(channels[entity_id], level, slats)
                        for entity_id, (level, slats) in content.items()
                    },
                })
                changed_config = True
                self.links[ha_id] = {"enet": uid, "name": scene.display_name, "content": content}
                await self._save()
                _LOGGER.info("Scene %s of the Enet server is now a Home Assistant scene",
                             scene.display_name)

            # Home Assistant scenes with Enet entities that are not linked yet
            if scene_uids is None:
                for ha_id, ha_scene in by_id.items():
                    if ha_id in self.links:
                        continue
                    content = self._ha_content(ha_scene, channels)
                    if not content:
                        continue
                    await self._create_enet_scene(ha_id, ha_scene, content, channels)

            if changed_config:
                await self._write_config(config)

    async def _sync_linked(self, ha_id, link, ha_scene, enet_scene, entities, channels) -> bool:
        """Sync a linked scene. Returns True if the Home Assistant scene changed."""
        ha_content = self._ha_content(ha_scene, channels)
        enet_content = self._enet_content(enet_scene, entities)
        if enet_content is None:
            enet_content = {}
        ha_name = str(ha_scene.get(CONF_NAME) or "")
        changed_config = False

        if not ha_content:
            # No Enet entities anymore: the Enet scene is not needed
            _LOGGER.info("Scene %s has no Enet entities anymore, deleting it on the Enet "
                         "server", ha_name)
            await enet_scene.delete()
            del self.links[ha_id]
            return False

        if not same_content(ha_content, enet_content):
            if same_content(ha_content, link["content"]):
                # Changed on the server
                self._set_enet_part(ha_scene, enet_content, channels)
                changed_config = True
            else:
                # Changed in Home Assistant (or on both sides: Home Assistant wins)
                await self._update_enet_scene(enet_scene, ha_content, channels)
        link["content"] = ha_content if not changed_config else enet_content

        if ha_name != enet_scene.display_name:
            if ha_name == link.get("name"):
                ha_scene[CONF_NAME] = enet_scene.display_name
                changed_config = True
            else:
                await enet_scene.rename(ha_name)
        link["name"] = ha_scene[CONF_NAME]
        return changed_config

    @staticmethod
    def _set_enet_part(ha_scene: dict, content: dict, channels: dict) -> None:
        """Replace the Enet entities of a Home Assistant scene"""
        entities = {
            entity_id: state
            for entity_id, state in (ha_scene.get(CONF_ENTITIES) or {}).items()
            if entity_id not in channels
        }
        for entity_id, (level, slats) in content.items():
            entities[entity_id] = state_from_value(channels[entity_id], level, slats)
        ha_scene[CONF_ENTITIES] = entities

    async def _update_enet_scene(self, scene: EnetScene, content: dict, channels: dict) -> None:
        """Make the members and levels of an Enet scene the content"""
        wanted_channels = {channels[entity_id].uid: entity_id for entity_id in content}
        for member in [m for m in scene.members if m.uid not in wanted_channels]:
            await scene.remove_member(member)
        new = [channels[e] for e in content if scene.get_member(channels[e].uid) is None]
        if new:
            async with scene.keep_light_states(new):
                for channel in new:
                    await scene.add_member(channel)
        await scene.set_values({
            channels[entity_id].uid: tuple(value) for entity_id, value in content.items()
        })

    async def _create_enet_scene(self, ha_id, ha_scene, content, channels) -> None:
        name = str(ha_scene.get(CONF_NAME) or ha_id)
        _LOGGER.info("Creating the Enet scene for the scene %s", name)
        full_name = app_scene_name(name)
        scene = EnetScene(self.hub, {"uid": await self.hub.create_scene_action(full_name),
                                     "name": full_name})
        scene.values_loaded = True
        self.links[ha_id] = {"enet": scene.uid, "name": name, "content": {}}
        await self._save()
        try:
            await self._update_enet_scene(scene, content, channels)
        finally:
            self.links[ha_id]["content"] = {
                e: v for e, v in content.items() if scene.get_member(channels[e].uid)
            }
            await self._save()

    def _take_over_entity_id(self, scene_uid: str, ha_id: str) -> None:
        """The Home Assistant scene gets the entity id of the integration's scene
        entity, which is removed with its Save button"""
        ent_reg = er.async_get(self.hass)
        old = ent_reg.async_get_entity_id("scene", DOMAIN, scene_uid)
        button = ent_reg.async_get_entity_id("button", DOMAIN, f"{scene_uid}-save")
        if button:
            ent_reg.async_remove(button)
        if old is None:
            return
        ent_reg.async_remove(old)
        ent_reg.async_get_or_create(
            "scene", "homeassistant", ha_id, suggested_object_id=old.split(".", 1)[1]
        )

    async def _save(self) -> None:
        await self._store.async_save(self.links)

    async def async_remove(self) -> None:
        await self._store.async_remove()

    # --- Running a linked scene ---------------------------------------------

    @callback
    def _service_called(self, event: Event) -> None:
        """scene.turn_on of a linked scene: send the Enet scene before Home
        Assistant switches the members one by one"""
        data = event.data
        if data.get("domain") != "scene" or data.get("service") != "turn_on":
            return
        service_data = data.get("service_data") or {}
        if "transition" in service_data or self._lock.locked():
            return  # a transition, or the Enet scenes are being changed
        entity_ids = service_data.get("entity_id") or []
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        channels = None
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            link = self.links.get(str(state.attributes.get("id"))) if state else None
            if link is None:
                continue
            channels = channels or self._channels()
            now = time.monotonic()
            members = []
            for member_entity, (level, _slats) in link["content"].items():
                channel = channels.get(member_entity)
                if channel is None or channel.application_mode == ChannelApplicationMode.BLINDS:
                    continue
                self._calls[channel.uid] = {"level": level, "until": now + LOCK_SECONDS,
                                            "time": now, "suppressed": False}
                members.append(channel)
            self.entry.async_create_background_task(
                self.hass, self._run_scene(link["enet"], members, now), "enet scene call"
            )

    def suppressed(self, channel_uid: str, on: bool, percent: int | None = None) -> bool:
        """True if a command to a member is done by the scene just called"""
        call = self._calls.get(channel_uid)
        if call is None or call["suppressed"] or time.monotonic() > call["until"]:
            return False
        level = call["level"]
        level_on = level.upper() == "ON" if isinstance(level, str) else bool(level)
        if on != level_on:
            return False
        if on and percent is not None and not isinstance(level, str) and abs(percent - level) > 1:
            return False
        call["suppressed"] = True
        return True

    def note_command(self, channel_uid: str) -> None:
        self._commands[channel_uid] = time.monotonic()

    async def _run_scene(self, scene_uid: str, members: list, called: float) -> None:
        try:
            await self.hub.activate_scene(scene_uid)
        except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
            _LOGGER.warning("Running the Enet scene failed (%s), switching its lights "
                            "individually", e)
        await asyncio.sleep(CORRECT_DELAY)
        for channel in members:
            call = self._calls.get(channel.uid)
            if call is None or call["time"] != called:
                continue  # a newer scene
            del self._calls[channel.uid]
            if not call["suppressed"] or self._commands.get(channel.uid, 0) > called:
                continue  # Home Assistant switched it, not the scene
            await self._correct(channel, call["level"])

    async def _correct(self, channel, level) -> None:
        """Switch a member that is not at its level in the scene"""
        on = channel.get_current_value(ChannelTypeFunctionName.ON_OFF)
        want_on = level.upper() == "ON" if isinstance(level, str) else bool(level)
        if channel.application_mode == ChannelApplicationMode.LIGHT_DIMMING and want_on:
            brightness = channel.get_current_value(ChannelTypeFunctionName.BRIGHTNESS)
            if on and brightness is not None and abs(brightness - level) <= 1:
                return
            _LOGGER.info("%s did not get the scene, setting it to %s %%", channel.name, level)
            await channel.set_value(ChannelTypeFunctionName.BRIGHTNESS, level)
        elif bool(on) != want_on:
            _LOGGER.info("%s did not get the scene, switching it %s", channel.name,
                         "on" if want_on else "off")
            await (channel.turn_on() if want_on else channel.turn_off())
