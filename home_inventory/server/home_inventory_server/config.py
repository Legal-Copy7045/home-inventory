"""Runtime configuration.

Configuration is read, in order of precedence, from:

1. Environment variables prefixed with ``HINV_`` (used by the standalone
   container deployment and by tests).
2. ``/data/options.json`` written by the Home Assistant Supervisor from the
   App's configuration tab.
3. Built-in defaults.

Secrets (AI API keys, barcode API keys) only ever arrive through these two
channels; nothing is read from the source tree.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = Path("/data")
# s6-overlay (used by Home Assistant base images) saves the container's
# original environment here when it resets the environment for services.
S6_ENV_DIR = Path("/run/s6/container_environment")


def getenv(name: str, default: str | None = None) -> str | None:
    """Read an environment variable, falling back to s6-overlay's saved copy."""
    value = os.environ.get(name)
    if value is not None:
        return value
    try:
        return (S6_ENV_DIR / name).read_text().strip() or default
    except OSError:
        return default

# Supervisor's Ingress proxy always connects from this address.
SUPERVISOR_INGRESS_IP = "172.30.32.2"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


@dataclass
class Settings:
    data_dir: Path = DEFAULT_DATA_DIR
    # "supervisor" (Home Assistant App behind Ingress) or "standalone"
    # (companion container for Home Assistant Container/Core installs, where
    # the companion integration proxies authenticated requests).
    mode: str = "supervisor"
    ingress_port: int = 8099
    api_port: int = 8100
    trusted_proxies: list[str] = field(default_factory=lambda: [SUPERVISOR_INGRESS_IP])
    log_level: str = "info"

    # Roles
    admin_users: list[str] = field(default_factory=list)
    read_only_users: list[str] = field(default_factory=list)
    default_role: str = "member"

    # AI
    # home_assistant (AI Task, default) | gemini | anthropic | openai | ollama | none
    ai_provider: str = "home_assistant"
    ai_task_entity: str = ""  # e.g. ai_task.google_ai_task; empty = auto-detect
    ai_api_key: str = ""
    ai_model: str = ""
    ai_base_url: str = ""
    ai_timeout: float = 60.0
    ai_max_image_px: int = 1568
    ai_retries: int = 2
    # Rate limits (defaults: Gemini's free tier).  0 = no limit.
    ai_requests_per_minute: int = 15
    ai_tokens_per_minute: int = 250_000
    ai_requests_per_day: int = 500
    # After "too many requests": how long every AI call pauses before retrying.
    ai_retry_cooldown: float = 60.0
    # How long a call someone is waiting for may queue; background work waits as long as needed.
    ai_max_wait_seconds: float = 30.0
    ai_background_retries: int = 5

    # Barcode lookups
    barcode_open_facts: bool = True
    barcode_upcitemdb: bool = True
    barcode_lookup_api_key: str = ""  # barcodelookup.com (commercial)
    barcode_timeout: float = 8.0

    # Uploads
    max_upload_mb: int = 25
    backup_max_mb: int = 2048
    max_image_px: int = 4096
    # How much detail photos keep, for the machine the App runs on:
    # "standard" suits a Raspberry Pi; "high" keeps larger photos and
    # panoramas and sends the AI more detail (more memory and AI tokens).
    photo_detail: str = "standard"
    panorama_height: int = 1600

    # Labels / QR
    qr_base_url: str = ""

    # Warranty notifications
    warranty_notice_days: int = 30
    expiry_notice_days: int = 14

    # Supervisor / Home Assistant access (used for discovery and user roles)
    supervisor_token: str = ""
    supervisor_url: str = "http://supervisor"
    # Standalone (Container/Core) installs: Home Assistant URL + token for AI Task.
    ha_url: str = ""
    ha_token: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "inventory.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "integration_token"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def ai_enabled(self) -> bool:
        return self.ai_provider not in ("", "none")

    @classmethod
    def load(cls, options_path: Path | None = None) -> Settings:
        data_dir = Path(getenv("HINV_DATA_DIR") or str(DEFAULT_DATA_DIR))
        options_file = options_path or Path(getenv("HINV_OPTIONS_FILE") or str(data_dir / "options.json"))
        options: dict[str, Any] = {}
        if options_file.exists():
            try:
                options = json.loads(options_file.read_text())
            except (OSError, ValueError):
                options = {}

        s = cls(data_dir=data_dir)

        def pick(name: str) -> Any:
            value = getenv(f"HINV_{name.upper()}")
            if value is not None:
                return value
            return options.get(name)

        conv = {
            "mode": str,
            "ingress_port": int,
            "api_port": int,
            "trusted_proxies": _as_list,
            "log_level": str,
            "admin_users": _as_list,
            "read_only_users": _as_list,
            "default_role": str,
            "ai_provider": str,
            "ai_task_entity": str,
            "ha_url": str,
            "ha_token": str,
            "ai_api_key": str,
            "ai_model": str,
            "ai_base_url": str,
            "ai_timeout": float,
            "ai_max_image_px": int,
            "ai_retries": int,
            "ai_requests_per_minute": int,
            "ai_tokens_per_minute": int,
            "ai_requests_per_day": int,
            "ai_retry_cooldown": float,
            "ai_max_wait_seconds": float,
            "barcode_open_facts": _as_bool,
            "barcode_upcitemdb": _as_bool,
            "barcode_lookup_api_key": str,
            "barcode_timeout": float,
            "max_upload_mb": int,
            "backup_max_mb": int,
            "max_image_px": int,
            "photo_detail": str,
            "qr_base_url": str,
            "warranty_notice_days": int,
            "expiry_notice_days": int,
        }
        for name, fn in conv.items():
            raw = pick(name)
            if raw is None or raw == "":
                continue
            try:
                setattr(s, name, fn(raw))
            except (TypeError, ValueError):
                pass

        s.supervisor_token = getenv("SUPERVISOR_TOKEN") or getenv("HASSIO_TOKEN") or ""
        if s.default_role not in ("member", "read_only", "admin"):
            s.default_role = "member"
        s.ai_provider = s.ai_provider.lower()
        s.apply_photo_detail(explicit={n for n in ("max_image_px", "ai_max_image_px") if pick(n) not in (None, "")})
        return s

    def apply_photo_detail(self, explicit: set[str] | None = None) -> None:
        """Image sizes for the chosen ``photo_detail`` (explicit size settings win)."""
        explicit = explicit or set()
        self.photo_detail = (self.photo_detail or "standard").lower()
        if self.photo_detail == "high":
            if "max_image_px" not in explicit:
                self.max_image_px = 6144
            if "ai_max_image_px" not in explicit:
                self.ai_max_image_px = 2048
            self.panorama_height = 2400
        else:
            self.photo_detail = "standard"
            self.panorama_height = 1600

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.media_dir, self.backup_dir):
            d.mkdir(parents=True, exist_ok=True)
