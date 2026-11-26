"""Config flow for Enet Smart Home integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.helpers.selector import BooleanSelector

from . import aioenet
from .aioenet import normalize_url
from .const import (
    CONF_LOCATION_SCENES,
    CONF_SERIAL_NUMBER,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

TITLE = "Enet Smart Home"
# Option of a discovered server: HTTPS (True) or HTTP
CONF_SECURE = "secure_connection"

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

    def __init__(self) -> None:
        super().__init__()
        # Url and serial number of a discovered server
        self._discovered: dict[str, Any] = {}

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
        self, discovery_info: ZeroconfServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """An Enet server found by zeroconf: it announces a web service on port
        80 and a secure one on port 443, with its address and serial number"""
        _LOGGER.debug("Enet zeroconf discovery: %s", discovery_info)
        properties = discovery_info.properties
        host = properties.get("address") or str(discovery_info.ip_address)
        return await self._async_discovered(
            host, properties.get("deviceSerialNumber"),
            # An entry may use its name, like eNetServer.local
            discovery_info.hostname.rstrip("."),
        )

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """An Enet server found by SSDP"""
        _LOGGER.debug("Enet SSDP discovery: %s", discovery_info)
        host = urlparse(discovery_info.ssdp_location or "").hostname
        if not host:
            return self.async_abort(reason="cannot_connect")
        return await self._async_discovered(
            host, discovery_info.upnp.get("serialNumber")
        )

    async def _async_discovered(
        self, host: str, serial: str | None, hostname: str | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show the setup form with the url of a discovered server, unless it is
        set up already"""
        names = {host.lower(), (hostname or host).lower()}
        for entry in self._async_current_entries(include_ignore=False):
            url = entry.data.get(CONF_URL, "")
            if (urlparse(url).hostname or "").lower() in names:
                if serial and entry.data.get(CONF_SERIAL_NUMBER) != serial:
                    # Remembered, to find the server when its address changes
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_SERIAL_NUMBER: serial}
                    )
                return self.async_abort(reason="already_configured")
            if serial and entry.data.get(CONF_SERIAL_NUMBER) == serial:
                # The server got a new address
                old = urlparse(url)
                netloc = f"{host}:{old.port}" if old.port else host
                new_url = normalize_url(old._replace(netloc=netloc).geturl())
                _LOGGER.info("The Enet server %s moved to %s", url, new_url)
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_URL: new_url}, unique_id=new_url
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="already_configured")

        # One flow for both web services and SSDP; ignoring it ignores the server
        await self.async_set_unique_id(normalize_url(f"http://{host}"))
        self._abort_if_unique_id_configured()
        self._discovered = {CONF_HOST: host, CONF_SERIAL_NUMBER: serial}
        self.context["title_placeholders"] = {"name": f"{TITLE} ({host})"}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Set up a discovered server: choose HTTPS or HTTP, and log in"""
        host = self._discovered[CONF_HOST]
        errors: dict[str, str] = {}

        if user_input is not None:
            scheme = "https" if user_input[CONF_SECURE] else "http"
            url = normalize_url(f"{scheme}://{host}")
            if self._url_configured(url):
                return self.async_abort(reason="already_configured")
            data = {
                CONF_URL: url,
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            errors = await async_try_connect(self.hass, data)
            if not errors:
                await self.async_set_unique_id(url, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                if serial := self._discovered.get(CONF_SERIAL_NUMBER):
                    data[CONF_SERIAL_NUMBER] = serial
                return self.async_create_entry(title=TITLE, data=data)

        defaults = user_input or {
            # Enet servers announce a secure web service besides the plain one
            CONF_SECURE: True,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "admin",
        }
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_SECURE, default=defaults[CONF_SECURE]): BooleanSelector(),
                vol.Required(CONF_USERNAME, default=defaults[CONF_USERNAME]): str,
                vol.Required(CONF_PASSWORD, default=defaults[CONF_PASSWORD]): str,
            }),
            description_placeholders={CONF_HOST: host},
            errors=errors,
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Choose whether the scenes the Enet server makes for locations are added."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        # Not self.config_entry: Home Assistant sets that itself in newer versions
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self._entry.options.get(CONF_LOCATION_SCENES, False)
        schema = vol.Schema(
            {vol.Required(CONF_LOCATION_SCENES, default=current): BooleanSelector()}
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
