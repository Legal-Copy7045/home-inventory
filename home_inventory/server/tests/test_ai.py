"""AI identification workflows, failures and provider adapters."""

import json

import httpx
import pytest

from home_inventory_server.ai import AIService
from home_inventory_server.ai.providers import (
    AIProviderError,
    HomeAssistantAITaskProvider,
    OpenAICompatibleProvider,
    build_provider,
)
from tests.conftest import jpeg_bytes, make_settings


def test_identify_item_returns_suggestion_for_review(ai_env, fake_ai):
    u = ai_env.admin
    photo = u.upload()
    fake_ai.responses.append(
        {"name": "Cordless drill", "description": "18V drill", "category": "Tools", "manufacturer": "DeWalt",
         "model_number": None, "confidence": 0.91, "notes": None}
    )
    res = u.ok("post", "/api/v1/ai/identify-item", json={"attachment_id": photo["id"]})
    assert res["suggestion"]["name"] == "Cordless drill"
    assert res["suggestion"]["confidence_label"] == "high"
    assert res["metadata"]["provider"] == "fake"
    # Nothing was saved
    assert u.ok("get", "/api/v1/items")["total"] == 0
    assert fake_ai.calls[0]["image"] is True
    # user confirms (edited)
    item = u.item("Cordless drill driver", manufacturer="DeWalt", photo_ids=[photo["id"]],
                  identification_status="confirmed", ai_metadata=res["metadata"])
    assert item["product"]["identification_status"] == "confirmed"
    hist = u.ok("get", "/api/v1/history", params={"entity_type": "item", "entity_id": item["id"]})["events"]
    assert "ai_identified" in [e["action"] for e in hist]


def test_ai_failure_is_reported_without_data_loss(ai_env, fake_ai):
    u = ai_env.admin
    photo = u.upload()
    fake_ai.responses += [AIProviderError("boom", retryable=True), AIProviderError("boom again", retryable=True)]
    r = u.post("/api/v1/ai/identify-item", json={"attachment_id": photo["id"]})
    assert r.status_code == 502
    # photo still exists and can be used for manual entry
    item = u.item("Something", photo_ids=[photo["id"]])
    assert item["photo"]["id"] == photo["id"]


def test_ai_invalid_json_retried(ai_env, fake_ai):
    u = ai_env.admin
    photo = u.upload()
    fake_ai.responses += ["not json", json.dumps({"name": "Kettle", "confidence": 0.4})]
    res = u.ok("post", "/api/v1/ai/identify-item", json={"attachment_id": photo["id"]})
    assert res["suggestion"]["name"] == "Kettle"
    assert res["suggestion"]["confidence_label"] == "low"


def test_ai_unavailable(env):
    photo = env.admin.upload()
    r = env.admin.post("/api/v1/ai/identify-item", json={"attachment_id": photo["id"]})
    assert r.status_code == 503


