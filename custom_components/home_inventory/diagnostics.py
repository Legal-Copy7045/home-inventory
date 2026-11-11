"""Diagnostics (download from the integration page)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import InventoryError
from .const import CONF_TOKEN, DOMAIN

REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    runtime = hass.data[DOMAIN][entry.entry_id]
    try:
        health = await runtime.client.health()
    except InventoryError as err:
        health = {"error": str(err)}
    return {
        "entry": {"data": async_redact_data(dict(entry.data), REDACT), "options": dict(entry.options)},
        "app_health": health,
        "statistics": runtime.coordinator.data,
        "last_update_success": runtime.coordinator.last_update_success,
        "event_stream_connected": runtime.coordinator.stream_connected,
        "last_event": runtime.coordinator.last_event,
    }
