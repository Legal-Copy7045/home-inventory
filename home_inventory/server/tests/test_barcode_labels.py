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
