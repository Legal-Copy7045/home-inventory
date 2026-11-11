"""Statistics coordinator and live event listener."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import InventoryAuthError, InventoryClient, InventoryError
from .const import DOMAIN, EVENT_TYPES

_LOGGER = logging.getLogger(__name__)
SIGNAL_CONNECTION = f"{DOMAIN}_connection"


class InventoryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls aggregate statistics; refreshed early whenever the App reports a change."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: InventoryClient, interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
            request_refresh_debouncer=Debouncer(hass, _LOGGER, cooldown=2, immediate=False),
        )
        self.client = client
        self.stream_connected = False
        self.last_event: dict[str, Any] | None = None
        self._listener: asyncio.Task | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.stats()
        except InventoryAuthError as err:
            from homeassistant.exceptions import ConfigEntryAuthFailed

            raise ConfigEntryAuthFailed(str(err)) from err
        except InventoryError as err:
            raise UpdateFailed(str(err)) from err

    @callback
    def start_event_listener(self) -> None:
        self._listener = self.config_entry.async_create_background_task(
            self.hass, self._listen(), name=f"{DOMAIN} event stream"
        )

    async def stop_event_listener(self) -> None:
        if self._listener:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            self._listener = None

    def _set_connected(self, value: bool) -> None:
        if self.stream_connected != value:
            self.stream_connected = value
            async_dispatcher_send(self.hass, SIGNAL_CONNECTION)

    async def _listen(self) -> None:
        backoff = 2
        while True:
            try:
                self._set_connected(True)
                async for message in self.client.events():
                    backoff = 2
                    self._handle(message)
                self._set_connected(False)
            except asyncio.CancelledError:
                self._set_connected(False)
                raise
            except InventoryAuthError:
                self._set_connected(False)
                _LOGGER.error("Home Inventory event stream: API token rejected")
                backoff = 300
            except InventoryError as err:
                self._set_connected(False)
                _LOGGER.debug("Home Inventory event stream disconnected: %s", err)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    @callback
    def _handle(self, message: dict[str, Any]) -> None:
        etype = message.get("type")
        if etype not in EVENT_TYPES:
            return
        self.last_event = message
        self.hass.bus.async_fire(f"{DOMAIN}_{etype}", message.get("data") or {})
        self.hass.async_create_task(self.async_request_refresh())