def test_container_scan_review_confirm_and_rescan(ai_env, fake_ai):
    u = ai_env.admin
    box = u.container("Garage box", "3")
    fake_ai.responses.append({"items": [
        {"name": "Screwdriver set", "description": None, "category": "Tools", "manufacturer": None, "model_number": None, "quantity": 1, "confidence": 0.9},
        {"name": "Duct tape", "description": None, "category": "Hardware", "manufacturer": None, "model_number": None, "quantity": 2, "confidence": 0.8},
    ], "notes": None})
    scan = u.ok("post", f"/api/v1/containers/{box['id']}/scans", files={"file": ("c.jpg", jpeg_bytes())})
    assert scan["status"] == "ready"
    assert [s["proposed_action"] for s in scan["suggestions"]] == ["add", "add"]
    # Nothing created before confirmation
    assert u.ok("get", f"/api/v1/containers/{box['id']}")["items"] == []
    # user removes the tape suggestion, edits the first, adds one manually
    res = u.ok("post", f"/api/v1/scans/{scan['id']}/confirm", json={"items": [
        {"action": "add", "name": "Screwdriver set (6pc)", "quantity": 1},
        {"action": "add", "name": "Cable ties", "quantity": 50},
    ]})
    assert len(res["created"]) == 2
    contents = u.ok("get", f"/api/v1/containers/{box['id']}")
    assert sorted(i["name"] for i in contents["items"]) == ["Cable ties", "Screwdriver set (6pc)"]
    assert all(i["photo"] for i in contents["items"])  # container photo associated
    assert u.post(f"/api/v1/scans/{scan['id']}/confirm", json={"items": []}).status_code == 409

    # Re-scan: existing items are matched, not duplicated
    fake_ai.responses.append({"items": [
        {"name": "Screwdriver set", "description": None, "category": None, "manufacturer": None, "model_number": None, "quantity": 1, "confidence": 0.9},
        {"name": "Cable tie", "description": None, "category": None, "manufacturer": None, "model_number": None, "quantity": 60, "confidence": 0.7},
    ], "notes": None})
    scan2 = u.ok("post", f"/api/v1/containers/{box['id']}/scans", files={"file": ("c.jpg", jpeg_bytes())})
    s = {x["name"]: x for x in scan2["suggestions"]}
    assert s["Screwdriver set"]["proposed_action"] == "skip"
    assert s["Screwdriver set"]["match"]["name"] == "Screwdriver set (6pc)"
    assert s["Cable tie"]["proposed_action"] == "increase"
    ties_id = s["Cable tie"]["match"]["item_id"]
    u.ok("post", f"/api/v1/scans/{scan2['id']}/confirm", json={"items": [
        {"action": "skip", "name": "Screwdriver set"},
        {"action": "increase", "existing_item_id": ties_id, "quantity": 60},
    ]})
    contents = u.ok("get", f"/api/v1/containers/{box['id']}")
    assert len(contents["items"]) == 2
    assert next(i for i in contents["items"] if i["id"] == ties_id)["quantity"] == 60
    # both container photos retained
    assert len(contents["photos"]) == 2


def test_container_scan_failure_keeps_photo(ai_env, fake_ai):
    u = ai_env.admin
    box = u.container("Box")
    fake_ai.responses += [AIProviderError("down", retryable=False)]
    scan = u.ok("post", f"/api/v1/containers/{box['id']}/scans", files={"file": ("c.jpg", jpeg_bytes())})
    assert scan["status"] == "failed" and "down" in scan["error"]
    assert scan["photo"] is not None
    fake_ai.responses.append({"items": [], "notes": "empty box"})
    retry = u.ok("post", f"/api/v1/scans/{scan['id']}/retry")
    assert retry["status"] == "ready"


def test_ai_image_downscaled(tmp_path, fake_ai):
    s = make_settings(tmp_path, ai_max_image_px=100)
    svc = AIService(s, provider=fake_ai)
    seen = {}

    def gen(prompt, schema, image, timeout):
        import io

        from PIL import Image

        seen["size"] = Image.open(io.BytesIO(image[0])).size
        return {"name": "x", "confidence": 1}

    fake_ai.generate_json = gen
    svc.identify_item(jpeg_bytes(size=(1000, 500)))
    assert max(seen["size"]) == 100


