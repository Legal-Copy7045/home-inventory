"""Summary sensors.  Individual inventory items are deliberately *not*
entities; they live in the App's database."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import InventoryEntity


@dataclass(frozen=True, kw_only=True)
class InvSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]


SENSORS: tuple[InvSensorDescription, ...] = (
    InvSensorDescription(key="total_items", translation_key="total_items", icon="mdi:package-variant-closed",
                         state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement="items", value=lambda d: d["total_items"]),
    InvSensorDescription(key="containers", translation_key="containers", icon="mdi:archive-outline",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d["containers"]),
    InvSensorDescription(key="locations", translation_key="locations", icon="mdi:map-marker-multiple-outline",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d["locations"]),
    InvSensorDescription(key="total_value", translation_key="total_value", icon="mdi:cash-multiple",
                         device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL,
                         value=lambda d: round(d["total_value"], 2)),
    InvSensorDescription(key="on_loan", translation_key="on_loan", icon="mdi:handshake-outline",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d["on_loan"]),
    InvSensorDescription(key="loans_overdue", translation_key="loans_overdue", icon="mdi:clock-alert-outline",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d["loans_overdue"]),
    InvSensorDescription(key="temporarily_relocated", translation_key="temporarily_relocated", icon="mdi:swap-horizontal",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d["temporarily_relocated"]),
    InvSensorDescription(key="warranties_expiring", translation_key="warranties_expiring", icon="mdi:shield-alert-outline",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d["warranties_expiring"]),
    InvSensorDescription(key="expiring_soon", translation_key="expiring_soon", icon="mdi:calendar-alert",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d.get("expiring_soon", 0)),
    InvSensorDescription(key="expired", translation_key="expired", icon="mdi:calendar-remove",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d.get("expired", 0)),
    InvSensorDescription(key="low_stock", translation_key="low_stock", icon="mdi:cart-arrow-down",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d.get("low_stock", 0)),
    InvSensorDescription(key="maintenance_overdue", translation_key="maintenance_overdue", icon="mdi:wrench-clock",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d.get("maintenance_overdue", 0)),
    InvSensorDescription(key="maintenance_due_soon", translation_key="maintenance_due_soon", icon="mdi:wrench-outline",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d.get("maintenance_due_soon", 0)),
    InvSensorDescription(key="repairs_in_progress", translation_key="repairs_in_progress", icon="mdi:tools",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d.get("repairs_in_progress", 0)),
    InvSensorDescription(key="repairs_awaiting_pickup", translation_key="repairs_awaiting_pickup", icon="mdi:account-clock",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d.get("repairs_awaiting_pickup", 0)),
    InvSensorDescription(key="replenishment_needed", translation_key="replenishment_needed", icon="mdi:cart-alert",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d.get("replenishment_needed", 0)),
    InvSensorDescription(key="shopping_list_open", translation_key="shopping_list_open", icon="mdi:cart-outline",
                         state_class=SensorStateClass.MEASUREMENT, value=lambda d: d.get("shopping_list_open", 0)),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    async_add_entities(InventorySensor(coordinator, d) for d in SENSORS)


class InventorySensor(InventoryEntity, SensorEntity):
    entity_description: InvSensorDescription

    def __init__(self, coordinator, description: InvSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return self.entity_description.value(self.coordinator.data)

    @property
    def native_unit_of_measurement(self):
        if self.entity_description.key == "total_value" and self.coordinator.data:
            return self.coordinator.data.get("currency")
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        key = self.entity_description.key
        if key == "total_value":
            return {"records_with_price": d.get("valued_records")}
        if key == "warranties_expiring":
            return {"window_days": d.get("warranty_window_days")}
        if key == "expiring_soon":
            return {"window_days": d.get("expiry_window_days")}
        if key == "total_items":
            return {"records": d.get("item_records"), "products": d.get("products"), "last_activity": d.get("last_activity")}
        return None
