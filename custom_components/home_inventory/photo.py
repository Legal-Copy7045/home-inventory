"""Item photos for voice-answer notifications ("show me").

``/api/home_inventory/photo/<attachment id>`` needs a Home Assistant login,
which the companion app sends for images in notifications. The photo is
fetched from the App as that Home Assistant user, so the App's own rules on
who may see what still apply.
"""

from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import InventoryError
from .const import DOMAIN

PHOTO_URL = "/api/home_inventory/photo"


class PhotoView(HomeAssistantView):
    url = PHOTO_URL + "/{attachment_id}"
    name = "api:home_inventory:photo"
    requires_auth = True

    async def get(self, request: web.Request, attachment_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        if not attachment_id.isdigit():
            return web.Response(status=404)
        entries = hass.data.get(DOMAIN) or {}
        if not entries:
            return web.Response(status=503)
        client = next(iter(entries.values())).client
        try:
            data, ctype = await client.fetch_bytes(f"attachments/{int(attachment_id)}/file", user=request.get("hass_user"))
        except InventoryError:
            return web.Response(status=404)
        if not ctype.startswith("image/"):
            return web.Response(status=404)
        return web.Response(body=data, content_type=ctype.split(";")[0],
                            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


def async_register_photo_view(hass: HomeAssistant) -> None:
    key = f"{DOMAIN}_photo_view"
    if not hass.data.get(key):
        hass.http.register_view(PhotoView())
        hass.data[key] = True
