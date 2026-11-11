"""Quick capture: photos now, details later."""

import asyncio

from home_inventory_server.ai.providers import AIProviderError
from home_inventory_server.services import capture as capture_svc
from tests.conftest import jpeg_bytes


def _snap(u, **place):
    return u.ok("post", "/api/v1/capture", files={"file": ("p.jpg", jpeg_bytes())}, data={k: str(v) for k, v in place.items()})


def test_quick_photos_land_in_box_and_need_details(env):
    u = env.admin
    box = u.container("Box", "15")
    shots = [_snap(u, container_id=box["id"]) for _ in range(3)]
    assert all(s["needs_details"] and s["photo"] for s in shots)
    assert shots[0]["capture"]["ai_status"] == "none"  # no AI configured
    contents = u.ok("get", f"/api/v1/containers/{box['id']}")
    assert len(contents["items"]) == 3
    todo = u.ok("get", "/api/v1/items", params={"needs_details": True, "sort": "oldest"})
    assert [i["id"] for i in todo["items"]] == [s["id"] for s in shots]
    assert u.ok("get", "/api/v1/stats")["needs_details"] == 3
    # Finishing one
    done = u.ok("patch", f"/api/v1/items/{shots[0]['id']}", json={"name": "Hose reel", "category": "Garden", "identification_status": "confirmed"})
    assert done["needs_details"] is False and done["name"] == "Hose reel"
    assert done["placement"]["container"]["id"] == box["id"] and done["photo"]["id"] == shots[0]["photo"]["id"]
    assert u.ok("get", "/api/v1/stats")["needs_details"] == 2
    hist = u.ok("get", "/api/v1/history", params={"container_id": box["id"]})["events"]
    assert sum(e["action"] == "captured" for e in hist) == 3


def test_quick_photos_to_a_location_and_validation(env):
    u = env.admin
    shelf = u.location("Shelf")
    s = _snap(u, location_id=shelf["id"])
    assert s["placement"]["location"]["id"] == shelf["id"]
    assert u.post("/api/v1/capture", files={"file": ("p.jpg", jpeg_bytes())}).status_code == 422
    assert u.post("/api/v1/capture", files={"file": ("p.jpg", b"not an image")}, data={"location_id": str(shelf["id"])}).status_code == 415
    viewer = env.user("u-v", "Viewer")
    viewer.get("/api/v1/me")
    vid = next(x["id"] for x in u.ok("get", "/api/v1/users") if x["name"] == "Viewer")
    u.ok("patch", f"/api/v1/users/{vid}", json={"role": "read_only"})
    assert viewer.post("/api/v1/capture", files={"file": ("p.jpg", jpeg_bytes())}, data={"location_id": str(shelf["id"])}).status_code == 403


def test_background_ai_suggestion_is_only_a_suggestion(ai_env, fake_ai):
    u = ai_env.admin
    box = u.container("Box")
    s1 = _snap(u, container_id=box["id"])
    s2 = _snap(u, container_id=box["id"])
    assert s1["capture"]["ai_status"] == "queued"
    assert ai_env.state.capture_queue.qsize() == 2
    fake_ai.responses += [
        {"name": "Cordless drill", "description": None, "category": "Tools", "manufacturer": "DeWalt", "model_number": None, "confidence": 0.9, "notes": None},
        AIProviderError("quota exceeded"),
    ]
    capture_svc._suggest_one(ai_env.state, s1["id"])
    capture_svc._suggest_one(ai_env.state, s2["id"])
    a = u.ok("get", f"/api/v1/items/{s1['id']}")
    assert a["capture"]["ai_status"] == "ready" and a["capture"]["suggestion"]["name"] == "Cordless drill"
    assert a["name"] == capture_svc.PLACEHOLDER_NAME and a["needs_details"]  # nothing applied without the user
    b = u.ok("get", f"/api/v1/items/{s2['id']}")
    assert b["capture"]["ai_status"] == "failed" and b["needs_details"] and b["photo"]  # capture kept


def test_worker_resumes_queued_captures_after_restart(ai_env, fake_ai):
    u = ai_env.admin
    box = u.container("Box")
    s = _snap(u, container_id=box["id"])
    fake_ai.responses.append({"name": "Lantern", "description": None, "category": None, "manufacturer": None,
                              "model_number": None, "confidence": 0.7, "notes": None})

    async def run():
        ai_env.state.capture_queue = asyncio.Queue()  # "restart": the in-memory queue is empty
        task = asyncio.create_task(capture_svc.worker(ai_env.state))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not ai_env.state.capture_queue.qsize() and fake_ai.calls:
                await ai_env.state.capture_queue.join()
                break
        task.cancel()

    asyncio.run(run())
    assert u.ok("get", f"/api/v1/items/{s['id']}")["capture"]["suggestion"]["name"] == "Lantern"
