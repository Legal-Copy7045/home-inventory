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
    assert len(ids) == 18  # 17 sensors + availability
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
    # A request the browser marks as cross-site is refused even without an Origin header
    r = await client.post("/api/home_inventory/panel/api/v1/locations", json={"name": "Evil 2"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status == 403
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


async def test_voice_where_is_intent(hass: HomeAssistant, inventory_app, hass_admin_user):
    """Assist / Siri (via Assist) / Alexa custom skill all end up in this intent."""
    from homeassistant.helpers import intent

    entry = await _setup_via_user_flow(hass, inventory_app)
    ctx = Context(user_id=hass_admin_user.id)
    resp = await intent.async_handle(hass, "test", "HomeInventoryWhereIs", {"item": {"value": "axle stands?"}}, context=ctx)
    speech = resp.speech["plain"]["speech"]
    assert speech.startswith("Axle stand is in Garage"), speech
    assert " › " not in speech  # read aloud naturally
    resp = await intent.async_handle(hass, "test", "HomeInventoryWhereIs", {"item": {"value": "unicorn saddle"}})
    assert resp.speech["plain"]["speech"] == "I couldn't find unicorn saddle in your inventory."
    await hass.config_entries.async_unload(entry.entry_id)
    if hasattr(intent, "async_remove"):
        with pytest.raises(intent.UnknownIntent):
            await intent.async_handle(hass, "test", "HomeInventoryWhereIs", {"item": {"value": "axle stands"}})


def test_is_or_are_follows_the_name():
    from custom_components.home_inventory.intent import spoken_answer, verb_for

    are = ["Wood screws", "Axle stands", "Pliers", "Safety glasses", "Nuts and bolts", "Drill bits 10pk",
           "AA batteries", "Christmas lights", "Hoses"]
    is_ = ["Axle stand", "Kink Control Plus Water Hose 50ft", "Siphon pump with hose and funnel", "Glass",
           "Box of screws", "Set of drill bits", "Hedge trimmer", "Grass seed", "Bus pass", "Compass", "Cactus"]
    assert {n: verb_for(n) for n in are} == dict.fromkeys(are, "are")
    assert {n: verb_for(n) for n in is_} == dict.fromkeys(is_, "is")
    screws = {"display_name": "Wood screws", "status": "stored", "placement": {"path": "Basement › Server Shelf Left › Top Shelf"}}
    assert spoken_answer("screws", [screws])[0] == "Wood screws are in Basement, Server Shelf Left, Top Shelf."
    lent = {"display_name": "Pliers", "status": "on_loan", "loan": {"borrower": "Sam"}, "placement": {"path": "Garage"}}
    assert spoken_answer("pliers", [lent])[0] == "Pliers are lent to Sam."


def test_spoken_answer_wording():
    from custom_components.home_inventory.intent import pick, spoken_answer

    item = lambda name, path, **kw: {"display_name": name, "placement": {"path": path}, "status": kw.pop("status", "stored"), **kw}  # noqa: E731
    assert spoken_answer("drill", [item("Drill", "Shed › Box #3")]) == ("Drill is in Shed, Box #3.", None)
    assert spoken_answer("ladder", [item("Ladder", "Garage", status="on_loan", loan={"borrower": "Sam"})])[0] == "Ladder is lent to Sam."
    moved = item("Saw", "Garage", status="temporarily_relocated", home_placement={"path": "Shed"})
    assert spoken_answer("saw", [moved])[0] == "Saw is in Garage for now, normally Shed."
    # Same thing in several places: one answer, not a question
    same = [item("Battery", f"Shed › Bin {i}") for i in range(5)]
    text, choices = spoken_answer("battery", same)
    assert choices is None and text == "Battery is in Shed, Bin 0, and in Shed, Bin 1, and in Shed, Bin 2, and 2 more places."
    # Different things: ask which
    hoses = [item("Kink Control Plus Water Hose 50ft", "Shed › Rear Right Shelving"),
             item("Siphon pump hose", "Shed › Small Engine Maintenance #2"),
             item("Siphon pump with hose and funnel", "Shed › Small Engine Maintenance #2")]
    text, choices = spoken_answer("hose", hoses)
    assert text == ("I found 3 things for hose: Kink Control Plus Water Hose 50ft, Siphon pump hose and "
                    "Siphon pump with hose and funnel. Which one did you mean?")
    assert pick("the water hose one", choices)["name"].startswith("Kink")
    assert pick("the kink control one", choices)["name"].startswith("Kink")
    assert pick("the second one", choices)["name"] == "Siphon pump hose"
    assert pick("the last one", choices)["name"] == "Siphon pump with hose and funnel"
    assert pick("the one with the funnel", choices)["name"] == "Siphon pump with hose and funnel"
    assert pick("the lawn mower", choices) is None
    # An exact name among several answers directly
    assert spoken_answer("siphon pump hose", hoses)[1] is None
    # In a box: the box first, then where the box is
    def boxed(name, *boxes, rooms=("Shed", "Rear Right Shelving", "Middle Shelf")):
        return {"display_name": name, "status": "stored", "placement": {
            "path": "ignored", "container_path": list(boxes), "location_path": [{"name": r} for r in rooms]}}
    tote = {"name": "Hoses", "number": "1", "type": "Plastic tote"}
    assert spoken_answer("hose", [boxed("Water hose", tote)])[0] == (
        "Water hose is in Plastic tote number 1, labelled Hoses, located in Shed, Rear Right Shelving, Middle Shelf.")
    assert spoken_answer("hose", [boxed("Water hose", {"name": "Hoses", "number": "1"})])[0] == (
        "Water hose is in Hoses number 1, located in Shed, Rear Right Shelving, Middle Shelf.")  # older App: no type
    tin = {"name": "Fittings tin", "number": None, "type": "Tin"}  # type already in the name
    assert spoken_answer("washer", [boxed("Washer", tote, tin)])[0] == (
        "Washer is in Fittings tin, inside Plastic tote number 1, labelled Hoses, located in Shed, Rear Right Shelving, Middle Shelf.")
    assert spoken_answer("x", [boxed("X", {"name": "Loft box", "number": "4", "type": "Crate"}, rooms=())])[0] == (
        "X is in Crate number 4, labelled Loft box.")
    many = [item(f"Thing {c}", "Shed") for c in "abcdefgh"]
    text, choices = spoken_answer("thing", many)
    assert text.startswith("I found 8 things for thing, including Thing a, Thing b, Thing c and Thing d.") and len(choices) == 6


def test_where_does_it_go_wording():
    from custom_components.home_inventory.intent import spoken_answer

    item = lambda name, path, **kw: {"display_name": name, "placement": {"path": path}, "status": kw.pop("status", "stored"), **kw}  # noqa: E731
    goes = lambda q, rows: spoken_answer(q, rows, goes=True)[0]  # noqa: E731
    assert goes("hammer", [item("Hammer", "Garage › Tool wall")]) == "Hammer goes in Garage, Tool wall."
    assert goes("stands", [item("Axle stands", "Garage")]) == "Axle stands go in Garage."
    # Away from home: where it belongs, then where it is now
    assert goes("saw", [item("Saw", "Kitchen", status="temporarily_relocated", home_placement={"path": "Shed"})]) == (
        "Saw goes in Shed. It's in Kitchen for now.")
    assert goes("drill", [item("Drill", "Shed", status="in_use", status_note="Taken by Sam")]) == "Drill goes in Shed. Sam has it at the moment."
    assert goes("ladder", [item("Ladder", "Garage", status="on_loan", loan={"borrower": "Pat"})]) == (
        "Ladder goes in Garage. It's lent to Pat at the moment.")
    # Kept in two places
    assert goes("oil", [item("Engine oil", "Garage › Shelf 1"), item("Engine oil", "Shed")]) == (
        "Engine oil goes in Garage, Shelf 1, or in Shed.")
    assert goes("unicorn", []) == "I couldn't find unicorn in your inventory."


async def test_voice_where_does_it_go(hass: HomeAssistant, inventory_app):
    from homeassistant.helpers import intent

    shed = inventory_app.seed["shed"]
    for name in ("Claw hammer", "Lump hammer"):
        inventory_app.ui("POST", "items", {"name": name, "location_id": shed["id"]})
    await _setup_via_user_flow(hass, inventory_app)
    r = await intent.async_handle(hass, "conversation", "HomeInventoryWhereDoesItGo", {"item": {"value": "axle stand"}})
    assert r.speech["plain"]["speech"] == "Axle stand goes in Garage."
    ask = await intent.async_handle(hass, "conversation", "HomeInventoryWhereDoesItGo", {"item": {"value": "hammer"}}, device_id="hall")
    assert ask.speech["plain"]["speech"].endswith("Which one did you mean?")
    pick = await intent.async_handle(hass, "conversation", "HomeInventoryChoose", {"choice": {"value": "the lump one"}}, device_id="hall")
    assert pick.speech["plain"]["speech"] == "Lump hammer goes in Shed."  # answered as "goes", not "is"


async def test_voice_move_clear_place_and_box_number(hass: HomeAssistant, inventory_app):
    from homeassistant.helpers import intent

    app = inventory_app
    shed, garage, box = app.seed["shed"], app.seed["garage"], app.seed["box"]
    sem = app.ui("POST", "locations", {"name": "Small Engine Repair", "parent_id": shed["id"]})
    oil = app.ui("POST", "items", {"name": "Engine oil", "location_id": garage["id"]})["items"][0]
    await _setup_via_user_flow(hass, app)

    async def say(intent_name, slots, device="garage-echo"):
        r = await intent.async_handle(hass, "alexa", intent_name, {k: {"value": v} for k, v in slots.items()}, device_id=device)
        return r.speech["plain"]["speech"]

    assert await say("HomeInventoryMove", {"item": "engine oil", "place": "small engine repair"}) == (
        "Done. I've moved Engine oil to Small Engine Repair, in Shed.")
    assert app.ui("GET", f"items/{oil['id']}")["placement"]["location"]["id"] == sem["id"]
    assert await say("HomeInventoryMove", {"item": "the engine oil", "place": "container number 15"}) == (
        "Done. I've moved Engine oil to Container number 15, Box, in Shed.")
    assert app.ui("GET", f"items/{oil['id']}")["placement"]["container"]["id"] == box["id"]
    assert (await say("HomeInventoryMove", {"item": "engine oil", "place": "box 15"})).startswith("Engine oil is already in")
    assert (await say("HomeInventoryMove", {"item": "unicorn saddle", "place": "shed"})).startswith("I couldn't find unicorn saddle")
    assert (await say("HomeInventoryMove", {"item": "engine oil", "place": "the moon"})) == (
        "I couldn't find a place called the moon, so I haven't moved Engine oil.")


async def test_voice_answer_notification_shows_the_photo(hass: HomeAssistant, inventory_app, hass_client):
    from homeassistant.helpers import intent
    from pytest_homeassistant_custom_component.common import async_mock_service

    sent = async_mock_service(hass, "notify", "mobile_app_photo_phone")
    item = inventory_app.ui("POST", "items", {"name": "Photographed bench vice", "location_id": inventory_app.seed["shed"]["id"]})["items"][0]
    att = inventory_app.upload_photo(item["id"])
    entry = await _setup_via_user_flow(hass, inventory_app)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(flow["flow_id"], {"voice_notify": "all", "notify_service": "notify.mobile_app_photo_phone"})
    await hass.async_block_till_done()
    await intent.async_handle(hass, "conversation", "HomeInventoryWhereIs", {"item": {"value": "photographed bench vice"}})
    await hass.async_block_till_done()
    data = sent[-1].data["data"]
    assert data["image"] == f"/api/home_inventory/photo/{att['id']}"
    # The image address needs a Home Assistant login and serves the photo
    client = await hass_client()
    r = await client.get(data["image"])
    assert r.status == 200 and r.headers["Content-Type"] == "image/jpeg" and len(await r.read()) > 100
    assert (await client.get("/api/home_inventory/photo/abc")).status == 404


async def test_voice_whats_in_a_box_or_on_a_shelf(hass: HomeAssistant, inventory_app):
    from homeassistant.helpers import intent

    app = inventory_app
    shed = app.seed["shed"]
    tray = app.ui("POST", "containers", {"name": "Odds tray", "number": "77", "location_id": shed["id"]})
    for name, qty in (("Wall plugs", 40), ("Spirit level", 1)):
        app.ui("POST", "items", {"name": name, "quantity": qty, "container_id": tray["id"]})
    await _setup_via_user_flow(hass, app)
    r = await intent.async_handle(hass, "conversation", "HomeInventoryWhatsIn", {"place": {"value": "box 77"}})
    speech = r.speech["plain"]["speech"]
    assert speech.startswith("In Container number 77, Odds tray") and "40 Wall plugs" in speech and "Spirit level" in speech
    r = await intent.async_handle(hass, "conversation", "HomeInventoryWhatsIn", {"place": {"value": "the moon"}})
    assert r.speech["plain"]["speech"] == "I couldn't find a place called the moon."


async def test_voice_questions_are_kept_in_voice_history(hass: HomeAssistant, inventory_app):
    import json

    from homeassistant.helpers import intent

    await _setup_via_user_flow(hass, inventory_app)
    await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "flux capacitor"}}, device_id="hall-echo")
    import asyncio

    path, last = inventory_app.data_dir / "voice_history.json", None
    for _ in range(50):  # written in the background, after the answer
        await hass.async_block_till_done()
        mine = [e for e in json.loads(path.read_text()) if e.get("device") == "hall-echo"] if path.exists() else []
        if mine:
            last = mine[-1]
            break
        await asyncio.sleep(0.1)
    assert last is not None
    assert last["source"] == "alexa" and last["device"] == "hall-echo" and last["intent"] == "HomeInventoryWhereIs"
    assert last["slots"] == {"item": "flux capacitor"} and last["answer"] == "I couldn't find flux capacitor in your inventory."
    assert last["question"] is False