def _ha_fakes(monkeypatch, upload_status=(200,)):
    """Fake Home Assistant: states list, media upload, AI Task and WS remove."""
    from home_inventory_server.ai import providers

    calls = {"uploads": [], "removed": [], "stored": set()}
    statuses = list(upload_status)

    def fake_get(url, headers, timeout):
        return httpx.Response(200, json=[
            {"entity_id": "ai_task.text_only", "attributes": {"supported_features": 1}},
            {"entity_id": "ai_task.google_ai_task", "attributes": {"supported_features": 3}},
        ], request=httpx.Request("GET", url))

    def fake_post(url, json=None, headers=None, timeout=None, data=None, files=None):
        if url.endswith("/media_source/local_source/upload"):
            folder, (name, content, ctype) = data["media_content_id"], files["file"]
            calls["uploads"].append((folder, name, ctype, content))
            status = statuses.pop(0) if statuses else 200
            if status >= 400:
                return httpx.Response(status)
            media_id = f"{folder}/{name}"
            calls["stored"].add(media_id)
            return httpx.Response(200, json={"media_content_id": media_id})
        calls["url"], calls["body"] = url, json
        att = json["attachments"][0]["media_content_id"]
        calls["file_existed"] = att in calls["stored"]
        return httpx.Response(200, json={"service_response": {"data": '```json\n{"name": "Mug", "confidence": 0.7}\n```'}})

    def fake_ws(url, token, message):
        calls["ws_url"] = url
        assert message["type"] == "media_source/local_source/remove"
        calls["removed"].append(message["media_content_id"])
        calls["stored"].discard(message["media_content_id"])

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(providers, "_ws_request", fake_ws)
    return calls


def test_ha_ai_task_provider_uses_media_api_not_a_mount(tmp_path, monkeypatch):
    s = make_settings(tmp_path, ai_provider="home_assistant")
    s.supervisor_token = "tok"
    calls = _ha_fakes(monkeypatch)
    p = build_provider(s)
    assert isinstance(p, HomeAssistantAITaskProvider)
    out = p.generate_json("identify", {"type": "object"}, (b"jpegdata", "image/jpeg"), 10)
    assert json.loads(out)["name"] == "Mug"
    assert calls["body"]["entity_id"] == "ai_task.google_ai_task"
    assert calls["url"] == "http://supervisor/core/api/services/ai_task/generate_data?return_response"
    folder, name, ctype, content = calls["uploads"][0]
    assert folder == "media-source://media_source/local/home_inventory/.ai-tmp"
    assert ctype == "image/jpeg" and content == b"jpegdata" and name.endswith(".jpg")
    assert calls["file_existed"] is True
    assert calls["removed"] == [f"{folder}/{name}"] and not calls["stored"]  # cleaned up
    assert calls["ws_url"] == "ws://supervisor/core/websocket"


def test_ha_ai_task_older_home_assistant_falls_back_to_media_root(tmp_path, monkeypatch):
    s = make_settings(tmp_path, ai_provider="home_assistant")
    s.supervisor_token = "tok"
    calls = _ha_fakes(monkeypatch, upload_status=(400, 200))
    build_provider(s).generate_json("identify", {"type": "object"}, (b"jpegdata", "image/jpeg"), 10)
    assert calls["uploads"][1][0] == "media-source://media_source/local"
    assert calls["uploads"][1][1].startswith("home_inventory_ai_")
    assert not calls["stored"]


def test_ha_ai_task_temp_photo_removed_even_when_ai_fails(tmp_path, monkeypatch):
    s = make_settings(tmp_path, ai_provider="home_assistant")
    s.supervisor_token = "tok"
    calls = _ha_fakes(monkeypatch)
    real_post = httpx.post

    def failing(url, **kw):
        if "generate_data" in url:
            raise httpx.ConnectTimeout("boom")
        return real_post(url, **kw)

    monkeypatch.setattr(httpx, "post", failing)
    with pytest.raises(AIProviderError):
        build_provider(s).generate_json("identify", {"type": "object"}, (b"jpegdata", "image/jpeg"), 10)
    assert calls["uploads"] and not calls["stored"]


def test_app_manifest_does_not_mount_media():
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "config.yaml").read_text()
    assert not re.search(r"^map:", text, re.M), "the App must not mount shared folders"


def test_openai_compatible_errors(monkeypatch):
    p = OpenAICompatibleProvider("openai", "k", "m", "https://example.invalid/v1")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, text="slow down"))
    with pytest.raises(AIProviderError) as e:
        p.generate_json("x", {}, None, 1)
    assert e.value.retryable
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401, text="bad key"))
    with pytest.raises(AIProviderError) as e:
        p.generate_json("x", {}, None, 1)
    assert not e.value.retryable


