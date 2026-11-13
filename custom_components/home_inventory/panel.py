"""Sidebar panel for installations without Supervisor Ingress.

On Home Assistant OS/Supervised the App's own Ingress sidebar entry is used
and this module is inactive.  On Container/Core installs the integration
registers a sidebar panel that frames the App's UI through an authenticated
reverse proxy inside Home Assistant:

1. The panel (a small web component) calls the authenticated WebSocket
   command ``home_inventory/panel_url`` with the user's HA session.
2. Home Assistant returns a single-use token (60 s) bound to that user.
3. The iframe loads ``/api/home_inventory/panel/_auth?t=<token>``; the view
   exchanges it for an HttpOnly, SameSite=Strict session cookie scoped to
   the proxy path and redirects to the UI.
4. Each proxied request re-checks the session and that the HA user is still
   active, then forwards it to the App with the integration's bearer token
   and the user's identity.  Client cookies/authorization are never
   forwarded.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

import voluptuous as vol
from aiohttp import web
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .api import user_headers
from .const import DOMAIN, PANEL_PROXY_PREFIX, PANEL_URL_PATH, STATIC_URL

_LOGGER = logging.getLogger(__name__)
TOKEN_TTL = 60
SESSION_TTL = 12 * 3600
MAX_TOKENS = 200     # unredeemed sign-in links kept at once (oldest dropped)
MAX_SESSIONS = 500   # panel sessions kept at once (oldest dropped)
COOKIE = "hinv_panel"
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
               "transfer-encoding", "upgrade", "content-length", "content-encoding", "set-cookie"}
FORWARD_REQUEST_HEADERS = {"content-type", "accept", "accept-language", "if-none-match", "if-modified-since", "range"}


@dataclass
class _Grant:
    user_id: str
    expires: float


class PanelSessions:
    def __init__(self) -> None:
        self.tokens: dict[str, _Grant] = {}
        self.sessions: dict[str, _Grant] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        self.tokens = {k: v for k, v in self.tokens.items() if v.expires > now}
        self.sessions = {k: v for k, v in self.sessions.items() if v.expires > now}

    @staticmethod
    def _cap(store: dict[str, _Grant], limit: int) -> None:
        while len(store) >= limit:
            store.pop(next(iter(store)))  # insertion order: the oldest first

    def issue_token(self, user_id: str) -> str:
        self._prune()
        self._cap(self.tokens, MAX_TOKENS)
        token = secrets.token_urlsafe(32)
        self.tokens[token] = _Grant(user_id, time.monotonic() + TOKEN_TTL)
        return token

    def redeem(self, token: str) -> str | None:
        self._prune()
        grant = self.tokens.pop(token, None)
        if grant is None:
            return None
        self._cap(self.sessions, MAX_SESSIONS)
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = _Grant(grant.user_id, time.monotonic() + SESSION_TTL)
        return sid

    def user_for(self, sid: str | None) -> str | None:
        if not sid:
            return None
        self._prune()
        grant = self.sessions.get(sid)
        return grant.user_id if grant else None


def _same_origin(request: web.Request) -> bool:
    """A state-changing request must come from the panel itself.

    The session cookie is SameSite=Strict already; this also refuses requests
    a browser marks as cross-site, and any whose Origin is another site.
    """
    if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
        return False
    origin = request.headers.get("Origin")
    return not origin or origin == f"{request.scheme}://{request.host}"


def _sessions(hass: HomeAssistant) -> PanelSessions:
    return hass.data.setdefault(f"{DOMAIN}_panel_sessions", PanelSessions())


@websocket_api.websocket_command({vol.Required("type"): "home_inventory/panel_url"})
@callback
def ws_panel_url(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if not _client(hass):
        connection.send_error(msg["id"], "not_ready", "Home Inventory is not connected")
        return
    token = _sessions(hass).issue_token(connection.user.id)
    connection.send_result(msg["id"], {"url": f"{PANEL_PROXY_PREFIX}/_auth?t={token}"})


def _client(hass: HomeAssistant):
    for runtime in (hass.data.get(DOMAIN) or {}).values():
        return runtime.client
    return None


class PanelProxyView(HomeAssistantView):
    """Authenticated (via panel session) reverse proxy to the App UI/API."""

    url = PANEL_PROXY_PREFIX + "/{path:.*}"
    name = "api:home_inventory:panel"
    requires_auth = False  # authenticated with the panel session cookie instead

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def _handle(self, request: web.Request, path: str) -> web.StreamResponse:
        sessions = _sessions(self.hass)
        if path == "_auth":
            sid = sessions.redeem(request.query.get("t", ""))
            if sid is None:
                return web.Response(status=403, text="This link has expired. Reopen Home Inventory from the sidebar.")
            resp = web.HTTPFound(PANEL_PROXY_PREFIX + "/")
            resp.set_cookie(COOKIE, sid, path=PANEL_PROXY_PREFIX + "/", httponly=True, samesite="Strict",
                            secure=request.secure, max_age=SESSION_TTL)
            return resp

        user_id = sessions.user_for(request.cookies.get(COOKIE))
        user = await self.hass.auth.async_get_user(user_id) if user_id else None
        if user is None or not user.is_active:
            return web.Response(status=401, text="Session expired. Reopen Home Inventory from the sidebar.")
        if request.method not in ("GET", "HEAD") and not _same_origin(request):
            return web.Response(status=403, text="Cross-origin request refused")
        client = _client(self.hass)
        if client is None:
            return web.Response(status=503, text="Home Inventory is not connected")

        headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_REQUEST_HEADERS}
        headers.update(user_headers(user))
        target = path + (f"?{request.query_string}" if request.query_string else "")
        body = request.content if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.can_read_body else None
        try:
            upstream = await client.raw(request.method, target, headers, body)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Panel proxy error: %s", err)
            return web.Response(status=502, text="Home Inventory is not reachable")
        try:
            resp = web.StreamResponse(status=upstream.status)
            for k, v in upstream.headers.items():
                if k.lower() not in HOP_HEADERS:
                    resp.headers[k] = v
            await resp.prepare(request)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
            return resp
        finally:
            upstream.release()

    async def get(self, request: web.Request, path: str) -> web.StreamResponse:
        return await self._handle(request, path)

    async def post(self, request: web.Request, path: str) -> web.StreamResponse:
        return await self._handle(request, path)

    async def put(self, request: web.Request, path: str) -> web.StreamResponse:
        return await self._handle(request, path)

    async def patch(self, request: web.Request, path: str) -> web.StreamResponse:
        return await self._handle(request, path)

    async def delete(self, request: web.Request, path: str) -> web.StreamResponse:
        return await self._handle(request, path)


async def async_setup_panel_infra(hass: HomeAssistant) -> None:
    """Register the proxy view, static panel script and WS command (once)."""
    key = f"{DOMAIN}_panel_infra"
    if hass.data.get(key):
        return
    hass.data[key] = True
    hass.http.register_view(PanelProxyView(hass))
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "www"), cache_headers=False)]
    )
    websocket_api.async_register_command(hass, ws_panel_url)


async def async_register_panel(hass: HomeAssistant) -> None:
    await async_setup_panel_infra(hass)
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="home-inventory-panel",
        frontend_url_path=PANEL_URL_PATH,
        module_url=f"{STATIC_URL}/panel.js",
        sidebar_title="Inventory",
        sidebar_icon="mdi:package-variant-closed",
        require_admin=False,
        embed_iframe=False,
        config={},
    )


@callback
def async_remove_panel(hass: HomeAssistant) -> None:
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
