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
from .const import (
    CONF_CUSTOMER_NOTIFY_SERVICE,
    CONF_CUSTOMER_NOTIFY_STATUSES,
    CONF_CUSTOMER_NOTIFY_TO,
    CONF_MAINTENANCE_NOTIFY,
    CONF_NOTIFY_SERVICE,
    CUSTOMER_STATUS_TEXT,
    DOMAIN,
    EVENT_TYPES,
    NAME,
)

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
        data = message.get("data") or {}
        self.hass.bus.async_fire(f"{DOMAIN}_{etype}", data)
        if etype == "maintenance_due":
            self._notify_maintenance(data)
        if etype == "repair_job_status":
            self._notify_customer(data)
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _notify_maintenance(self, data: dict[str, Any]) -> None:
        """Send a "maintenance due" notice to the phone, if that's turned on."""
        opts = self.config_entry.options if self.config_entry else {}
        service = opts.get(CONF_NOTIFY_SERVICE)
        if not opts.get(CONF_MAINTENANCE_NOTIFY) or not service or not data.get("message"):
            return

        async def send() -> None:
            try:
                await self.hass.services.async_call(
                    "notify", service, {"title": f"{NAME}: maintenance", "message": data["message"]}, blocking=False
                )
            except Exception as err:  # noqa: BLE001 - a notice must never break the event stream
                _LOGGER.warning("Couldn't send the maintenance notice to notify.%s: %s", service, err)

        self.hass.async_create_task(send())


    @callback
    def _notify_customer(self, data: dict[str, Any]) -> None:
        """Tell the customer their repair has moved on, if that's set up.

        Events carry no customer details, so the job (with the contact) is
        read from the App here, only when a message is going to be sent."""
        opts = self.config_entry.options if self.config_entry else {}
        service = opts.get(CONF_CUSTOMER_NOTIFY_SERVICE)
        status = data.get("status")
        if not service or not data.get("job_id") or status not in CUSTOMER_STATUS_TEXT \
                or status not in (opts.get(CONF_CUSTOMER_NOTIFY_STATUSES) or ["completed"]):
            return

        async def send() -> None:
            try:
                job = await self.client.request("GET", f"repair-jobs/{int(data['job_id'])}", purpose="customer-message")
            except InventoryError as err:
                _LOGGER.warning("Couldn't read repair job %s for the customer message: %s", data.get("number"), err)
                return
            cust = job.get("customer") or {}
            to = cust.get("email") if opts.get(CONF_CUSTOMER_NOTIFY_TO) == "email" else cust.get("phone")
            if not to:
                return
            first = (cust.get("name") or "").split(" ")[0]
            thing = (job.get("item") or {}).get("name") or "equipment"
            greeting = f"Hi {first}, your" if first else "Your"
            message = f"{greeting} {thing} (job {job.get('number')}) {CUSTOMER_STATUS_TEXT[status]}."
            try:
                await self.hass.services.async_call(
                    "notify", service, {"title": f"Repair {job.get('number')}", "message": message, "target": [to]},
                    blocking=False)
            except Exception as err:  # noqa: BLE001 - a message must never break the event stream
                _LOGGER.warning("Couldn't send the customer message with notify.%s: %s", service, err)

        self.hass.async_create_task(send())
