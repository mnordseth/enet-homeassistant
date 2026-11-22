"""Scenes on the Enet server ("scene actions") and their members"""

import asyncio
import contextlib
import logging
import re

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import aiohttp

from ..enet_data.enums import ChannelApplicationMode, ChannelTypeFunctionName

from .errors import EnetServerError

log = logging.getLogger(__name__)

# Seconds between the scene telegram that makes the actuators learn their
# current state and reading the learned values back
LEARN_SETTLE_SECONDS = 2
# A learned value may differ this much from the state it was learned from
# (dimmers and blinds round between percent and the byte in the actuator)
LEARNED_VALUE_TOLERANCE = 1
# Programming links or parameters into an actuator restarts it, and lights
# and switches come back off. Seconds after programming before they are
# switched back on, so the off report of the restart has arrived.
RESTORE_DELAY_SECONDS = 2
RESTORED_MODES = (
    ChannelApplicationMode.LIGHT_DIMMING,
    ChannelApplicationMode.LIGHT_SWITCHING,
    ChannelApplicationMode.SWITCHING,
)


# The icons of scenes in the Enet app, in the order of its IconType. The app
# names scenes "[libenet:<icon number>]<name>".
SCENE_ICONS = (
    "undefined", "alarm_clock", "away", "bath", "bed", "book", "boy", "bread",
    "car", "chefs_hat", "croissant", "cup", "cupcake", "dinner", "fitness",
    "gamepad", "girl", "heart", "home", "ice", "keys", "lightning", "man", "moon",
    "movie_projector", "notebook", "notes", "palm_tree", "people", "pot", "rain",
    "silverware", "siren", "soccer", "sofa", "storm", "suitcase", "sun", "sunset",
    "teddy", "travel_case", "tv", "wall_clock", "wine", "woman", "blinds_down",
    "blinds_up", "lights_off", "lights_on", "energy_off", "energy_on",
)
DEFAULT_SCENE_ICON = "lights_on"


def app_scene_name(name, icon=DEFAULT_SCENE_ICON):
    """The name of a scene as the Enet app names it, with its icon"""
    number = icon if isinstance(icon, int) else SCENE_ICONS.index(icon)
    return f"[libenet:{number}]{name}"


def icon_number(name):
    """The icon number in the name of a scene made in the Enet app, or None"""
    match = re.match(r"^\[libenet:(\d+)\]", name)
    return int(match.group(1)) if match else None


def same_scene_value(a, b):
    """True if two scene values are equal, except for rounding"""
    if isinstance(a, str) or isinstance(b, str):
        return str(a).upper() == str(b).upper()
    if a is None or b is None:
        return a is b
    return abs(a - b) <= LEARNED_VALUE_TOLERANCE


# Scene parameters of an actuator channel, ie PT_INDA.SCV1[5]
SCENE_PARAMETER_RE = re.compile(r"\.(SCV1|SCV2|SNA)\[(\d+)\]$")


class SceneValueKind(StrEnum):
    """How the values of a scene member are stored"""

    LEVEL = "level"  # dimmer, 0-100 %
    SWITCH = "switch"  # on / off
    COVER = "cover"  # position (and slats) 0-100, Enet counts from open


@dataclass
class SceneParameter:
    """A scene parameter of an actuator channel, ie PT_INDA.SCV1[5]"""

    uid: str
    type_id: str
    value_type_id: str | None = None
    value: Any = None


