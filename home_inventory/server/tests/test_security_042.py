"""0.42.0: fixes for the findings of the first /regression security audit.

Customer equipment names in events, shopping-list notes and product history;
the barcode API key in the log; non-product barcodes sent to outside catalogs;
panorama upload formats; attachment caching; roles by display name; the token
alone making someone an administrator; NAT64 addresses in link imports.
"""

from __future__ import annotations

import io
import ipaddress
import logging

import httpx
import pytest
from PIL import Image

from home_inventory_server.services import maintenance_notices
from tests.conftest import Env, jpeg_bytes
from tests.test_parts import _equipment, _set, _task
from tests.test_privacy import _viewer


def _check(env):
    db = env.state.db.SessionLocal()
    try:
        return maintenance_notices.check(db)
    finally:
        db.close()


@pytest.fixture
def customer_mower(env):
    """Customer equipment with an overdue task that needs an out-of-stock part."""
    u = env.admin
    cust = u.ok("post", "/api/v1/customers", json={"name": "Casey Hillcrest"})
    job = u.ok("post", "/api/v1/repair-jobs", json={"customer_id": cust["id"],
                                                     "new_equipment": {"name": "Casey's chainsaw"}})
    item = {"id": job["item"]["id"]}
    u.ok("put", f"/api/v1/items/{item['id']}/equipment", json={"equipment_type": "Chainsaw", "meter": "hours",
                                                                "current_hours": 0})
    plug = u.item("Spark plug", quantity=1)
    u.ok("delete", f"/api/v1/items/{plug['id']}")  # none in stock
    t = _task(u, item, "Sharpen chain", interval_hours=10)
    u.ok("patch", f"/api/v1/maintenance-tasks/{t['id']}", json={"next_due_hours": 0})
    _set(u, t, [{"product_id": plug["product_id"], "quantity": 1}])
    return item


def test_maintenance_due_event_never_names_customer_equipment(env, customer_mower):
    events = []
    env.state.bus.publish = lambda t, d: events.append((t, d))
    assert _check(env)["sent"] == 1
    d = next(d for k, d in events if k == "maintenance_due")
    assert "Casey" not in str(d)
    assert d["item_name"] == "Customer equipment" and d["message"].startswith("Customer equipment: Sharpen chain")


def test_household_equipment_keeps_its_name_in_events(env):
    events = []
    env.state.bus.publish = lambda t, d: events.append((t, d))
    u = env.admin
    mower = _equipment(u, "Ride-on mower")
    t = _task(u, mower, "Blades", interval_hours=10)
    u.ok("patch", f"/api/v1/maintenance-tasks/{t['id']}", json={"next_due_hours": 0})
    _check(env)
    d = next(d for k, d in events if k == "maintenance_due")
    assert d["item_name"] == "Ride-on mower" and "Ride-on mower: Blades" in d["message"]


def test_event_bus_redacts_every_name_field():
    from home_inventory_server import events as ev

    class FakeDb:
        def flush(self):
            pass

        def get(self, model, key):
            return object()  # every item is customer equipment here

    out = ev._without_customer_names(FakeDb(), {"item_id": 1, "item_name": "Casey's chainsaw",
                                                "message": "Casey's chainsaw: oil is due."})
    assert out["item_name"] == "Customer equipment" and "Casey" not in out["message"]
    assert out["customer_equipment"] is True


def test_auto_shopping_note_never_names_customer_equipment(env, customer_mower):
    _check(env)
    v = _viewer(env)
    shop = v.get("/api/v1/shopping-list").json()
    notes = [e.get("note") or "" for g in shop["groups"] for e in g["entries"]]
    assert notes and not any("Casey" in n for n in notes)
    assert any("customer equipment" in n for n in notes)


def test_read_only_history_hides_customer_only_products(env, customer_mower):
    u = env.admin
    item = u.ok("get", f"/api/v1/items/{customer_mower['id']}")
    u.ok("put", f"/api/v1/products/{item['product_id']}/parts", json={"content_quantity": 1})
    v = _viewer(env)
    hist = v.get("/api/v1/history", params={"limit": 200}).json()
    rows = hist["events"]
    assert rows and not any("Casey" in (r.get("summary") or "") for r in rows)
    one = v.get("/api/v1/history", params={"entity_type": "product", "entity_id": item["product_id"]}).json()
    assert one["events"] == []
    # the administrator still sees it
    mine = u.ok("get", "/api/v1/history", params={"entity_type": "product", "entity_id": item["product_id"]})
    assert mine["events"]