async def test_voice_move_asks_when_unclear(hass: HomeAssistant, inventory_app):
    from homeassistant.helpers import intent

    app = inventory_app
    shed, garage = app.seed["shed"], app.seed["garage"]
    top_shed = app.ui("POST", "locations", {"name": "Top rack", "parent_id": shed["id"]})
    top_garage = app.ui("POST", "locations", {"name": "Top rack", "parent_id": garage["id"]})
    lathe = app.ui("POST", "locations", {"name": "Lathe corner", "parent_id": shed["id"]})
    red = app.ui("POST", "items", {"name": "Red tin snips", "location_id": garage["id"]})["items"][0]
    blue = app.ui("POST", "items", {"name": "Blue tin snips", "location_id": garage["id"]})["items"][0]
    await _setup_via_user_flow(hass, app)

    async def say(intent_name, slots, device="hall"):
        r = await intent.async_handle(hass, "conversation", intent_name, {k: {"value": v} for k, v in slots.items()}, device_id=device)
        return r.speech["plain"]["speech"]

    # Which item, then which place
    assert (await say("HomeInventoryMove", {"item": "tin snips", "place": "top rack"})).startswith("Which one did you move:")
    ask = await say("HomeInventoryChoose", {"choice": "the red one"})
    assert ask.startswith("Which place: Top rack, in ") and "in Shed" in ask and "in Garage" in ask
    assert await say("HomeInventoryChoose", {"choice": "the garage one"}) == "Done. I've moved Red tin snips to Top rack, in Garage."
    assert app.ui("GET", f"items/{red['id']}")["placement"]["location"]["id"] == top_garage["id"]
    # A doubtful place: "Did you mean...?" - no cancels, yes moves
    assert await say("HomeInventoryMove", {"item": "blue tin snips", "place": "laith corner"}) == "Did you mean Lathe corner, in Shed?"
    assert await say("HomeInventoryChoose", {"choice": "no"}) == "Okay, I haven't moved anything."
    assert app.ui("GET", f"items/{blue['id']}")["placement"]["location"]["id"] == garage["id"]
    await say("HomeInventoryMove", {"item": "blue tin snips", "place": "laith corner"})
    assert await say("HomeInventoryChoose", {"choice": "yes"}) == "Done. I've moved Blue tin snips to Lathe corner, in Shed."
    assert app.ui("GET", f"items/{blue['id']}")["placement"]["location"]["id"] == lathe["id"]
    # Yes with nothing open
    assert await say("HomeInventoryChoose", {"choice": "yes"}) == "There's nothing to confirm right now."
    assert top_shed["id"] != top_garage["id"]


