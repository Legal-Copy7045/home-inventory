"""GTIN handling, barcode lookup, QR codes and PDF labels."""

import pytest

from home_inventory_server.services import gtin
from home_inventory_server.services.labels import TEMPLATES, LabelData, parse_qr, qr_payload, render_pdf


@pytest.mark.parametrize(
    "code,sym,gtin14",
    [
        ("012345678905", "upc_a", "00012345678905"),
        ("4006381333931", "ean_13", "04006381333931"),
        ("96385074", "ean_8", "00000096385074"),
        ("10012345678902", "gtin_14", "10012345678902"),
        ("01234565", None, None),  # depends on hint below
        ("01234567890", "upc_a", "00012345678905"),  # UPC-A without check digit
    ],
)
def test_gtin_parse(code, sym, gtin14):
    bc = gtin.parse(code)
    if sym:
        assert bc.symbology == sym and bc.gtin14 == gtin14


def test_upce_expansion():
    assert gtin.upce_to_upca("01234565") == "012345000065"
    assert gtin.upce_to_upca("04252614") == "042100005264"
    bc = gtin.parse("04252614", hint="upc_e")
    assert bc.symbology == "upc_e" and bc.gtin14 == "00042100005264"
    # same product as its UPC-A label
    assert gtin.normalise("042100005264") == bc.gtin14


def test_invalid_and_non_numeric():
    assert gtin.parse("4006381333932").gtin14 is None  # bad check digit
    assert gtin.parse("ABC-123").symbology == "other"


def test_barcode_lookup_inventory_first(env):
    u = env.admin
    shed = u.location("Shed")
    box = u.container("Box", "15", location_id=shed["id"])
    garage = u.location("Garage")
    a = u.item("Extension cord", barcode="012345678905", quantity=2, container_id=box["id"])
    u.ok("post", f"/api/v1/items/{a['id']}/move", json={"location_id": garage["id"], "quantity": 1})
    res = u.ok("post", "/api/v1/barcode/lookup", json={"code": "0012345678905"})
    assert res["owned_quantity"] == 2
    assert "You own 2" in res["message"]
    paths = sorted(h["path"] for h in res["inventory_matches"][0]["holdings"])
    assert paths == ["Garage", "Shed › Box #15"]


def test_barcode_catalog_lookup_and_cache(tmp_path):
    from tests.conftest import Env

    calls = []

    def fake_provider(code, timeout):
        calls.append(code)
        return {"name": "Duct tape", "manufacturer": "3M", "source": "Fake"} if code.endswith("905") else None

    e = Env(tmp_path, barcode_providers=[("fake", fake_provider)])
    u = e.admin
    res = u.ok("post", "/api/v1/barcode/lookup", json={"code": "012345678905"})
    assert res["inventory_matches"] == [] and res["catalog"]["name"] == "Duct tape"
    u.ok("post", "/api/v1/barcode/lookup", json={"code": "012345678905"})
    assert len(calls) == 1  # cached
    miss = u.ok("post", "/api/v1/barcode/lookup", json={"code": "4006381333931"})
    assert miss["catalog"] is None  # no result is normal
    # Adding another instance never overwrites an existing record
    first = u.item("Duct tape", barcode="012345678905", quantity=1)
    second = u.item("Duct tape", barcode="012345678905", quantity=3)
    assert first["id"] != second["id"] and first["product_id"] == second["product_id"]
    assert u.ok("get", f"/api/v1/items/{first['id']}")["quantity"] == 1
    e.state.db.dispose()


def test_qr_payload_round_trip():
    assert parse_qr(qr_payload("ABCD2345", None)) == "ABCD2345"
    assert parse_qr(qr_payload("ABCD2345", "https://ha.example/hassio/ingress/abc_home_inventory")) == "ABCD2345"
    assert parse_qr("abcd2345") == "ABCD2345"
    assert parse_qr("https://example.com") is None


