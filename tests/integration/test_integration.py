"""Companion integration against a real App process."""

from __future__ import annotations

import asyncio
from http import HTTPStatus

import pytest
from homeassistant import config_entries
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.home_inventory.const import CONF_PANEL, DOMAIN
from custom_components.home_inventory.diagnostics import async_get_config_entry_diagnostics


async def _setup_via_user_flow(hass: HomeAssistant, app, panel: bool = False):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": app.api_url, "api_token": app.token, "sidebar_panel": panel}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return result["result"]


async def test_user_flow_invalid_token(hass: HomeAssistant, inventory_app):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": inventory_app.api_url, "api_token": "nope"})
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": "http://127.0.0.1:1", "api_token": "nope"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_hassio_discovery(hass: HomeAssistant, inventory_app):
    info = HassioServiceInfo(
        config={"host": "127.0.0.1", "port": inventory_app.api_port, "api_token": inventory_app.token},
        name="Home Inventory",
        slug="local_home_inventory",
        uuid="1234",
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=info)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "hassio_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options[CONF_PANEL] is False  # Ingress provides the sidebar entry
    await hass.async_block_till_done()
    # Re-discovery (e.g. the App's hostname or token changed) updates the entry.
    info2 = HassioServiceInfo(config={**info.config, "host": "localhost"}, name="x", slug="x", uuid="1234")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=info2)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    await hass.async_block_till_done()
    assert entry.data["url"] == f"http://localhost:{inventory_app.api_port}"
    # (the test socket guard only allows 127.0.0.1, so the reload itself may retry here)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_sensors(hass: HomeAssistant, inventory_app):
    entry = await _setup_via_user_flow(hass, inventory_app)
    reg = er.async_get(hass)
    ids = {e.translation_key: e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    assert hass.states.get(ids["total_items"]).state == str(inventory_app.ui("GET", "stats")["total_items"])
    assert hass.states.get(ids["containers"]).state == "1"
    value = hass.states.get(ids["total_value"])
    assert float(value.state) == 80.0 and value.attributes["unit_of_measurement"] == "USD"
    assert hass.states.get(ids["app_available"]).state == "on"
    # Only summary entities: never one per inventory item
    assert len(ids) == 16  # 15 sensors + availability
    assert hass.states.get(ids["maintenance_overdue"]).state == "0"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_services(hass: HomeAssistant, inventory_app, hass_admin_user):
    entry = await _setup_via_user_flow(hass, inventory_app)
    ctx = Context(user_id=hass_admin_user.id)
    r = await hass.services.async_call(DOMAIN, "search", {"query": "where are my axle stands"}, blocking=True, return_response=True, context=ctx)
    assert r["total_quantity"] == 2 and r["items"][0]["location"] == "Garage"
    r = await hass.services.async_call(DOMAIN, "get_container_contents", {"container": "15"}, blocking=True, return_response=True)
    assert r["items"][0]["name"] == "Extension cord" and r["location"] == "Shed"
    cord_id = r["items"][0]["item_id"]
    r = await hass.services.async_call(DOMAIN, "move_item", {"item_id": cord_id, "location": "Garage", "quantity": 1}, blocking=True, return_response=True, context=ctx)
    assert r["location"] == "Garage" and r["quantity"] == 1
    r = await hass.services.async_call(DOMAIN, "adjust_quantity", {"item_id": cord_id, "delta": -1}, blocking=True, return_response=True, context=ctx)
    assert r["quantity"] == 1
    r = await hass.services.async_call(DOMAIN, "get_item_location", {"name": "extension cord"}, blocking=True, return_response=True)
    assert sorted(i["location"] for i in r["items"]) == ["Garage", "Shed › Box #15"]
    r = await hass.services.async_call(DOMAIN, "move_container", {"container": "15", "location": "Garage", "temporary": True}, blocking=True, return_response=True)
    assert r["location"] == "Garage › Box #15"
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "get_container_contents", {"container": "does-not-exist"}, blocking=True, return_response=True)
    # The HA user who called the action is recorded in the App's history
    hist = inventory_app.ui("GET", f"history?entity_type=item&entity_id={cord_id}")
    assert hist["events"][0]["user"] == hass_admin_user.name
    await hass.services.async_call(DOMAIN, "move_container", {"container": "15", "location": "Shed"}, blocking=True)
    await hass.config_entries.async_unload(entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "search")


