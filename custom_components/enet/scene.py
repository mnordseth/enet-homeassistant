"""Support for Enet scenes"""
import logging
from homeassistant.components.scene import Scene as SceneEntity
from homeassistant.helpers.entity import DeviceInfo

from homeassistant.const import (
    ATTR_IDENTIFIERS,
    ATTR_NAME,
)

from .const import DOMAIN, NAME_ENET_CONTROLLER, NAME_ENET_SERVER

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Add Enet scenes"""
    hub = hass.data[DOMAIN][entry.entry_id]

    scenes = await hub.get_scenes()
    for scene_name in scenes:
        scene_entity = EnetSceneEntity(hub, scene_name, scenes[scene_name])
        async_add_entities([scene_entity])

    _LOGGER.info("Finished async setup()")


class EnetSceneEntity(SceneEntity):
    """Representation of a Scene entity."""

    def __init__(self, hub, name, uid):
        self._name = name
        self.uid = uid
        self.hub = hub
        _LOGGER.info("EnetSceneEntity.init()  done %s", self.name)

    @property
    def name(self):
        name = self._name
        if name.startswith("[libenet") and "]" in name:
            name = name.split("]", 1)[-1]
        return name

    @property
    def unique_id(self):
        return self.uid

    async def async_activate(self, **kwargs):
        """Activate Enet scene."""
        await self.hub.activate_scene(self.uid)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device (service) info."""
        return DeviceInfo(
            {
                ATTR_IDENTIFIERS: {(DOMAIN, NAME_ENET_CONTROLLER)},
                ATTR_NAME: NAME_ENET_SERVER,
            }
        )