async def test_voice_asks_which_one_and_understands_the_reply(hass: HomeAssistant, inventory_app):
    from homeassistant.helpers import intent

    shed = inventory_app.seed["shed"]
    for name in ("Kink Control Plus Water Hose 50ft", "Siphon pump hose", "Siphon pump with hose and funnel"):
        inventory_app.ui("POST", "items", {"name": name, "location_id": shed["id"]})
    await _setup_via_user_flow(hass, inventory_app)
    ask = await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "hose"}})
    assert ask.speech["plain"]["speech"].endswith("Which one did you mean?")
    assert "Which one:" in ask.reprompt["plain"]["reprompt"]  # keeps the Alexa session open
    unclear = await intent.async_handle(hass, "alexa", "HomeInventoryChoose", {"choice": {"value": "the lawn mower"}})
    assert unclear.speech["plain"]["speech"].startswith("Sorry, which one:")
    answer = await intent.async_handle(hass, "alexa", "HomeInventoryChoose", {"choice": {"value": "the water hose one"}})
    assert answer.speech["plain"]["speech"] == "Kink Control Plus Water Hose 50ft is in Shed."
    assert not answer.reprompt
    # The question has been answered: a bare name is now a question of its own
    again = await intent.async_handle(hass, "alexa", "HomeInventoryChoose", {"choice": {"value": "siphon pump hose"}})
    assert again.speech["plain"]["speech"] == "Siphon pump hose is in Shed."
    # Questions are per device: an Assist satellite's reply doesn't answer Alexa's question
    q = await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "hose"}})
    listed = q.speech["plain"]["speech"].split(": ", 1)[1].removesuffix(". Which one did you mean?")
    second = listed.split(", ")[1].split(" and ")[0]  # order as spoken
    other = await intent.async_handle(hass, "conversation", "HomeInventoryChoose", {"choice": {"value": "the second one"}}, device_id="kitchen")
    assert other.speech["plain"]["speech"].startswith("I couldn't find")  # not Alexa's question
    mine = await intent.async_handle(hass, "alexa", "HomeInventoryChoose", {"choice": {"value": "the second one"}})
    assert mine.speech["plain"]["speech"] == f"{second} is in Shed."


