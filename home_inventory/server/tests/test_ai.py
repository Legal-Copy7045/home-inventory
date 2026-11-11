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


def test_ha_ai_task_provider(tmp_path, monkeypatch):
    s = make_settings(tmp_path, ai_provider="home_assistant")
    s.supervisor_token = "tok"
    s.ha_media_dir = tmp_path / "media"
    calls = {}

    def fake_get(url, headers, timeout):
        return httpx.Response(200, json=[
            {"entity_id": "ai_task.text_only", "attributes": {"supported_features": 1}},
            {"entity_id": "ai_task.google_ai_task", "attributes": {"supported_features": 3}},
        ], request=httpx.Request("GET", url))

    def fake_post(url, json, headers, timeout):
        calls["url"], calls["body"] = url, json
        att = json["attachments"][0]["media_content_id"]
        rel = att.split("media_source/local/")[1]
        calls["file_existed"] = (s.ha_media_dir / rel).exists()
        return httpx.Response(200, json={"service_response": {"data": '```json\n{"name": "Mug", "confidence": 0.7}\n```'}})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    p = build_provider(s)
    assert isinstance(p, HomeAssistantAITaskProvider)
    out = p.generate_json("identify", {"type": "object"}, (b"jpegdata", "image/jpeg"), 10)
    assert json.loads(out)["name"] == "Mug"
    assert calls["body"]["entity_id"] == "ai_task.google_ai_task"
    assert calls["url"] == "http://supervisor/core/api/services/ai_task/generate_data?return_response"
    assert calls["file_existed"] is True
    assert list((s.ha_media_dir / "home_inventory/.ai-tmp").iterdir()) == []  # temp image removed


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