def test_failed_barcode_lookup_never_logs_the_api_key(tmp_path, caplog):
    from home_inventory_server.services import barcode

    def failing(code, timeout):
        req = httpx.Request("GET", f"https://api.barcodelookup.com/v3/products?barcode={code}&key=FAKE-KEY-0000")
        raise httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req))

    e = Env(tmp_path, barcode_providers=[("barcodelookup", failing)])
    with caplog.at_level(logging.INFO):
        e.admin.ok("post", "/api/v1/barcode/lookup", json={"code": "4006381333931"})
    text = caplog.text
    assert "FAKE-KEY-0000" not in text and "barcodelookup" in text and "403" in text
    assert barcode  # imported for the log name
    e.state.db.dispose()


def test_non_product_barcodes_stay_in_the_house(tmp_path):
    calls = []
    e = Env(tmp_path, barcode_providers=[("fake", lambda code, timeout: calls.append(code))])
    e.admin.ok("post", "/api/v1/barcode/lookup", json={"code": "../../cgi/search.pl?q=x#"})
    e.admin.ok("post", "/api/v1/barcode/lookup", json={"code": "SN-4471-ABC"})
    assert calls == []
    e.admin.ok("post", "/api/v1/barcode/lookup", json={"code": "4006381333931"})
    assert calls  # a real product barcode still goes out
    e.state.db.dispose()


def test_open_facts_url_encodes_the_code(monkeypatch):
    from home_inventory_server.services import barcode

    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(barcode.httpx, "get", fake_get)
    barcode._open_facts("world.openfoodfacts.org", "Open Food Facts")("a/b?c", 5)
    assert seen == ["https://world.openfoodfacts.org/api/v2/product/a%2Fb%3Fc.json"]


def _img(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), (10, 120, 200)).save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["TIFF", "BMP", "PPM"])
def test_panorama_refuses_formats_outside_the_allow_list(fmt):
    from home_inventory_server.services.media import MediaError, stitch

    with pytest.raises(MediaError):
        stitch([jpeg_bytes(), _img(fmt)])


def test_panorama_still_takes_jpeg_and_png():
    from home_inventory_server.services.media import stitch

    assert stitch([jpeg_bytes(), _img("PNG")])[:2] == b"\xff\xd8"


@pytest.mark.parametrize("kind", ["file", "thumb"])
def test_attachments_are_not_cached(env, kind):
    att = env.admin.upload(jpeg_bytes())
    r = env.admin.get(f"/api/v1/attachments/{att['id']}/{kind}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"


def test_display_name_never_grants_admin(tmp_path):
    e = Env(tmp_path, admin_users=["morgan"])
    impostor = e.user("u-casey", "Casey")
    impostor.headers["X-Remote-User-Display-Name"] = "Morgan"
    me = impostor.ok("get", "/api/v1/me")
    assert me["role"] != "admin"
    real = e.user("u-morgan", "morgan")
    real.headers["X-Remote-User-Name"] = "morgan"
    assert real.ok("get", "/api/v1/me")["role"] == "admin"
    e.state.db.dispose()


def test_home_assistant_answer_beats_forwarded_admin_flag(tmp_path):
    e = Env(tmp_path)
    e.state.admin_directory.is_admin = lambda ha_id: False  # Home Assistant says: not an administrator
    e.state.admin_directory.name = lambda ha_id: "Sam"
    api = e.raw_api()
    r = api.get("/api/v1/me", headers={"Authorization": f"Bearer {e.state.api_token}", "X-Remote-User-Id": "u-sam",
                                       "X-Remote-User-Is-Admin": "true"})
    assert r.status_code == 200 and r.json()["is_admin"] is False
    e.state.db.dispose()


@pytest.mark.parametrize("addr, public", [
    ("64:ff9b::a00:1", False),      # NAT64 of 10.0.0.1
    ("64:ff9b::7f00:1", False),     # NAT64 of 127.0.0.1
    ("64:ff9b:1::a00:1", False),
    ("::a00:1", False),             # IPv4-compatible
    ("64:ff9b::808:808", True),     # NAT64 of a public address
    ("2001:4860:4860::8888", True),
])
def test_link_import_checks_ipv4_inside_ipv6(addr, public):
    from home_inventory_server.services.weblink import _embedded_ipv4

    assert _embedded_ipv4(ipaddress.ip_address(addr)).is_global is public