async def test_zero_turn_said_as_digits_and_bare_answers(hass: HomeAssistant, inventory_app):
    """Alexa writes "zero" as "0"; replies to "which one" are often just a name."""
    from homeassistant.helpers import intent

    shed = inventory_app.seed["shed"]
    for name in ("Zero-turn lawn mower", "Toro Lawn Mower", "Lawn mower blade bracket"):
        inventory_app.ui("POST", "items", {"name": name, "location_id": shed["id"]})
    await _setup_via_user_flow(hass, inventory_app)
    direct = await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "0 turn mower"}})
    assert direct.speech["plain"]["speech"] == "Zero-turn lawn mower is in Shed."
    ask = await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "lawn mower"}})
    assert ask.speech["plain"]["speech"].endswith("Which one did you mean?")
    pick_it = await intent.async_handle(hass, "alexa", "HomeInventoryChoose", {"choice": {"value": "0 turn"}})
    assert pick_it.speech["plain"]["speech"] == "Zero-turn lawn mower is in Shed."


async def test_alexa_custom_skill_request_is_answered(hass: HomeAssistant, inventory_app):
    """Alexa custom-skill requests reach the intent.

    Home Assistant's Alexa integration (homeassistant/components/alexa/intent.py)
    turns each Alexa slot into ``{name: {"value": v}, name + "_Id": {"value": id}}``
    and calls ``intent.async_handle(hass, "alexa", intent_name, slots)``.  Importing
    that module needs camera libraries, so the same call is made directly here.
    """
    from homeassistant.helpers import intent

    await _setup_via_user_flow(hass, inventory_app)
    resp = await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs",
                                     {"item": {"value": "axle stand"}, "item_Id": {"value": None}})
    assert resp.speech["plain"]["speech"].startswith("Axle stand is in Garage")