def test_qr_stable_when_container_changes(env):
    u = env.admin
    box = u.container("Box", "1")
    qr = box["qr_code"]
    loc = u.location("Loft")
    u.ok("patch", f"/api/v1/containers/{box['id']}", json={"name": "Renamed", "number": "99"})
    u.ok("post", f"/api/v1/containers/{box['id']}/move", json={"location_id": loc["id"]})
    u.item("Thing", container_id=box["id"])
    assert u.ok("get", f"/api/v1/containers/{box['id']}")["qr_code"] == qr
    svg = u.get(f"/api/v1/containers/{box['id']}/qr.svg")
    assert svg.status_code == 200 and svg.content.lstrip().startswith(b"<svg")


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_label_templates_render(template):
    labels = [LabelData(code=f"CODE{i:04d}", payload=f"HINV:C:CODE{i:04d}", title=f"Box {i}", number=str(i),
                        location="Shed › Right side", contents=["Hammer", "2 × Tape"]) for i in range(25)]
    pdf = render_pdf(labels, template)
    assert pdf.startswith(b"%PDF")


def test_labels_endpoint_bulk(env):
    u = env.admin
    ids = [u.container(f"Box {i}", str(i))["id"] for i in range(3)]
    u.item("Hammer", container_id=ids[0])
    r = u.post("/api/v1/labels/pdf", json={"container_ids": ids, "template": "avery_5160", "include_contents": True, "copies": 2})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.count(b"/Type /Page") >= 1
    assert u.post("/api/v1/labels/pdf", json={"container_ids": []}).status_code == 422


BASE = "https://ha.example.com/b1a256fc_home_inventory"


def test_label_links_open_the_app():
    from home_inventory_server.services.labels import parse_qr_ref

    assert qr_payload("ABCD2345", BASE) == f"{BASE}/c/ABCD2345"
    assert qr_payload("ABCD2345", BASE + "/", "L") == f"{BASE}/l/ABCD2345"
    assert qr_payload("ABCD2345", "javascript:alert(1)") == "HINV:C:ABCD2345"  # unsafe base ignored
    assert parse_qr_ref(f"{BASE}/c/ABCD2345") == ("C", "ABCD2345")
    assert parse_qr_ref(f"{BASE}/l/ABCD2345?x=1") == ("L", "ABCD2345")
    assert parse_qr_ref("HINV:L:ABCD2345") == ("L", "ABCD2345")
    assert parse_qr_ref("https://old.example/app?c=ABCD2345") == ("C", "ABCD2345")  # earlier format
    assert parse_qr_ref("abcd2345") == (None, "ABCD2345")
    assert parse_qr_ref(f"{BASE}/c/") is None


def test_location_labels_and_resolve(env):
    u = env.admin
    shed = u.location("Shed")
    shelf = u.location("Middle shelf", shed["id"])
    assert len(shelf["qr_code"]) == 8
    box = u.container("Hoses", "1", location_id=shelf["id"])
    u.item("Hose reel", location_id=shelf["id"])
    r = u.ok("post", "/api/v1/qr/resolve", json={"text": f"{BASE}/l/{shelf['qr_code']}"})
    assert r == {"type": "location", "id": shelf["id"], "location_id": shelf["id"], "qr_code": shelf["qr_code"]}
    r = u.ok("post", "/api/v1/qr/resolve", json={"text": f"{BASE}/c/{box['qr_code']}"})
    assert r["type"] == "container" and r["container_id"] == box["id"]
    assert u.ok("post", "/api/v1/qr/resolve", json={"text": shelf["qr_code"]})["type"] == "location"  # bare code
    # The QR image uses the browser's address when Settings has none
    svg = u.get(f"/api/v1/locations/{shelf['id']}/qr.svg", params={"base": BASE})
    assert svg.status_code == 200
    r = u.post("/api/v1/labels/pdf", json={"location_ids": [shelf["id"]], "container_ids": [box["id"]],
                                           "include_contents": True, "base_url": BASE})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_settings_address_overrides_browser(env, monkeypatch):
    from home_inventory_server.services import labels

    seen = []
    real = labels.qr_payload
    monkeypatch.setattr(labels, "qr_payload", lambda code, base, kind="C": seen.append(base) or real(code, base, kind))
    u = env.admin
    box = u.container("Box")
    u.get(f"/api/v1/containers/{box['id']}/qr.svg", params={"base": BASE})
    u.ok("patch", "/api/v1/settings", json={"qr_base_url": "https://inventory.example.org/app"})
    u.get(f"/api/v1/containers/{box['id']}/qr.svg", params={"base": BASE})
    assert seen == [BASE, "https://inventory.example.org/app"]
