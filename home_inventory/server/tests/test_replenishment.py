"""Replenishment, retailer links and the shopping list."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from home_inventory_server.services.replenishment import suggested_quantity
from home_inventory_server.services.retailers import PROVIDERS


@pytest.mark.parametrize(
    "stock,minimum,target,reorder,pack,expected",
    [
        (1, 2, 5, None, 1, 4),  # the spec's example
        (5, 2, 5, None, 1, 0),  # at target
        (2, 2, None, None, 1, 0),  # no target: restock to the minimum only when below it
        (1, 2, None, None, 1, 1),
        (1, 2, 5, 6, 1, 6),  # reorder quantity wins when larger
        (1, 2, 5, None, 3, 6),  # rounded up to whole packs of 3
        (0, None, None, None, 1, 0),  # untracked
    ],
)
def test_suggested_quantity(stock, minimum, target, reorder, pack, expected):
    assert suggested_quantity(stock, minimum, target, reorder, pack) == expected


def _oil(u, qty=1):
    return u.item("SAE 30 engine oil", quantity=qty, min_quantity=2)


def test_thresholds_and_suggestions(env):
    u = env.admin
    oil = _oil(u)
    u.ok("put", f"/api/v1/products/{oil['product_id']}/restock", json={"target_stock": 5})
    filters = u.item("Mower air filter", quantity=2, min_quantity=2)
    u.ok("put", f"/api/v1/products/{filters['product_id']}/restock", json={"target_stock": 4})
    towels = u.item("Paper towels", quantity=6, min_quantity=2)
    d = u.ok("get", "/api/v1/replenishment")
    below = {r["name"]: r for r in d["below_minimum"]}
    assert below["SAE 30 engine oil"]["suggested_quantity"] == 4 and below["SAE 30 engine oil"]["stock"] == 1
    assert [r["name"] for r in d["approaching"]] == ["Mower air filter"]
    assert [r["name"] for r in d["ok"]] == ["Paper towels"]
    assert d["needs_count"] == 1
    assert towels and u.ok("get", "/api/v1/stats")["replenishment_needed"] == 1


def test_restock_level_validation(env):
    u = env.admin
    oil = _oil(u)
    r = u.put(f"/api/v1/products/{oil['product_id']}/restock", json={"min_quantity": 5, "target_stock": 3})
    assert r.status_code == 422


def test_sources_and_links(env):
    u = env.admin
    oil = _oil(u)
    pid = oil["product_id"]
    d = u.ok("post", f"/api/v1/products/{pid}/sources", json={"retailer": "amazon", "url": "https://www.amazon.com/Briggs-Stratton/dp/B000BQ7JXA?th=1", "estimated_price": "8.99"})
    amazon = d["sources"][0]
    assert amazon["retailer_product_id"] == "B000BQ7JXA" and amazon["preferred"] is True  # ASIN read from the link
    assert amazon["capabilities"]["cart_handoff"] is True and amazon["capabilities"]["cart_api"] is False
    d = u.ok("post", f"/api/v1/products/{pid}/sources", json={"retailer": "lowes", "retailer_product_id": "5013211393"})
    lowes = next(s for s in d["sources"] if s["retailer"] == "lowes")
    assert lowes["link_kind"] == "search" and lowes["link"] == "https://www.lowes.com/search?searchTerm=5013211393"
    assert lowes["capabilities"]["cart_handoff"] is False
    d = u.ok("post", f"/api/v1/products/{pid}/sources", json={"retailer": "tractor_supply", "url": "https://www.tractorsupply.com/tsc/product/traveller-sae-30-oil-1-qt-1234567"})
    tsc = next(s for s in d["sources"] if s["retailer"] == "tractor_supply")
    assert tsc["link_kind"] == "product" and tsc["retailer_product_id"] == "1234567"
    # wrong shop for the link, bad ASIN, non-web links
    assert u.post(f"/api/v1/products/{pid}/sources", json={"retailer": "lowes", "url": "https://www.amazon.com/dp/B000BQ7JXA"}).status_code == 422
    assert u.post(f"/api/v1/products/{pid}/sources", json={"retailer": "amazon", "retailer_product_id": "nope"}).status_code == 422
    assert u.post(f"/api/v1/products/{pid}/sources", json={"retailer": "other", "url": "javascript:alert(1)"}).status_code == 422
    # making another source preferred unsets the first
    d = u.ok("patch", f"/api/v1/sources/{lowes['id']}", json={"preferred": True})
    assert [s["retailer"] for s in d["sources"] if s["preferred"]] == ["lowes"]


def test_amazon_cart_form_link():
    class Src:
        retailer = "amazon"
        url = None
        retailer_product_id = "B000BQ7JXA"

    class Src2(Src):
        retailer_product_id = "B07PGL2ZSL"

    link = PROVIDERS["amazon"].cart_handoff_url([(Src(), 2), (Src2(), 1)], {"amazon_associate_tag": "mytag-20"})
    u = urlparse(link)
    q = parse_qs(u.query)
    assert u.netloc == "www.amazon.com" and u.path == "/gp/aws/cart/add.html"
    assert q == {"ASIN.1": ["B000BQ7JXA"], "Quantity.1": ["2"], "ASIN.2": ["B07PGL2ZSL"], "Quantity.2": ["1"], "AssociateTag": ["mytag-20"]}
    uk = PROVIDERS["amazon"].cart_handoff_url([(Src(), 1)], {"amazon_domain": "amazon.co.uk"})
    assert uk.startswith("https://www.amazon.co.uk/gp/aws/cart/add.html?") and "AssociateTag" not in uk


def test_shopping_list_grouped_by_retailer_and_never_claims_cart(env):
    u = env.admin
    oil = _oil(u)
    u.ok("put", f"/api/v1/products/{oil['product_id']}/restock", json={"target_stock": 5})
    u.ok("post", f"/api/v1/products/{oil['product_id']}/sources", json={"retailer": "amazon", "retailer_product_id": "B000BQ7JXA", "estimated_price": "8.99"})
    filt = u.item("Mower air filter", quantity=0, min_quantity=1)
    u.ok("post", f"/api/v1/products/{filt['product_id']}/sources", json={"retailer": "lowes", "url": "https://www.lowes.com/pd/Briggs-filter/1000123456"})
    lst = u.ok("post", "/api/v1/shopping-list", json={"product_id": oil["product_id"]})
    lst = u.ok("post", "/api/v1/shopping-list", json={"product_id": filt["product_id"], "quantity": 2})
    groups = {g["retailer"]: g for g in lst["groups"]}
    assert set(groups) == {"amazon", "lowes"}
    oil_entry = groups["amazon"]["entries"][0]
    assert oil_entry["quantity"] == 4 and oil_entry["estimated_cost"] == pytest.approx(35.96)  # suggested qty used
    assert u.post("/api/v1/shopping-list", json={"product_id": oil["product_id"]}).status_code == 422  # already listed

    ids = [oil_entry["id"], groups["lowes"]["entries"][0]["id"]]
    h = u.ok("post", "/api/v1/shopping-list/handoff", json={"entry_ids": ids})
    hg = {g["retailer"]: g for g in h["groups"]}
    assert "ASIN.1=B000BQ7JXA&Quantity.1=4" in hg["amazon"]["cart_url"] and "confirm" in hg["amazon"]["cart_note"]
    assert hg["lowes"]["cart_url"] is None and "no way for other apps" in hg["lowes"]["cart_note"]
    assert hg["lowes"]["links"][0]["url"] == "https://www.lowes.com/pd/Briggs-filter/1000123456"
    statuses = {e["name"]: e["status"] for g in u.ok("get", "/api/v1/shopping-list")["groups"] for e in g["entries"]}
    assert statuses == {"SAE 30 engine oil": "handed_off", "Mower air filter": "handed_off"}  # NOT in_cart
    # only the user can say it's in the cart
    u.ok("patch", f"/api/v1/shopping-list/{ids[0]}", json={"status": "in_cart"})
    d = u.ok("get", "/api/v1/replenishment")
    assert d["below_minimum"][0]["in_cart"] is True or any(r["in_cart"] for r in d["below_minimum"])
    assert u.patch(f"/api/v1/shopping-list/{ids[0]}", json={"status": "purchased"}).status_code == 422


def test_purchase_updates_stock(env):
    u = env.admin
    oil = _oil(u)
    second = u.ok("post", "/api/v1/items", json={"product_id": oil["product_id"], "name": "SAE 30 engine oil", "quantity": 0,
                                                 "location_id": u.location("Garage")["id"]})["items"][0]
    assert second["product_id"] == oil["product_id"]
    lst = u.ok("post", "/api/v1/shopping-list", json={"product_id": oil["product_id"], "quantity": 3})
    eid = lst["groups"][0]["entries"][0]["id"]
    u.ok("post", f"/api/v1/shopping-list/{eid}/purchased", json={"unit_price": "7.49", "item_id": second["id"]})
    assert u.ok("get", f"/api/v1/items/{second['id']}")["quantity"] == 3
    assert u.ok("get", f"/api/v1/items/{oil['id']}")["quantity"] == 1
    restock = u.ok("get", f"/api/v1/products/{oil['product_id']}/restock")
    assert restock["profile"]["last_price"] == 7.49 and restock["status"]["stock"] == 4
    assert u.ok("get", "/api/v1/shopping-list")["open_count"] == 0
    assert u.post(f"/api/v1/shopping-list/{eid}/purchased", json={}).status_code == 422  # closed
    # bought but not added to stock
    lst = u.ok("post", "/api/v1/shopping-list", json={"product_id": oil["product_id"], "quantity": 1})
    eid = lst["groups"][0]["entries"][0]["id"]
    u.ok("post", f"/api/v1/shopping-list/{eid}/purchased", json={"add_to_stock": False})
    assert u.ok("get", f"/api/v1/products/{oil['product_id']}/restock")["status"]["stock"] == 4


def test_defer_and_cancel(env):
    u = env.admin
    oil = _oil(u)
    lst = u.ok("post", "/api/v1/shopping-list", json={"product_id": oil["product_id"]})
    eid = lst["groups"][0]["entries"][0]["id"]
    lst = u.ok("patch", f"/api/v1/shopping-list/{eid}", json={"status": "deferred", "deferred_until": "2030-01-01"})
    assert lst["groups"][0]["entries"][0]["status"] == "deferred" and lst["open_count"] == 0
    u.ok("patch", f"/api/v1/shopping-list/{eid}", json={"status": "cancelled"})
    assert u.ok("get", "/api/v1/shopping-list")["groups"] == []


def test_customer_stock_is_not_replenished(env):
    u = env.admin
    c = u.ok("post", "/api/v1/customers", json={"name": "Pat"})
    eq = u.ok("post", f"/api/v1/customers/{c['id']}/equipment", json={"name": "Pat's oil"})
    u.ok("patch", f"/api/v1/items/{eq['id']}", json={"min_quantity": 5})
    d = u.ok("get", "/api/v1/replenishment")
    assert all(r["name"] != "Pat's oil" for r in d["below_minimum"] + d["approaching"] + d["ok"])


def test_read_only_can_view_not_change(env):
    u = env.admin
    oil = _oil(u)
    v = env.user("u-v", "Val Viewer")
    v.get("/api/v1/me")
    uid = next(x["id"] for x in u.ok("get", "/api/v1/users") if x["name"] == "Val Viewer")
    u.ok("patch", f"/api/v1/users/{uid}", json={"role": "read_only"})
    assert v.get("/api/v1/replenishment").status_code == 200
    assert v.post("/api/v1/shopping-list", json={"product_id": oil["product_id"]}).status_code == 403


def test_amazon_settings(env):
    u = env.admin
    s = u.ok("patch", "/api/v1/settings", json={"amazon_domain": "amazon.co.uk", "amazon_associate_tag": "mytag-21"})
    assert s["amazon_domain"] == "amazon.co.uk"
    assert u.patch("/api/v1/settings", json={"amazon_domain": "evil.example"}).status_code == 422
    oil = _oil(u)
    d = u.ok("post", f"/api/v1/products/{oil['product_id']}/sources", json={"retailer": "amazon", "retailer_product_id": "B000BQ7JXA"})
    assert d["sources"][0]["link"] == "https://www.amazon.co.uk/dp/B000BQ7JXA"
