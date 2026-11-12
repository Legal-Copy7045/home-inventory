"""Home Inventory companion integration.

The Home Inventory App owns the inventory database and business logic.  This
integration adds Home Assistant-native pieces: summary sensors, actions,
automation events, diagnostics and (on installs without Ingress) a sidebar
panel.  Removing the integration never deletes inventory data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InventoryClient
from .const import CONF_PANEL, CONF_SCAN_INTERVAL, CONF_TOKEN, CONF_URL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import InventoryCoordinator
from .intent import async_setup_intents, async_unload_intents
from .panel import async_register_panel, async_remove_panel
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


@dataclass
class InventoryRuntime:
    client: InventoryClient
    coordinator: InventoryCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = InventoryClient(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
    coordinator = InventoryCoordinator(hass, entry, client, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = InventoryRuntime(client, coordinator)
    coordinator.start_event_listener()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass)
    async_setup_intents(hass)
    if entry.options.get(CONF_PANEL):
        await async_register_panel(hass)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: InventoryRuntime = hass.data[DOMAIN][entry.entry_id]
    await runtime.coordinator.stop_event_listener()
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        async_remove_panel(hass)
        if not hass.data[DOMAIN]:
            async_unload_services(hass)
            async_unload_intents(hass)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _LOGGER.info(
        "Home Inventory integration removed. Inventory data is kept by the Home Inventory App "
        "and is unaffected; uninstall the App to remove it."
    )
