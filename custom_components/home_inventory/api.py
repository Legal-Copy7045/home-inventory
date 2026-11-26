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
import re
import socket
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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


def user_headers(user: User | str | None) -> dict[str, str]:
    """Who is asking, for the App to apply their role.

    Voice and automation requests pass only the Home Assistant user *id*
    (an opaque identifier); the App looks the person's name and
    administrator rights up in Home Assistant itself, so no personal details
    leave the integration.  The sidebar proxy (Home Assistant Container/Core,
    where the App can't ask Home Assistant) passes the signed-in ``User``.
    """
    if user is None:
        return {}
    if isinstance(user, str):
        return {"X-Remote-User-Id": user[:128]}
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


def _numeric_ipv4(host: str) -> str | None:
    """The address a numeric IPv4 spelling stands for (``inet_aton`` forms), else None."""
    if not re.fullmatch(r"(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}", host):
        return None
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return "0.0.0.0"  # malformed: never local


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
    numeric = _numeric_ipv4(host)
    if numeric is not None:
        # 134744072, 0x08080808, 010.0.0.1...: old IPv4 spellings the resolver accepts.
        return "local" if _local_ip(numeric) else "refuse"
    if host == "localhost" or "." not in host or host.endswith(LOCAL_SUFFIXES):
        return "local"
    return "resolve"


async def ensure_local_url(url: str) -> str | None:
    """Refuse an App address that isn't on this machine or your own network.
    A name like ``nas.example.com`` is accepted only if every address it
    resolves to is private; then the checked address is returned, so the
    caller connects to exactly that address (no second lookup to rebind)."""
    kind = local_host_kind(url)
    if kind == "local":
        return None
    if kind == "resolve":
        host = urlsplit(url).hostname or ""
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as err:
            raise InventoryConnectionError(f"Cannot find {host}: {err}") from err
        addrs = [str(i[4][0]) for i in infos]
        if addrs and all(_local_ip(a) for a in addrs):
            return addrs[0]
    raise InventoryNotLocalError(
        "The Home Inventory App address must be on this machine or your own network "
        "(for example http://homeinventory:8100 or http://192.168.1.20:8100)")


class InventoryClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self.base_url = base_url.rstrip("/")
        self._token = token

    async def _target(self) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Where to send a request: checked before every request.  A name that
        needs a lookup is resolved and checked here, and the request goes to that
        checked address (with the name in Host, and for https in the TLS check)."""
        addr = await ensure_local_url(self.base_url)
        if addr is None:
            return self.base_url, {}, {}
        parts = urlsplit(self.base_url)
        host_ip = f"[{addr}]" if ":" in addr else addr
        netloc = host_ip + (f":{parts.port}" if parts.port else "")
        base = urlunsplit((parts.scheme, netloc, parts.path, "", "")).rstrip("/")
        extra = {"server_hostname": parts.hostname} if parts.scheme == "https" else {}
        return base, {"Host": parts.netloc.rsplit("@", 1)[-1]}, extra

    def _headers(self, user: User | str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", **user_headers(user)}

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        user: User | str | None = None,
        timeout: float = 30,
        purpose: str | None = None,
    ) -> Any:
        base, host_hdr, extra = await self._target()
        url = f"{base}/api/v1/{path.lstrip('/')}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            async with self._session.request(
                method,
                url,
                json=json_body,
                params=clean,
                # A request with no user can't see customer data, except for
                # a declared purpose the App allows (the repair-ready message).
                headers={**self._headers(user), **host_hdr, **({"X-Home-Inventory-Purpose": purpose} if purpose else {})},
                timeout=aiohttp.ClientTimeout(total=timeout),
                **extra,
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

    async def fetch_bytes(self, path: str, user: User | str | None = None, timeout: float = 20) -> tuple[bytes, str]:
        """A file from the App (a photo), with its content type."""
        base, host_hdr, extra = await self._target()
        url = f"{base}/api/v1/{path.lstrip('/')}"
        try:
            async with self._session.get(url, headers={**self._headers(user), **host_hdr},
                                         timeout=aiohttp.ClientTimeout(total=timeout), **extra) as resp:
                if resp.status >= 400:
                    raise InventoryApiError(resp.status, "Not available")
                return await resp.read(), resp.headers.get("Content-Type", "application/octet-stream")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise InventoryConnectionError(str(err)) from err

    async def health(self) -> dict[str, Any]:
        base, host_hdr, extra = await self._target()
        try:
            async with self._session.get(f"{base}/api/health", headers=host_hdr,
                                         timeout=aiohttp.ClientTimeout(total=10), **extra) as resp:
                resp.raise_for_status()
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise InventoryConnectionError(str(err)) from err

    async def stats(self) -> dict[str, Any]:
        return await self.request("GET", "stats")

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events from the App's server-sent event stream."""
        base, host_hdr, extra = await self._target()
        url = f"{base}/api/v1/events/stream"
        try:
            async with self._session.get(
                url,
                headers={**self._headers(), **host_hdr, "Accept": "text/event-stream"},
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=90),
                **extra,
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
        base, host_hdr, extra = await self._target()
        return await self._session.request(
            method,
            f"{base}/{path_and_query.lstrip('/')}",
            headers={**headers, **host_hdr, "Authorization": f"Bearer {self._token}"},
            data=body,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=300),
            **extra,
        )
