"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import InventoryCoordinator


class InventoryEntity(CoordinatorEntity[InventoryCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: InventoryCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Home Inventory",
            model="Inventory App",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=None,
        )
