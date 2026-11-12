"""Contract test: Home Assistant's media API behaves the way the App relies on.

The App hands photos to AI Task by uploading them with
``/api/media_source/local_source/upload`` and deleting them with the
``media_source/local_source/remove`` WebSocket command (it no longer mounts
``/media``).  This runs both calls against a real Home Assistant.
"""

from __future__ import annotations

import aiohttp
from homeassistant.setup import async_setup_component

FOLDER = "media-source://media_source/local/home_inventory/.ai-tmp"


async def test_upload_creates_folder_and_remove_deletes(hass, hass_client, hass_ws_client, tmp_path):
    hass.config.media_dirs = {"local": str(tmp_path)}
    assert await async_setup_component(hass, "media_source", {})
    client = await hass_client()

    form = aiohttp.FormData()
    form.add_field("media_content_id", FOLDER)
    form.add_field("file", b"\xff\xd8\xff\xe0jpeg", filename="abc123.jpg", content_type="image/jpeg")
    r = await client.post("/api/media_source/local_source/upload", data=form)
    assert r.status == 200, await r.text()
    media_id = (await r.json())["media_content_id"]
    assert media_id == f"{FOLDER}/abc123.jpg"
    stored = tmp_path / "home_inventory" / ".ai-tmp" / "abc123.jpg"
    assert stored.read_bytes().startswith(b"\xff\xd8")  # missing folders were created

    ws = await hass_ws_client()
    await ws.send_json({"id": 1, "type": "media_source/local_source/remove", "media_content_id": media_id})
    reply = await ws.receive_json()
    assert reply["success"], reply
    assert not stored.exists()


async def test_upload_requires_admin(hass, hass_client, hass_admin_user, tmp_path):
    hass.config.media_dirs = {"local": str(tmp_path)}
    assert await async_setup_component(hass, "media_source", {})
    hass_admin_user.groups = []  # demote the test user
    client = await hass_client()
    form = aiohttp.FormData()
    form.add_field("media_content_id", FOLDER)
    form.add_field("file", b"x", filename="a.jpg", content_type="image/jpeg")
    r = await client.post("/api/media_source/local_source/upload", data=form)
    assert r.status == 401
