"""Constants for the Home Inventory integration."""

from __future__ import annotations

DOMAIN = "home_inventory"
NAME = "Home Inventory"

CONF_URL = "url"
CONF_TOKEN = "api_token"
CONF_PANEL = "sidebar_panel"
CONF_SCAN_INTERVAL = "scan_interval"
# Voice answers also sent as a notification: "off", "alexa" or "all" voice assistants.
CONF_VOICE_NOTIFY = "voice_notify"
# Send "maintenance due" notices to the same notify action.
CONF_MAINTENANCE_NOTIFY = "maintenance_notify"
CONF_NOTIFY_SERVICE = "notify_service"  # a notify action, e.g. "mobile_app_pixel_8"
# Messages to repair customers when a job's status changes (via a notify
# action that reaches customers: an SMS gateway or email).
CONF_CUSTOMER_NOTIFY_SERVICE = "customer_notify_service"
CONF_CUSTOMER_NOTIFY_TO = "customer_notify_to"  # "email" or "phone"
CONF_CUSTOMER_NOTIFY_STATUSES = "customer_notify_statuses"
CUSTOMER_STATUSES = ("awaiting_approval", "waiting_for_parts", "in_progress", "completed")
CUSTOMER_STATUS_TEXT = {
    "awaiting_approval": "has been looked at and needs your go-ahead",
    "waiting_for_parts": "is waiting for parts",
    "in_progress": "is being worked on",
    "completed": "is ready for collection",
}
VOICE_NOTIFY_OFF = "off"
VOICE_NOTIFY_ALEXA = "alexa"
VOICE_NOTIFY_ALL = "all"

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
    "maintenance_completed",
    "maintenance_due",
    "repair_job_status",
    "tag_scanned",
    "shopping_list_updated",
    "checkup_due",
    "meter_reading_due",
)