class SceneMember:
    """An actuator channel taking part in a scene"""

    def __init__(self, scene, raw):
        self.scene = scene
        self.device_uid = raw["deviceUID"]
        self.channel_number = raw["channelNumber"]
        self.channel = scene.client.get_channel(self.device_uid, self.channel_number)
        self.level = None  # SCV1[n]
        self.slats = None  # SCV2[n], blinds only
        self.slot = None
        for raw_param in raw.get("sceneParameters", []):
            match = SCENE_PARAMETER_RE.search(raw_param.get("deviceParameterTypeID", ""))
            if not match:
                continue
            param = SceneParameter(raw_param["deviceParameterUID"], match.string)
            if match.group(1) == "SCV1":
                self.level = param
                self.slot = int(match.group(2))
            elif match.group(1) == "SCV2":
                self.slats = param

    def __repr__(self):
        return (
            f"{self.__class__.__name__} ({self.name} level={self.level} slats={self.slats})"
        )

    @property
    def uid(self):
        """The uid of the channel, as used by the entities of the channel"""
        return f"{self.device_uid}-{self.channel_number}"

    @property
    def name(self):
        if self.channel is None:
            return self.uid
        return f"{self.channel.device.name} {self.channel.name}"

    @property
    def kind(self):
        """The kind of values of the member, None until the values are loaded"""
        if self.slats is not None or (
            self.channel is not None
            and self.channel.application_mode == ChannelApplicationMode.BLINDS
        ):
            return SceneValueKind.COVER
        if self.level is None or self.level.value_type_id is None:
            return None
        if isinstance(self.level.value, str) or "SWITCH" in self.level.value_type_id:
            return SceneValueKind.SWITCH
        return SceneValueKind.LEVEL

    async def load_values(self):
        """Read the values configured on the server"""
        for param in (self.level, self.slats):
            if param is None:
                continue
            values = await self.scene.client.get_parameter_to_be_values(param.uid)
            if values:
                param.value_type_id = values[0].get("valueTypeID")
                param.value = values[0].get("value")

    def encode(self, on=None, level=None):
        """Return the SCV1 value for the member: on/off, or a level 0-100"""
        if self.kind == SceneValueKind.SWITCH:
            if on is None:
                on = bool(level)
            return "ON" if on else "OFF"
        if level is None:
            level = 100 if on else 0
        return int(level)

    STATE_FUNCTIONS = (
        ChannelTypeFunctionName.ON_OFF,
        ChannelTypeFunctionName.BRIGHTNESS,
        ChannelTypeFunctionName.COVER_POSITION,
        ChannelTypeFunctionName.TILT_POSITION,
    )

    @classmethod
    def channel_state(cls, channel):
        """A snapshot of the state of a channel, for current_values()"""
        return {f: channel.get_current_value(f) for f in cls.STATE_FUNCTIONS}

    def current_values(self, state=None):
        """Return (level, slats) for the current state of the channel, to
        store it in the scene, or for a snapshot from channel_state(). Raises
        ValueError if the state is unknown."""
        channel = self.channel
        if channel is None:
            raise ValueError(f"{self.name} is not a known channel")
        if state is None:
            state = self.channel_state(channel)
        if self.kind == SceneValueKind.COVER:
            position = state.get(ChannelTypeFunctionName.COVER_POSITION)
            tilt = state.get(ChannelTypeFunctionName.TILT_POSITION)
            if position is None:
                raise ValueError(f"The position of {self.name} is unknown")
            return int(position), (None if tilt is None else int(tilt))

        on = state.get(ChannelTypeFunctionName.ON_OFF)
        brightness = None
        if channel.application_mode == ChannelApplicationMode.LIGHT_DIMMING:
            brightness = state.get(ChannelTypeFunctionName.BRIGHTNESS)
        if on is None and brightness is None:
            raise ValueError(f"The state of {self.name} is unknown")
        if on is False:
            return self.encode(on=False), None
        if brightness is not None and self.kind == SceneValueKind.LEVEL:
            return self.encode(level=brightness), None
        return self.encode(on=True), None


