"""Client for the Home Inventory App's internal API.

The App owns all inventory data and business logic; this client only calls
it.  Requests carry the App-issued bearer token and, when acting on behalf of
a Home Assistant user, that user's id/name/admin flag so the App can apply
permissions and record who did what.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

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


# ---------------------------------------------------------------------------
# The App is on your own network, never somewhere else
# ---------------------------------------------------------------------------
#
# Requests carry the App's API token and, for a person, their Home Assistant
# user id, name and admin flag (so the App applies their role).  That must
# only ever reach the Home Inventory App on this machine or your own network:
# the Supervisor's internal hostname, a container name, localhost, a .local /
# .lan / .home.arpa / .internal name, a private or link-local address, or a
# name that resolves only to those.  Anything else is refused before a
# request is sent.

LOCAL_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain")


class InventoryNotLocalError(InventoryError):
    """The configured App address isn't on this machine or your own network."""


# Only these count as your own network (``is_private`` would also count
# documentation and other reserved ranges).
LOCAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10",
    "::1/128", "fc00::/7", "fe80::/10"))


def _local_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text.strip("[]").split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in LOCAL_NETWORKS)


def local_host_kind(url: str) -> str:
    """"local" (certainly on your network), "resolve" (a name to look up) or
    "refuse" (a public address, or not an http(s) URL)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "refuse"
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        return "refuse"
    if _local_ip(host):
        return "local"
    try:
        ipaddress.ip_address(host)
        return "refuse"  # a public IP address
    except ValueError:
        pass
    if host == "localhost" or "." not in host or host.endswith(LOCAL_SUFFIXES):
        return "local"
    return "resolve"


async def ensure_local_url(url: str) -> None:
    """Refuse an App address that isn't on this machine or your own network.
    A name like ``nas.example.com`` is accepted only if every address it
    resolves to is private."""
    kind = local_host_kind(url)
    if kind == "local":
        return
    if kind == "resolve":
        host = urlsplit(url).hostname or ""
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        except OSError as err:
            raise InventoryConnectionError(f"Cannot find {host}: {err}") from err
        if infos and all(_local_ip(str(i[4][0])) for i in infos):
            return
    raise InventoryNotLocalError(
        "The Home Inventory App address must be on this machine or your own network "
        "(for example http://homeinventory:8100 or http://192.168.1.20:8100)")


class InventoryClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._checked = local_host_kind(self.base_url) == "local"

    async def _ensure_local(self) -> None:
        """Checked once, before the first request (a name may need a lookup)."""
        if not self._checked:
            await ensure_local_url(self.base_url)
            self._checked = True

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
        purpose: str | None = None,
    ) -> Any:
        await self._ensure_local()
        url = f"{self.base_url}/api/v1/{path.lstrip('/')}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            async with self._session.request(
                method,
                url,
                json=json_body,
                params=clean,
                # A request with no user can't see customer data, except for
                # a declared purpose the App allows (the repair-ready message).
                headers={**self._headers(user), **({"X-Home-Inventory-Purpose": purpose} if purpose else {})},
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

    async def fetch_bytes(self, path: str, user: User | None = None, timeout: float = 20) -> tuple[bytes, str]:
        """A file from the App (a photo), with its content type."""
        await self._ensure_local()
        url = f"{self.base_url}/api/v1/{path.lstrip('/')}"
        try:
            async with self._session.get(url, headers=self._headers(user), timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status >= 400:
                    raise InventoryApiError(resp.status, "Not available")
                return await resp.read(), resp.headers.get("Content-Type", "application/octet-stream")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise InventoryConnectionError(str(err)) from err

    async def health(self) -> dict[str, Any]:
        await self._ensure_local()
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
        await self._ensure_local()
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
        await self._ensure_local()
        return await self._session.request(
            method,
            f"{self.base_url}/{path_and_query.lstrip('/')}",
            headers={**headers, "Authorization": f"Bearer {self._token}"},
            data=body,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=300),
        )
