"""App availability."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SIGNAL_CONNECTION
from .entity import InventoryEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([AppAvailable(hass.data[DOMAIN][entry.entry_id].coordinator, "app_available")])


class AppAvailable(InventoryEntity, BinarySensorEntity):
    _attr_translation_key = "app_available"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.last_update_success)

    @property
    def extra_state_attributes(self):
        return {"live_events": self.coordinator.stream_connected, "url": self.coordinator.client.base_url}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_CONNECTION, self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