async def test_voice_answers_sent_as_notification(hass: HomeAssistant, inventory_app):
    from homeassistant.helpers import intent
    from pytest_homeassistant_custom_component.common import async_mock_service

    from custom_components.home_inventory.const import CONF_NOTIFY_SERVICE, CONF_VOICE_NOTIFY

    sent = async_mock_service(hass, "notify", "mobile_app_phone")
    shed = inventory_app.seed["shed"]
    for name in ("Notify test rake", "Notify test leaf rake"):
        inventory_app.ui("POST", "items", {"name": name, "location_id": shed["id"]})
    entry = await _setup_via_user_flow(hass, inventory_app)

    async def options(**values):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        return await hass.config_entries.options.async_configure(flow["flow_id"], values)

    # Off by default
    await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "axle stand"}})
    await hass.async_block_till_done()
    assert sent == []
    # Turning it on needs a notify action that exists
    assert (await options(voice_notify="alexa"))["errors"] == {CONF_NOTIFY_SERVICE: "notify_service_required"}
    assert (await options(voice_notify="alexa", notify_service="nope"))["errors"] == {CONF_NOTIFY_SERVICE: "notify_service_unknown"}
    result = await options(voice_notify="alexa", notify_service="notify.mobile_app_phone")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_NOTIFY_SERVICE] == "mobile_app_phone" and entry.options[CONF_VOICE_NOTIFY] == "alexa"

    resp = await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "axle stand"}})
    await hass.async_block_till_done()
    assert len(sent) == 1 and sent[0].data == {"title": "Home Inventory", "message": resp.speech["plain"]["speech"]}
    # Questions and "couldn't find" aren't sent; the chosen answer is
    await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "unicorn saddle"}})
    await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "notify test"}})
    await hass.async_block_till_done()
    assert len(sent) == 1
    await intent.async_handle(hass, "alexa", "HomeInventoryChoose", {"choice": {"value": "the leaf rake"}})
    await hass.async_block_till_done()
    assert len(sent) == 2 and sent[1].data["message"] == "Notify test leaf rake is in Shed."
    # "Alexa only": Assist answers aren't sent; "all": they are
    await intent.async_handle(hass, "conversation", "HomeInventoryWhereIs", {"item": {"value": "axle stand"}})
    await hass.async_block_till_done()
    assert len(sent) == 2
    await options(voice_notify="all", notify_service="mobile_app_phone")
    await hass.async_block_till_done()
    await intent.async_handle(hass, "conversation", "HomeInventoryWhereIs", {"item": {"value": "axle stand"}})
    await hass.async_block_till_done()
    assert len(sent) == 3
    # A notify action that has since gone away doesn't break the spoken answer
    hass.services.async_remove("notify", "mobile_app_phone")
    resp = await intent.async_handle(hass, "alexa", "HomeInventoryWhereIs", {"item": {"value": "axle stand"}})
    assert resp.speech["plain"]["speech"].startswith("Axle stand is in Garage")
    await hass.config_entries.async_unload(entry.entry_id)