async def test_events_fired(hass: HomeAssistant, inventory_app):
    entry = await _setup_via_user_flow(hass, inventory_app)
    added = async_capture_events(hass, f"{DOMAIN}_item_added")
    moved = async_capture_events(hass, f"{DOMAIN}_container_moved")
    for _ in range(50):  # wait for the event stream to connect
        if hass.data[DOMAIN][entry.entry_id].coordinator.stream_connected:
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.3)
    await hass.async_add_executor_job(inventory_app.ui, "POST", "items", {"name": "Garden hose", "location_id": inventory_app.seed["shed"]["id"]})
    box = inventory_app.seed["box"]
    await hass.async_add_executor_job(inventory_app.ui, "POST", f"containers/{box['id']}/move", {"location_id": inventory_app.seed["shed"]["id"]})
    for _ in range(50):
        if added and moved:
            break
        await asyncio.sleep(0.1)
    assert added[0].data["name"] == "Garden hose" and added[0].data["user"] == "Pat UI"
    assert moved[0].data["to"] == "Shed" and moved[0].data["number"] == "15"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_sidebar_panel_proxy(hass: HomeAssistant, inventory_app, hass_ws_client, hass_client_no_auth, hass_admin_user):
    entry = await _setup_via_user_flow(hass, inventory_app, panel=True)
    assert "home-inventory" in hass.data["frontend_panels"]
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "home_inventory/panel_url"})
    msg = await ws.receive_json()
    assert msg["success"]
    url = msg["result"]["url"]
    client = await hass_client_no_auth()
    # No session: refused
    r = await client.get("/api/home_inventory/panel/api/v1/me")
    assert r.status == HTTPStatus.UNAUTHORIZED
    r = await client.get(url, allow_redirects=False)
    assert r.status == HTTPStatus.FOUND
    cookie = r.cookies["hinv_panel"]
    assert cookie["httponly"] and cookie["samesite"] == "Strict"
    # Single-use token
    r2 = await client.get(url, allow_redirects=False)
    assert r2.status == HTTPStatus.FORBIDDEN
    r = await client.get("/api/home_inventory/panel/api/v1/me")
    assert r.status == HTTPStatus.OK
    me = await r.json()
    assert me["name"] == hass_admin_user.name and me["role"] == "admin"
    r = await client.get("/api/home_inventory/panel/")
    assert r.status == HTTPStatus.OK and "<title>Home Inventory</title>" in await r.text()
    # Writes go through with the user's identity; cross-origin writes refused
    r = await client.post("/api/home_inventory/panel/api/v1/locations", json={"name": "Loft"})
    assert r.status == HTTPStatus.CREATED
    r = await client.post("/api/home_inventory/panel/api/v1/locations", json={"name": "Evil"}, headers={"Origin": "https://evil.example"})
    assert r.status == HTTPStatus.FORBIDDEN
    # Deactivated HA user loses access immediately
    await hass.auth.async_update_user(hass_admin_user, is_active=False)
    r = await client.get("/api/home_inventory/panel/api/v1/me")
    assert r.status == HTTPStatus.UNAUTHORIZED
    await hass.auth.async_update_user(hass_admin_user, is_active=True)
    await hass.config_entries.async_unload(entry.entry_id)
    assert "home-inventory" not in hass.data["frontend_panels"]


async def test_diagnostics_redacts_token(hass: HomeAssistant, inventory_app):
    entry = await _setup_via_user_flow(hass, inventory_app)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["api_token"] == "**REDACTED**"
    assert inventory_app.token not in str(diag)
    assert diag["app_health"]["status"] == "ok"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_remove_entry_keeps_inventory(hass: HomeAssistant, inventory_app):
    entry = await _setup_via_user_flow(hass, inventory_app)
    before = inventory_app.ui("GET", "stats")["total_items"]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert inventory_app.ui("GET", "stats")["total_items"] == before


async def test_app_unavailable_marks_entry_not_ready(hass: HomeAssistant, inventory_app):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, data={"url": "http://127.0.0.1:1", "api_token": inventory_app.token}, options={})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
