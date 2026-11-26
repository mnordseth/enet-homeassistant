"""The Enet Smart Home integration."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .aioenet import AuthError, EnetClient, EnetServerError, normalize_url
from .const import (
    CONF_LOCATION_SCENES,
    DOMAIN,
    ISSUE_SCENE_SYNC_REMOVED,
    NAME_ENET_CONTROLLER,
)
from .device import async_setup_devices
from .events import async_setup_enet_events

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.SCENE,
    Platform.BUTTON,
    Platform.COVER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
]

type EnetConfigEntry = ConfigEntry[EnetClient]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the actions that are not for an entity"""
    # Imported here: the scene platform pulls in much of Home Assistant
    from .scene import async_setup_services  # noqa: PLC0415

    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EnetConfigEntry) -> bool:
    """Set up Enet Smart Home from a config entry."""
    _LOGGER.debug("Setting up Enet Smart Home entry")

    hub = EnetClient(
        entry.data["url"],
        entry.data["username"],
        entry.data["password"],
        # noconnect=True,
        # load_file="/workspaces/enet-homeassistant/examples/config_entry-enet-oliver.json",
        # ,noconnect=True, load_file="/workspaces/enet-homeassistant/examples/config_entry-enet-01JRNS5MR3V9DX73M7BQ79KC40.json"
    )

    try:
        await hub.initialize()
    except AuthError as e:
        await hub.close()
        raise ConfigEntryAuthFailed(
            "Invalid username or password for Enet Smart Home"
        ) from e
    except (TimeoutError, aiohttp.ClientError, EnetServerError) as e:
        await hub.close()
        raise ConfigEntryNotReady("Failed to connect to Enet Smart Home") from e
    except Exception:
        # Unexpected errors are logged by Home Assistant, just release the session
        await hub.close()
        raise

    if entry.unique_id is None:
        # Entries of older versions have no unique id, the config flow uses the url
        hass.config_entries.async_update_entry(
            entry, unique_id=normalize_url(entry.data["url"])
        )

    try:
        hub.scenes = await hub.get_scene_actions(
            only_libenet=False,
            automatic_scenes=entry.options.get(CONF_LOCATION_SCENES, False),
        )
        hub.scenes_read = True
    except (TimeoutError, aiohttp.ClientError, EnetServerError) as e:
        _LOGGER.warning("Failed to read the scenes of the Enet server: %s", e)
        hub.scenes = []

    entry.runtime_data = hub
    await _async_retire_scene_sync(hass, entry)
    await async_setup_devices(hass, entry, hub)
    async_setup_enet_events(hass, entry, hub)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if hub.scenes:
        # The scene values are only shown as attributes, do not delay the setup
        entry.async_create_background_task(
            hass, hub.load_scene_values(), name=f"enet scene values {hub.baseurl}"
        )
    return True


async def _async_options_updated(hass: HomeAssistant, entry: EnetConfigEntry) -> None:
    """Reload the entry when the options change, to add the chosen scenes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EnetConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Enet Smart Home entry")
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.close()

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: EnetConfigEntry) -> None:
    """Remove what the entry stored when the integration is removed"""
    from .device import floors_store  # noqa: PLC0415

    await floors_store(hass, entry.entry_id).async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_SCENE_SYNC_REMOVED}_{entry.entry_id}")


# Left by the option "Edit Enet scenes in the scene editor" of 2026.11.3 and
# 2026.11.4, which synced the Enet scenes with scenes.yaml
LEGACY_SCENE_EDITOR = "scene_editor"
LEGACY_SCENE_PREFIX = "enet_"  # id in scenes.yaml of the scenes made from Enet scenes


async def _async_retire_scene_sync(hass: HomeAssistant, entry: EnetConfigEntry) -> None:
    """Forget the scene sync, and tell the user about the scenes it left.

    The scenes in scenes.yaml are the user's: they are not changed, the repair
    issue lists them so the user can delete them in the scene editor.
    """
    store: Store[dict] = Store(hass, 1, f"{DOMAIN}_scene_links_{entry.entry_id}")
    links = await store.async_load()
    if LEGACY_SCENE_EDITOR in entry.options:
        options = {k: v for k, v in entry.options.items() if k != LEGACY_SCENE_EDITOR}
        hass.config_entries.async_update_entry(entry, options=options)
    if links is None:
        return
    await store.async_remove()
    if not links:
        return

    def names(made_from_enet: bool) -> str:
        found = sorted(
            str(link.get("name") or ha_id)
            for ha_id, link in links.items()
            if ha_id.startswith(LEGACY_SCENE_PREFIX) == made_from_enet
        )
        return "\n".join(f"- {name}" for name in found) or "- (none)"

    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_SCENE_SYNC_REMOVED}_{entry.entry_id}",
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_SCENE_SYNC_REMOVED,
        translation_placeholders={
            "title": entry.title,
            "from_enet": names(True),
            "to_enet": names(False),
        },
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: EnetConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow manual removal of devices the Enet server no longer reports.

    Devices that still exist on the Enet server (and the controller itself)
    cannot be removed, as they would be recreated on the next reload anyway.
    """
    hub = getattr(config_entry, "runtime_data", None)
    if hub is None:
        return False

    known_uids = {device.uid for device in hub.devices}
    known_uids.add(NAME_ENET_CONTROLLER)

    return not any(
        identifier[0] == DOMAIN and identifier[1] in known_uids
        for identifier in device_entry.identifiers
    )