def test_unknown_provider(tmp_path):
    with pytest.raises(AIProviderError):
        build_provider(make_settings(tmp_path, ai_provider="skynet"))


def test_ai_search_interpretation_uses_db_only(ai_env, fake_ai):
    u = ai_env.admin
    shed = u.location("Shed")
    u.item("Jack stand", quantity=2, location_id=shed["id"])
    fake_ai.responses.append({"intent": "find", "terms": [["axle stand", "jack stand"]], "manufacturer": None, "category": None, "status": None})
    res = u.ok("get", "/api/v1/search", params={"q": "where are my axle stands"})
    assert res["interpretation"]["by"] == "ai:fake"
    assert res["total_quantity"] == 2
    assert "Shed" in res["answer"]


def test_ai_search_failure_falls_back(ai_env, fake_ai):
    u = ai_env.admin
    u.item("Garden hose")
    fake_ai.responses.append(AIProviderError("nope"))
    res = u.ok("get", "/api/v1/search", params={"q": "where is the garden hose"})
    assert res["total_matches"] == 1 and res["interpretation"]["by"] == "rules"
    assert res["ai_error"]


def _shelf_item(name, qty=1, position=None, box=None, **kw):
    return {"name": name, "description": None, "category": kw.get("category"), "manufacturer": kw.get("manufacturer"),
            "model_number": None, "quantity": qty, "confidence": kw.get("confidence", 0.8), "position": position, "box": box}


def test_shelf_photo_inventory_identifies_several_items(ai_env, fake_ai):
    """One photo of a shelf: AI lists everything on it, the user edits, and
    confirmed items become loose items on that shelf."""
    u = ai_env.admin
    garage = u.location("Garage")
    shelf = u.ok("post", "/api/v1/locations", json={"name": "Shelf 2", "parent_id": garage["id"], "kind": "shelf"})
    already = u.ok("post", "/api/v1/items", json={"name": "WD-40", "quantity": 1, "location_id": shelf["id"]})["items"][0]
    fake_ai.responses.append({"items": [
        _shelf_item("Paint tins", 3, "top shelf, left", category="Decorating"),
        _shelf_item("WD-40", 2, "second shelf"),
        _shelf_item("Socket set", 1, "bottom shelf", manufacturer="Halfords", confidence=0.4),
    ], "notes": None})
    scan = u.ok("post", f"/api/v1/locations/{shelf['id']}/scans", files={"file": ("s.jpg", jpeg_bytes())})
    assert scan["status"] == "ready" and scan["location_id"] == shelf["id"] and scan["container_id"] is None
    assert "shelf" in fake_ai.calls[-1]["prompt"].lower() and "Shelf 2 (shelf)" in fake_ai.calls[-1]["prompt"]
    sugg = {s["name"]: s for s in scan["suggestions"]}
    assert sugg["Paint tins"]["position"] == "top shelf, left" and sugg["Paint tins"]["quantity"] == 3
    assert sugg["WD-40"]["match"]["item_id"] == already["id"] and sugg["WD-40"]["proposed_action"] == "increase"
    assert sugg["Socket set"]["proposed_action"] == "add"
    # Nothing saved before confirming
    assert [i["id"] for i in u.ok("get", f"/api/v1/locations/{shelf['id']}")["items"]] == [already["id"]]
    assert [s["id"] for s in u.ok("get", f"/api/v1/locations/{shelf['id']}/scans")] == [scan["id"]]

    # The user corrects names/details, drops nothing, adds one it missed.
    res = u.ok("post", f"/api/v1/scans/{scan['id']}/confirm", json={"items": [
        {"action": "add", "name": "Emulsion paint (white)", "category": "Decorating", "manufacturer": "Dulux", "quantity": 3,
         "notes": "top shelf, left"},
        {"action": "increase", "existing_item_id": already["id"], "quantity": 2},
        {"action": "add", "name": "Socket set 1/2in", "manufacturer": "Halfords", "model_number": "HSS-200", "quantity": 1},
        {"action": "add", "name": "Funnel", "quantity": 1},
    ]})
    assert len(res["created"]) == 3 and res["increased"] == [already["id"]]
    loose = {i["name"]: i for i in u.ok("get", f"/api/v1/locations/{shelf['id']}")["items"]}
    assert set(loose) == {"WD-40", "Emulsion paint (white)", "Socket set 1/2in", "Funnel"}
    assert loose["WD-40"]["quantity"] == 2
    paint = u.ok("get", f"/api/v1/items/{loose['Emulsion paint (white)']['id']}")
    assert paint["manufacturer"] == "Dulux" and paint["notes"] == "top shelf, left" and paint["photo"]
    assert paint["placement"]["location"]["id"] == shelf["id"] and paint["placement"]["container"] is None
    assert u.ok("get", f"/api/v1/items/{loose['Socket set 1/2in']['id']}")["model_number"] == "HSS-200"
    hist = u.ok("get", "/api/v1/history", params={"entity_type": "location", "entity_id": shelf["id"]})["events"]
    assert any(e["action"] == "inventory_updated" and "3 added, 1 updated" in e["summary"] for e in hist)
    assert u.post(f"/api/v1/scans/{scan['id']}/confirm", json={"items": []}).status_code == 409


