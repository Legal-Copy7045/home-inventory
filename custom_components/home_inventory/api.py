"""Client for the Home Inventory App's internal API.

The App owns all inventory data and business logic; this client only calls
it.  Requests carry the App-issued bearer token and, when acting on behalf of
a Home Assistant user, that user's id/name/admin flag so the App can apply
permissions and record who did what.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
from homeassistant.auth.models import User

_LOGGER = logging.getLogger(__name__)


class InventoryError(Exception):
    """Base error."""


class InventoryAuthError(InventoryError):
    """The token was rejected."""


class InventoryConnectionError(InventoryError):
    """The App could not be reached."""


class InventoryApiError(InventoryError):
    """The App returned an error response."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def user_headers(user: User | None) -> dict[str, str]:
    if user is None:
        return {}
    return {
        "X-Remote-User-Id": user.id,
        "X-Remote-User-Name": (user.name or user.id)[:120],
        "X-Remote-User-Display-Name": (user.name or user.id)[:120],
        "X-Remote-User-Is-Admin": "true" if user.is_admin else "false",
    }


class InventoryClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self.base_url = base_url.rstrip("/")
        self._token = token

    def _headers(self, user: User | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", **user_headers(user)}

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        user: User | None = None,
        timeout: float = 30,
    ) -> Any:
        url = f"{self.base_url}/api/v1/{path.lstrip('/')}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            async with self._session.request(
                method,
                url,
                json=json_body,
                params=clean,
                headers=self._headers(user),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 401:
                    raise InventoryAuthError("The Home Inventory App rejected the API token")
                text = await resp.text()
                if resp.status >= 400:
                    try:
                        detail = json.loads(text).get("detail", text)
                    except ValueError:
                        detail = text
                    raise InventoryApiError(resp.status, str(detail)[:300])
                if not text:
                    return None
                return json.loads(text)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise InventoryConnectionError(f"Cannot reach the Home Inventory App at {self.base_url}: {err}") from err

    async def health(self) -> dict[str, Any]:
        try:
            async with self._session.get(f"{self.base_url}/api/health", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise InventoryConnectionError(str(err)) from err

    async def stats(self) -> dict[str, Any]:
        return await self.request("GET", "stats")

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events from the App's server-sent event stream."""
        url = f"{self.base_url}/api/v1/events/stream"
        try:
            async with self._session.get(
                url,
                headers={**self._headers(), "Accept": "text/event-stream"},
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=90),
            ) as resp:
                if resp.status == 401:
                    raise InventoryAuthError("The Home Inventory App rejected the API token")
                resp.raise_for_status()
                data_lines: list[str] = []
                async for raw in resp.content:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line == "" and data_lines:
                        payload = "\n".join(data_lines)
                        data_lines = []
                        try:
                            yield json.loads(payload)
                        except ValueError:
                            _LOGGER.debug("Ignoring malformed event: %s", payload[:200])
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise InventoryConnectionError(str(err)) from err

    async def raw(
        self, method: str, path_and_query: str, headers: dict[str, str], body: bytes | None
    ) -> aiohttp.ClientResponse:
        """Used by the sidebar proxy; caller must release the response."""
        return await self._session.request(
            method,
            f"{self.base_url}/{path_and_query.lstrip('/')}",
            headers={**headers, "Authorization": f"Bearer {self._token}"},
            data=body,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=300),
        )
