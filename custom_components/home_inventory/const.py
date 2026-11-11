"""Constants for the Home Inventory integration."""

from __future__ import annotations

DOMAIN = "home_inventory"
NAME = "Home Inventory"

CONF_URL = "url"
CONF_TOKEN = "api_token"
CONF_PANEL = "sidebar_panel"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 300  # seconds; events trigger refreshes in between

PANEL_URL_PATH = "home-inventory"
PANEL_PROXY_PREFIX = "/api/home_inventory/panel"
STATIC_URL = "/home_inventory_static"

# Event types published by the App; fired on the HA bus as "home_inventory_<type>".
EVENT_TYPES = (
    "item_added",
    "item_removed",
    "item_moved",
    "container_moved",
    "container_inventory_updated",
    "item_loaned",
    "item_returned",
    "warranty_expiring",
    "item_expiring",
    "low_stock",
)