def test_shelf_rescan_only_touches_that_shelf(ai_env, fake_ai):
    u = ai_env.admin
    shelf, other = u.location("Shelf A"), u.location("Shelf B")
    box = u.container("Bin on shelf A", location_id=shelf["id"])
    in_box = u.ok("post", "/api/v1/items", json={"name": "Hammer", "quantity": 1, "container_id": box["id"]})["items"][0]
    elsewhere = u.ok("post", "/api/v1/items", json={"name": "Saw", "quantity": 1, "location_id": other["id"]})["items"][0]
    gone = u.ok("post", "/api/v1/items", json={"name": "Old glue", "quantity": 1, "location_id": shelf["id"]})["items"][0]
    fake_ai.responses.append({"items": [_shelf_item("Hammer"), _shelf_item("Saw")], "notes": None})
    scan = u.ok("post", f"/api/v1/locations/{shelf['id']}/scans", files={"file": ("s.jpg", jpeg_bytes())})
    # Items in a box on the shelf, or on another shelf, are not "here".
    assert all(s["match"] is None for s in scan["suggestions"])
    for bad in ({"items": [{"action": "increase", "existing_item_id": in_box["id"], "quantity": 3}]},
                {"items": [], "remove_item_ids": [elsewhere["id"]]}):
        assert u.post(f"/api/v1/scans/{scan['id']}/confirm", json=bad).status_code == 409
    assert u.ok("get", f"/api/v1/items/{in_box['id']}")["quantity"] == 1
    # Removing something no longer on the shelf works.
    res = u.ok("post", f"/api/v1/scans/{scan['id']}/confirm", json={"items": [], "remove_item_ids": [gone["id"]]})
    assert res["removed"] == [gone["id"]]
    assert u.ok("get", f"/api/v1/items/{gone['id']}")["deleted_at"]


def test_shelf_scan_roles_and_missing_ai(env, ai_env, fake_ai):
    shelf = env.admin.location("Shelf")
    scan = env.admin.ok("post", f"/api/v1/locations/{shelf['id']}/scans", files={"file": ("s.jpg", jpeg_bytes())})
    assert scan["status"] == "failed" and "not configured" in scan["error"] and scan["photo"]
    assert env.admin.post("/api/v1/locations/999999/scans", files={"file": ("s.jpg", jpeg_bytes())}).status_code == 404
    assert env.admin.post(f"/api/v1/locations/{shelf['id']}/scans").status_code == 422
    viewer = env.user("u-ro", "Viewer")
    viewer.get("/api/v1/me")
    vid = next(x["id"] for x in env.admin.ok("get", "/api/v1/users") if x["name"] == "Viewer")
    env.admin.ok("patch", f"/api/v1/users/{vid}", json={"role": "read_only"})
    assert viewer.post(f"/api/v1/locations/{shelf['id']}/scans", files={"file": ("s.jpg", jpeg_bytes())}).status_code == 403
    assert viewer.post(f"/api/v1/scans/{scan['id']}/confirm", json={"items": []}).status_code == 403
    assert viewer.ok("get", f"/api/v1/locations/{shelf['id']}/scans")[0]["id"] == scan["id"]