async def test_voice_questions_without_slots(hass: HomeAssistant, inventory_app, hass_admin_user):
    from homeassistant.helpers import intent

    entry = await _setup_via_user_flow(hass, inventory_app)
    ctx = Context(user_id=hass_admin_user.id)
    say = lambda r: r.speech["plain"]["speech"]  # noqa: E731
    m = await intent.async_handle(hass, "test", "HomeInventoryMaintenanceDue", {}, context=ctx)
    assert "maintenance" in say(m) or "due" in say(m)
    low = await intent.async_handle(hass, "test", "HomeInventoryRunningLow", {}, context=ctx)
    assert say(low).startswith(("Nothing is running low", "You're running low on"))
    loans = await intent.async_handle(hass, "test", "HomeInventoryOnLoan", {}, context=ctx)
    assert say(loans).startswith(("Nothing is lent out", "1 thing is", "2 things are"))
    await hass.config_entries.async_unload(entry.entry_id)


def test_question_answers_read_naturally():
    from custom_components.home_inventory.intent import loans_answer, low_stock_answer, maintenance_answer

    dash = {"overdue": [{"item": {"name": "Mower"}, "title": "Engine oil change"}],
            "due": [{"item": {"name": "Generator"}, "title": "Exercise run"}]}
    assert maintenance_answer(dash) == ("1 job is overdue: Mower engine oil change. "
                                        "1 job is due in the next two weeks: Generator exercise run.")
    assert maintenance_answer({"overdue": [], "due": []}) == "Nothing is due for maintenance in the next two weeks."
    rows = [{"name": "AA batteries", "quantity": 3, "min_quantity": 4}, {"name": "Bin bags", "quantity": 9, "min_quantity": 2}]
    assert low_stock_answer(rows) == "You're running low on AA batteries (3 left)."
    many = [{"item": {"name": f"Tool {n}"}, "borrower": "Pat"} for n in range(7)]
    assert loans_answer(many).endswith("Tool 4 to Pat and 2 more.")


