"""Config flow for Enet Smart Home integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.components import zeroconf
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import aioenet
from .aioenet import normalize_url
from .const import (
    CONF_LEFTOVER_SCENES,
    CONF_LOCATION_SCENES,
    CONF_SCENE_EDITOR,
    DOMAIN,
    LEFTOVER_DELETE,
    LEFTOVER_KEEP,
)

_LOGGER = logging.getLogger(__name__)

TITLE = "Enet Smart Home"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="http://"): str,
        vol.Required(CONF_USERNAME, default="admin"): str,
        vol.Required(CONF_PASSWORD, default="admin"): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    hub = aioenet.EnetClient(data[CONF_URL], data[CONF_USERNAME], data[CONF_PASSWORD])
    try:
        await hub.simple_login()
    except aioenet.AuthError as err:
        raise InvalidAuth from err
    except (aiohttp.ClientError, TimeoutError, aioenet.EnetServerError) as err:
        raise CannotConnect from err
    finally:
        await hub.simple_logout()


async def async_try_connect(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate the user input, returning the form errors."""
    try:
        await validate_input(hass, data)
    except CannotConnect:
        return {"base": "cannot_connect"}
    except InvalidAuth:
        return {"base": "invalid_auth"}
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected exception")
        return {"base": "unknown"}
    return {}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Enet Smart Home."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Options of the integration: whether the scenes of locations are added."""
        return OptionsFlow(config_entry)

    def _url_configured(self, url: str, exclude_entry_id: str | None = None) -> bool:
        """Return True if another config entry already uses the Enet server at url."""
        url = normalize_url(url)
        return any(
            normalize_url(entry.data.get(CONF_URL, "")) == url
            for entry in self._async_current_entries(include_ignore=False)
            if entry.entry_id != exclude_entry_id
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = {**user_input, CONF_URL: normalize_url(user_input[CONF_URL])}
            await self.async_set_unique_id(user_input[CONF_URL])
            self._abort_if_unique_id_configured()
            # Entries of older versions may not have a unique id yet
            if self._url_configured(user_input[CONF_URL]):
                return self.async_abort(reason="already_configured")

            errors = await async_try_connect(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title=TITLE, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Change the url or credentials of the Enet server."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            user_input = {**user_input, CONF_URL: normalize_url(user_input[CONF_URL])}
            if self._url_configured(
                user_input[CONF_URL], exclude_entry_id=reconfigure_entry.entry_id
            ):
                errors["base"] = "already_configured"
            else:
                errors = await async_try_connect(self.hass, user_input)

            if not errors:
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    unique_id=user_input[CONF_URL],
                    data_updates=user_input,
                )

        defaults = user_input or reconfigure_entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=defaults[CONF_URL]): str,
                    vol.Required(CONF_USERNAME, default=defaults[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start a reauth flow when the Enet server rejects the login during setup."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for new credentials of the Enet server."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            errors = await async_try_connect(
                self.hass, {**reauth_entry.data, **user_input}
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates=user_input,
                )

        defaults = user_input or reauth_entry.data
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=defaults[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            description_placeholders={CONF_URL: reauth_entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Handle a discovered Enet Server.

        This flow is triggered by the Zeroconf component. It will check if the
        host is already configured and delegate to the import step if not.
        """
        _LOGGER.info("Enet Zeroconf discovery: %s", discovery_info)
        return self._async_abort_discovery()

    async def async_step_ssdp(
        self, discovery_info: config_entries.SsdpServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Handle a discovered Enet Server

        This flow is triggered by the SSDP component. It will check if the
        host is already configured and delegate to the import step if not.
        """
        _LOGGER.info("Enet SSDP discovery: %s", discovery_info)
        return self._async_abort_discovery()

    def _async_abort_discovery(self) -> config_entries.ConfigFlowResult:
        """Abort a discovery flow, as setting up from discovery is not supported yet.

        A discovery step must return a flow result; returning None makes Home
        Assistant log an AttributeError for each discovered server at startup.
        """
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        return self.async_abort(reason="discovery_not_supported")


class OptionsFlow(config_entries.OptionsFlow):
    """Choose whether the scenes the Enet server makes for locations are added."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        # Not self.config_entry: Home Assistant sets that itself in newer versions
        self._entry = entry
        self._options: dict[str, Any] = {}
        self._mirrored: list[str] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._options = user_input
            if self._entry.options.get(CONF_SCENE_EDITOR) and not user_input[CONF_SCENE_EDITOR]:
                from .scene_sync import async_mirrored_scenes  # noqa: PLC0415

                self._mirrored = await async_mirrored_scenes(self.hass, self._entry.entry_id)
                if self._mirrored:
                    return await self.async_step_scene_editor_off()
            return self.async_create_entry(data=user_input)

        options = self._entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCENE_EDITOR, default=options.get(CONF_SCENE_EDITOR, False)
                ): BooleanSelector(),
                vol.Required(
                    CONF_LOCATION_SCENES, default=options.get(CONF_LOCATION_SCENES, False)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_scene_editor_off(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask what happens with the scenes made from the scenes of the server"""
        if user_input is not None:
            if user_input[CONF_LEFTOVER_SCENES] == LEFTOVER_DELETE:
                from .scene_sync import async_delete_mirrored_scenes  # noqa: PLC0415

                # Stopped first: the sync would delete the Enet scenes with them
                scene_sync = getattr(self._entry.runtime_data, "scene_sync", None)
                if scene_sync is not None:
                    scene_sync.async_stop()
                await async_delete_mirrored_scenes(self.hass, self._mirrored)
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="scene_editor_off",
            data_schema=leftover_scenes_schema(),
            description_placeholders={"count": str(len(self._mirrored))},
        )


def leftover_scenes_schema() -> vol.Schema:
    """Keep or delete the Home Assistant scenes made from the scenes of the server"""
    return vol.Schema({
        vol.Required(CONF_LEFTOVER_SCENES, default=LEFTOVER_KEEP): SelectSelector(
            SelectSelectorConfig(
                options=[LEFTOVER_KEEP, LEFTOVER_DELETE],
                mode=SelectSelectorMode.LIST,
                translation_key=CONF_LEFTOVER_SCENES,
            )
        ),
    })


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