def test_scan_matches_do_not_name_customer_equipment_to_read_only(ai_env, fake_ai):
    from tests.test_privacy import _viewer

    u = ai_env.admin
    box = u.container("Bench box")
    drill = u.item("Household drill", container_id=box["id"])
    cust = u.ok("post", "/api/v1/customers", json={"name": "Pat"})
    u.ok("post", "/api/v1/repair-jobs", json={"customer_id": cust["id"], "reported_problem": "Dead",
                                               "new_equipment": {"name": "Pat's chainsaw", "container_id": box["id"]}})
    fake_ai.responses.append({"items": [_shelf_item("Household drill"), _shelf_item("Pat's chainsaw")], "notes": None})
    scan = u.ok("post", f"/api/v1/containers/{box['id']}/scans", files={"file": ("c.jpg", jpeg_bytes())})
    assert all(s["match"] for s in scan["suggestions"])  # members see both matches
    viewer = _viewer(ai_env)
    seen = {s["name"]: s["match"] for s in viewer.ok("get", f"/api/v1/scans/{scan['id']}")["suggestions"]}
    assert seen["Household drill"]["item_id"] == drill["id"]
    assert seen["Pat's chainsaw"] is None


def test_scan_items_get_cropped_photos_and_unseen_can_be_marked_missing(ai_env, fake_ai):
    u = ai_env.admin
    shelf = u.location("Shelf")
    gone = u.item("Old paint", location_id=shelf["id"])
    maybe = u.item("Spirit level", location_id=shelf["id"])
    fake_ai.responses.append({"items": [
        _shelf_item("Drill", box=[0.1, 0.1, 0.4, 0.5]),
        {**_shelf_item("Tape"), "box": [0.5, 0.5, 5, 5]},   # nonsense box: dropped
        _shelf_item("Pliers"),
    ], "notes": None})
    scan = u.ok("post", f"/api/v1/locations/{shelf['id']}/scans", files={"file": ("s.jpg", jpeg_bytes(size=(800, 600)))})
    sugg = {s["name"]: s for s in scan["suggestions"]}
    assert sugg["Drill"]["box"] == [0.1, 0.1, 0.4, 0.5] and "box" not in sugg["Tape"]
    res = u.ok("post", f"/api/v1/scans/{scan['id']}/confirm", json={
        "items": [{"action": "add", "name": "Drill", "box": sugg["Drill"]["box"]}, {"action": "add", "name": "Pliers"}],
        "remove_item_ids": [gone["id"]], "missing_item_ids": [maybe["id"]],
    })
    assert res["removed"] == [gone["id"]] and res["missing"] == [maybe["id"]]
    drill = u.ok("get", f"/api/v1/items/{res['created'][0]}")
    pliers = u.ok("get", f"/api/v1/items/{res['created'][1]}")
    assert drill["photo"]["id"] != scan["photo"]["id"]                 # its own cut-out
    assert (drill["photo"]["width"], drill["photo"]["height"]) == (384, 348)  # x 0.06..0.54, y 0.06..0.64 of 800x600
    assert pliers["photo"]["id"] == scan["photo"]["id"]                # no box: whole photo
    assert u.ok("get", f"/api/v1/items/{maybe['id']}")["status"] == "missing"
    assert u.ok("get", f"/api/v1/items/{gone['id']}")["deleted_at"]