class EnetScene:
    """A scene on the Enet server ("scene action") and its members"""

    # The server creates scenes per location itself, ie ~LocSceneLightsOn_Kitchen
    AUTOMATIC_PREFIX = "~"
    AUTOMATIC_NAME_RE = re.compile(r"^~LocScene(Lights|Blinds)(On|Off)_(.+)$")

    def __init__(self, client, raw):
        self.client = client
        self.uid = raw["uid"]
        self.name = raw.get("name", "")
        # The location of the scenes the server makes for locations, else None
        self.location_uid = raw.get("locationUID")
        # Other actions run by the scene, like other scenes
        self.additional_actions = raw.get("additionalActionUIDs") or []
        self.members = [SceneMember(self, member) for member in raw.get("deviceChannels", [])]
        self.values_loaded = False
        self._subscribers = []
        self._kept = set()  # uids of channels in keep_light_states()

    def __repr__(self):
        return f"{self.__class__.__name__} ({self.name} members={self.members})"

    @classmethod
    def is_automatic_name(cls, name):
        """True for the scenes the server creates itself"""
        return name.startswith(cls.AUTOMATIC_PREFIX)

    @property
    def automatic(self):
        return self.is_automatic_name(self.name)

    @property
    def display_name(self):
        """The name without the [libenet...] prefix, and automatic scenes as
        "<location> lights on" instead of ~LocSceneLightsOn_<location>"""
        name = self.name
        if name.startswith("[libenet") and "]" in name:
            return name.split("]", 1)[-1].strip()
        if match := self.AUTOMATIC_NAME_RE.match(name):
            kind, state, location = match.groups()
            return f"{location} {kind.lower()} {state.lower()}"
        return name

    def subscribe(self, callback):
        """Subscribe to changes of the scene values. The callback is called
        without arguments. Returns a function that unsubscribes the callback."""
        self._subscribers.append(callback)

        def unsubscribe():
            self._subscribers.remove(callback)

        return unsubscribe

    def _notify(self):
        for callback in list(self._subscribers):
            callback()

    def get_member(self, channel_uid):
        """Return the member with the channel uid (device uid-channel number)"""
        return next((m for m in self.members if m.uid == channel_uid), None)

    async def activate(self):
        await self.client.activate_scene(self.uid)

    async def load_values(self):
        """Read the values of all members from the server"""
        for member in self.members:
            await member.load_values()
        self.values_loaded = True
        self._notify()

    @contextlib.asynccontextmanager
    async def keep_light_states(self, channels):
        """Switch lights and switches that are on back to their level after
        programming them in this block: programming restarts the actuator, and
        they come back off. Only those that are off afterwards are switched, so
        a change made meanwhile is kept. Channels already kept by an outer
        block are left to it."""
        states = {}
        for channel in channels:
            if channel is None or channel.uid in self._kept:
                continue
            if channel.application_mode not in RESTORED_MODES:
                continue
            states[channel.uid] = (
                channel,
                channel.get_current_value(ChannelTypeFunctionName.ON_OFF),
                channel.get_current_value(ChannelTypeFunctionName.BRIGHTNESS),
            )
        self._kept.update(states)
        try:
            yield
        finally:
            self._kept.difference_update(states)
            was_on = [(channel, brightness) for channel, on, brightness in states.values() if on]
            if was_on:
                await asyncio.sleep(RESTORE_DELAY_SECONDS)
            for channel, brightness in was_on:
                if channel.get_current_value(ChannelTypeFunctionName.ON_OFF) is not False:
                    continue
                log.debug("Switching %s back on after programming it", channel.name)
                try:
                    if channel.application_mode == ChannelApplicationMode.LIGHT_DIMMING and brightness:
                        await channel.set_value(ChannelTypeFunctionName.BRIGHTNESS, brightness)
                    else:
                        await channel.turn_on()
                except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
                    log.warning("Could not switch %s back on: %s", channel.name, e)

    async def set_member_values(self, member, level=None, slats=None):
        """Write new values of a member to the scene. level is the SCV1 value
        (see SceneMember.encode()), slats the SCV2 value of blinds. A light
        that is on is switched back on after programming."""
        if member.level is not None and member.level.value_type_id is None:
            await member.load_values()
        writes = [
            (param, value)
            for param, value in ((member.level, level), (member.slats, slats))
            if value is not None and param is not None
        ]
        try:
            if not writes:
                return
            async with self.keep_light_states([member.channel]):
                for param, value in writes:
                    if param.value_type_id is None:
                        raise EnetServerError(f"The value type of {param.type_id} is unknown")
                    await self.client.write_device_parameter(
                        param.uid, [{"valueTypeID": param.value_type_id, "value": value}]
                    )
                    param.value = value
        finally:
            self._notify()

    async def reload_members(self):
        """Read the members of the scene from the server again. Members that
        stay keep their loaded values, new members load theirs."""
        raw = await self.client.get_scene_action(self.uid)
        if raw is None:
            raise EnetServerError(f"The scene {self.name} is gone from the server")
        old = {member.uid: member for member in self.members}
        members = []
        for raw_member in raw.get("deviceChannels", []):
            member = SceneMember(self, raw_member)
            if member.uid in old:
                member = old[member.uid]
            elif self.values_loaded:
                await member.load_values()
            members.append(member)
        self.members = members

    async def add_member(self, channel):
        """Add an actuator channel to the scene and program its links. Returns
        the new member, with the default values of the scene slot loaded; set
        its values with set_member_values()."""
        if self.get_member(channel.uid) is not None:
            raise ValueError(f"{channel.name} is already a member of the scene {self.name}")
        try:
            async with self.keep_light_states([channel]):
                await self.client.add_scene_member(
                    self.uid, channel.device.uid, channel.channel["no"]
                )
            await self.reload_members()
        finally:
            self._notify()
        member = self.get_member(channel.uid)
        if member is None:
            raise EnetServerError(f"{channel.name} was not added to the scene {self.name}")
        if not self.values_loaded:  # otherwise loaded by reload_members()
            await member.load_values()
        return member

    async def remove_member(self, member):
        """Remove a member from the scene and its links from the device. A
        light that is on is switched back on after programming.

        Removing a member resets its scene values on the server to the defaults
        of the slot, but not in the device, so the server marks the device as
        modified. The defaults are programmed as well.
        """
        try:
            async with self.keep_light_states([member.channel]):
                await self.client.remove_scene_member(
                    self.uid, member.device_uid, member.channel_number
                )
                for param in (member.level, member.slats):
                    if param is None:
                        continue
                    values = await self.client.get_parameter_to_be_values(param.uid)
                    current = await self.client.get_parameter_current_values(param.uid)
                    if values and values != current:
                        await self.client.write_device_parameter(param.uid, values)
            await self.reload_members()
        finally:
            self._notify()

    @classmethod
    async def create(cls, client, name, channels, icon=DEFAULT_SCENE_ICON):
        """Create a scene on the server with the channels as members, at their
        current state. Returns the scene.

        Adding the members programs their links, which restarts them: they are
        switched back on, and then learn the state they had before. If a member
        can't be added, the scene is deleted again.
        """
        states = {channel.uid: SceneMember.channel_state(channel) for channel in channels}
        full_name = app_scene_name(name, icon)
        scene = cls(client, {"uid": await client.create_scene_action(full_name),
                             "name": full_name})
        scene.values_loaded = True  # no members yet
        try:
            async with scene.keep_light_states(channels):
                for channel in channels:
                    await scene.add_member(channel)
        except Exception:
            log.warning("Scene %s: a member could not be added, deleting the scene", full_name)
            try:
                await scene.delete()
            except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
                log.warning("Scene %s could not be deleted: %s", full_name, e)
            raise
        # Let dimmers that were switched back on reach their level
        await asyncio.sleep(LEARN_SETTLE_SECONDS)
        await scene.store_current_values(states)
        return scene

    async def delete(self):
        """Delete the scene on the server. The server removes the links from the
        devices but marks them as modified, so their links are programmed
        again, and lights that were on are switched back on."""
        channels = [member.channel for member in self.members]
        device_uids = list(dict.fromkeys(member.device_uid for member in self.members))
        async with self.keep_light_states(channels):
            await self.client.delete_scene_action(self.uid)
            for device_uid in device_uids:
                try:
                    await self.client.write_links(device_uid)
                except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
                    log.warning("Scene %s: the links of a device could not be "
                                "programmed after deleting it: %s", self.name, e)
        self.members = []
        self._notify()

    async def rename(self, name):
        """Change the name of the scene, keeping its icon in the Enet app"""
        icon = icon_number(self.name)
        full_name = app_scene_name(name, DEFAULT_SCENE_ICON if icon is None else icon)
        await self.client.rename_scene_action(self.uid, full_name)
        self.name = full_name
        self._notify()

    async def set_values(self, wanted):
        """Make the values of the members the wanted ones: member uid -> (level,
        slats), see SceneMember.encode(). Returns the number of members changed.

        When every member is at its wanted state, as after setting them up in
        a scene editor, the members learn it from one scene telegram (see
        store_current_values()). Otherwise only the changed members are
        programmed: learning would store the current state of the others too.
        """
        if not self.values_loaded:
            await self.load_values()
        changes = []
        for member in self.members:
            if member.uid not in wanted:
                continue
            level, slats = wanted[member.uid]
            if member.level is not None and same_scene_value(member.level.value, level):
                level = None
            if member.slats is not None and same_scene_value(member.slats.value, slats):
                slats = None
            if level is not None or slats is not None:
                changes.append((member, level, slats))
        if not changes:
            return 0

        def at_wanted(member):
            if member.uid not in wanted or member.channel is None:
                return False
            try:
                level, slats = member.current_values()
            except ValueError:
                return False
            want_level, want_slats = wanted[member.uid]
            return same_scene_value(level, want_level) and (
                want_slats is None or same_scene_value(slats, want_slats)
            )

        if all(at_wanted(member) for member in self.members):
            return await self.store_current_values()
        for member, level, slats in changes:
            await self.set_member_values(member, level, slats)
        return len(changes)

    async def store_current_values(self, states=None):
        """Store the current state of every member in the scene, or the state
        in states (channel uid -> SceneMember.channel_state()). Returns the
        number of members that changed.

        The members learn their current state from one scene telegram, and the
        learned values of the changed members are read back, so the values on
        the server match the actuators. This is much faster than programming
        every actuator, and the lights do not restart. A member whose learned
        value cannot be read or is not its current state is programmed with
        the value instead. Nothing is sent when no member changed.
        """
        if not self.values_loaded:
            await self.load_values()
        changes = []
        unknown = []
        for member in self.members:
            if member.channel is None:
                unknown.append(member)
                continue
            level, slats = member.current_values((states or {}).get(member.uid))
            if member.level is not None and member.level.value == level:
                level = None
            if member.slats is not None and member.slats.value == slats:
                slats = None
            if level is not None or slats is not None:
                changes.append((member, level, slats))
        if not changes:
            return 0

        try:
            await self.client.store_scene_values(self.uid)
        except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
            log.warning(
                "Scene %s: the members could not learn their state (%s), "
                "programming the values instead", self.name, e
            )
            for member, level, slats in changes:
                await self.set_member_values(member, level, slats)
            return len(changes)

        await asyncio.sleep(LEARN_SETTLE_SECONDS)
        try:
            for member, level, slats in changes:
                if not await self._read_learned_values(member, level, slats):
                    await self.set_member_values(member, level, slats)
            # Their state is not known, but they learned it as well
            for member in unknown:
                log.warning(
                    "Scene %s: %s is not a known channel, reading its learned value",
                    self.name, member.uid,
                )
                await self._read_learned_values(member, None, None)
        finally:
            self._notify()
        return len(changes)

    async def _read_learned_values(self, member, level, slats):
        """Read the learned values of a member from its actuator. Returns False
        if they could not be read or are not the expected level and slats."""
        ok = True
        for param, expected in ((member.level, level), (member.slats, slats)):
            if param is None:
                continue
            try:
                values = await self.client.read_device_parameter(param.uid)
            except (EnetServerError, aiohttp.ClientError, TimeoutError) as e:
                log.warning(
                    "Scene %s: could not read the learned value of %s (%s), "
                    "programming it instead", self.name, member.name, e
                )
                ok = False
                continue
            if values:
                param.value = values[0].get("value")
                param.value_type_id = values[0].get("valueTypeID") or param.value_type_id
            if expected is not None and not same_scene_value(param.value, expected):
                log.warning(
                    "Scene %s: %s learned %s instead of %s, programming it instead",
                    self.name, member.name, param.value, expected,
                )
                ok = False
        return ok