def test_voice_intents_are_registered_once(hass: HomeAssistant, monkeypatch):
    """A second entry or a reload doesn't register them again ("being overwritten")."""
    from homeassistant.helpers import intent as ha_intent

    from custom_components.home_inventory import intent as hi

    calls = []
    monkeypatch.setattr(ha_intent, "async_register", lambda h, handler: calls.append(handler.intent_type))
    monkeypatch.setattr(ha_intent, "async_remove", lambda h, name: None, raising=False)
    hi.async_setup_intents(hass)
    first = len(calls)
    hi.async_setup_intents(hass)
    assert first == 8 and len(calls) == 8
    hi.async_unload_intents(hass)
    hi.async_setup_intents(hass)  # after unloading, registered afresh
    assert len(calls) == 16
    hi.async_unload_intents(hass)


def test_every_app_event_type_reaches_home_assistant():
    """The integration forwards each event the App can publish."""
    import ast
    from pathlib import Path

    from custom_components.home_inventory.const import EVENT_TYPES

    src = (Path(__file__).parents[2] / "home_inventory" / "server" / "home_inventory_server" / "events.py").read_text()
    tree = ast.parse(src)
    app_types = next(ast.literal_eval(n.value) for n in tree.body
                     if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "EVENT_TYPES")
    assert set(app_types) == set(EVENT_TYPES)
