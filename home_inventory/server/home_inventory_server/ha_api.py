"""Minimal read-only client for Home Assistant's REST API.

As an App it uses the Supervisor proxy and token (``homeassistant_api``);
standalone it uses ``ha_url`` + ``ha_token``.  Only reads entity states.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class HAUnavailable(RuntimeError):
    pass


class HAClient:
    def __init__(self, settings: Settings) -> None:
        if settings.supervisor_token:
            self.base = settings.supervisor_url.rstrip("/") + "/core/api"
            self.token = settings.supervisor_token
        elif settings.ha_url and settings.ha_token:
            self.base = settings.ha_url.rstrip("/") + "/api"
            self.token = settings.ha_token
        else:
            raise HAUnavailable("Home Assistant API access is not available to the App")

    @classmethod
    def maybe(cls, settings: Settings) -> HAClient | None:
        try:
            return cls(settings)
        except HAUnavailable:
            return None

    def _get(self, path: str, timeout: float = 10.0) -> Any:
        try:
            r = httpx.get(f"{self.base}{path}", headers={"Authorization": f"Bearer {self.token}"}, timeout=timeout)
        except httpx.HTTPError as err:
            raise HAUnavailable(f"Home Assistant didn't answer: {err}") from err
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise HAUnavailable(f"Home Assistant answered {r.status_code}")
        return r.json()

    def states(self) -> list[dict[str, Any]]:
        return self._get("/states") or []

    def state(self, entity_id: str) -> dict[str, Any] | None:
        return self._get(f"/states/{entity_id}")
