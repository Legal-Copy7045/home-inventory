"""Config flow: Supervisor discovery (Home Assistant OS/Supervised) or manual
URL + token (Home Assistant Container/Core with the companion container)."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import InventoryAuthError, InventoryClient, InventoryError
from .const import (
    CONF_NOTIFY_SERVICE,
    CONF_PANEL,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_URL,
    CONF_VOICE_NOTIFY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NAME,
    VOICE_NOTIFY_ALEXA,
    VOICE_NOTIFY_ALL,
    VOICE_NOTIFY_OFF,
)

_LOGGER = logging.getLogger(__name__)


class HomeInventoryConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] | None = None

    async def _validate(self, url: str, token: str) -> str | None:
        client = InventoryClient(async_get_clientsession(self.hass), url, token)
        try:
            await client.stats()
        except InventoryAuthError:
            return "invalid_auth"
        except InventoryError as err:
            _LOGGER.debug("Validation failed: %s", err)
            return "cannot_connect"
        return None

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """The Home Inventory App announced itself through the Supervisor."""
        cfg = discovery_info.config
        url = f"http://{cfg['host']}:{cfg['port']}"
        data = {CONF_URL: url, CONF_TOKEN: cfg["api_token"]}
        await self.async_set_unique_id(DOMAIN)
        # Keep an existing entry pointed at the App if its hostname/token changed.
        self._abort_if_unique_id_configured(updates=data)
        self._discovered = data
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._discovered is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._validate(self._discovered[CONF_URL], self._discovered[CONF_TOKEN])
            if error is None:
                # Under the Supervisor the App has its own sidebar entry via Ingress.
                return self.async_create_entry(title=NAME, data=self._discovered, options={CONF_PANEL: False})
            errors["base"] = error
        return self.async_show_form(step_id="hassio_confirm", errors=errors)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            error = await self._validate(url, user_input[CONF_TOKEN])
            if error is None:
                return self.async_create_entry(
                    title=NAME,
                    data={CONF_URL: url, CONF_TOKEN: user_input[CONF_TOKEN]},
                    options={CONF_PANEL: user_input.get(CONF_PANEL, True)},
                )
            errors["base"] = error
        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "http://homeinventory:8100")): str,
                vol.Required(CONF_TOKEN): str,
                vol.Optional(CONF_PANEL, default=True): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._validate(entry.data[CONF_URL], user_input[CONF_TOKEN])
            if error is None:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]})
            errors["base"] = error
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HomeInventoryOptionsFlow()


class HomeInventoryOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        notify = sorted(self.hass.services.async_services_for_domain("notify"))
        errors: dict[str, str] = {}
        if user_input is not None:
            service = (user_input.get(CONF_NOTIFY_SERVICE) or "").strip().removeprefix("notify.")
            user_input[CONF_NOTIFY_SERVICE] = service
            if user_input.get(CONF_VOICE_NOTIFY, VOICE_NOTIFY_OFF) != VOICE_NOTIFY_OFF:
                if not service:
                    errors[CONF_NOTIFY_SERVICE] = "notify_service_required"
                elif service not in notify:
                    errors[CONF_NOTIFY_SERVICE] = "notify_service_unknown"
            if not errors:
                return self.async_create_entry(data=user_input)
        opts = {**self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PANEL, default=opts.get(CONF_PANEL, False)): bool,
                    vol.Optional(CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): vol.All(
                        vol.Coerce(int), vol.Range(min=30, max=3600)
                    ),
                    vol.Optional(CONF_VOICE_NOTIFY, default=opts.get(CONF_VOICE_NOTIFY, VOICE_NOTIFY_OFF)): SelectSelector(
                        SelectSelectorConfig(
                            options=[VOICE_NOTIFY_OFF, VOICE_NOTIFY_ALEXA, VOICE_NOTIFY_ALL],
                            translation_key=CONF_VOICE_NOTIFY,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(CONF_NOTIFY_SERVICE, default=opts.get(CONF_NOTIFY_SERVICE, "")): SelectSelector(
                        SelectSelectorConfig(options=notify, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
                    ),
                }
            ),
        )
