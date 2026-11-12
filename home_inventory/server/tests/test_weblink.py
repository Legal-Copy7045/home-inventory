"""Import from a product web link: parsing, safety and the API flow."""

import json

import pytest

from home_inventory_server.services import weblink
from home_inventory_server.services.weblink import LinkError, _price, parse_page
from tests.conftest import jpeg_bytes

JSONLD_PAGE = """<!doctype html><html><head>
<title>DeWalt 20V MAX Cordless Drill DCD771C2 | Example Hardware</title>
<meta property="og:site_name" content="Example Hardware">
<meta property="og:image" content="/img/og.jpg">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"BreadcrumbList","itemListElement":[]},
 {"@type":"Product","name":"DEWALT 20V MAX Cordless Drill/Driver Kit","description":"Compact &amp; lightweight <b>drill</b>.",
  "brand":{"@type":"Brand","name":"DEWALT"},"mpn":"DCD771C2","sku":"1000-123","gtin12":"076174123456",
  "category":"Power Tools &gt; Drills","image":["https://cdn.example.com/drill.jpg"],
  "offers":{"@type":"Offer","price":"99.00","priceCurrency":"USD","seller":{"@type":"Organization","name":"Example Hardware"}}}
]}</script></head><body><h1>Drill</h1></body></html>"""

META_PAGE = """<html><head><title>Kink Control Water Hose 50ft - Gardenly</title>
<meta property="og:title" content="Kink Control Plus Water Hose 50ft">
<meta name="description" content="Heavy-duty garden hose.">
<meta property="og:image" content="https://img.example.com/hose.png">
<meta property="product:price:amount" content="1.299,95">
<meta property="product:price:currency" content="eur">
</head><body><p>Some text</p><script>var x=1</script></body></html>"""


def test_parse_jsonld_product():
    p = parse_page(JSONLD_PAGE, "https://www.examplehardware.com/p/123")
    assert p.source == "json-ld"
    assert p.name == "DEWALT 20V MAX Cordless Drill/Driver Kit"
    assert p.description == "Compact & lightweight drill."
    assert p.manufacturer == "DEWALT" and p.model_number == "DCD771C2" and p.sku == "1000-123"
    assert p.barcode == "076174123456"
    assert p.image_url == "https://cdn.example.com/drill.jpg"
    assert (p.price, p.currency, p.retailer) == ("99.00", "USD", "Example Hardware")
    assert p.category == "Power Tools > Drills"


def test_parse_meta_tags_only():
    p = parse_page(META_PAGE, "https://shop.gardenly.co.uk/hose")
    assert p.source == "meta"
    assert p.name == "Kink Control Plus Water Hose 50ft"
    assert p.description == "Heavy-duty garden hose."
    assert (p.price, p.currency) == ("1299.95", "EUR")
    assert p.retailer == "Gardenly"
    assert "var x" not in p.text


def test_title_fallback_strips_site_name():
    p = parse_page("<html><head><title>Garden Spade | Big Shop</title></head></html>", "https://bigshop.com/x")
    assert p.name == "Garden Spade"


@pytest.mark.parametrize(
    "raw,expected",
    [("1,299.99", "1299.99"), ("1.299,99", "1299.99"), ("$12", "12"), (12.5, "12.5"), ("£1,200", "1200"),
     ("19,99 €", "19.99"), ("free", None)],
)
def test_price_normalisation(raw, expected):
    assert _price(raw) == expected


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://localhost:8123/", "http://192.168.1.10/", "http://10.0.0.5/", "http://169.254.169.254/latest",
     "http://[::1]/", "ftp://example.com/", "http://user:pw@example.com/", "file:///etc/passwd"],
)
def test_private_and_unsupported_links_blocked(url):
    with pytest.raises(LinkError):
        weblink.fetch(url, 1000, "*/*")


def test_redirect_to_private_address_blocked(monkeypatch):
    import httpx

    def handler(request: httpx.Request):
        if request.url.host == "93.184.215.14":
            return httpx.Response(302, headers={"location": "http://192.168.1.1/admin"})
        return httpx.Response(200, text="should not be reached")

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(LinkError, match="private"):
        weblink.fetch("http://93.184.215.14/item", 1000, "*/*")


def test_blocked_by_site_message(monkeypatch):
    import httpx

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(403)), **kw))
    with pytest.raises(LinkError, match="refused automatic reading"):
        weblink.fetch("http://93.184.215.14/item", 1000, "*/*")


def test_size_limit(monkeypatch):
    import httpx

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 5000)), **kw),
    )
    with pytest.raises(LinkError, match="too large"):
        weblink.fetch("http://93.184.215.14/big", 1000, "*/*")


@pytest.fixture
def fake_web(monkeypatch):
    pages = {
        "https://www.examplehardware.com/p/123": (JSONLD_PAGE.encode(), "text/html; charset=utf-8"),
        "https://cdn.example.com/drill.jpg": (jpeg_bytes(size=(400, 300)), "image/jpeg"),
    }

    def fake_fetch(url, max_bytes, accept):
        if url not in pages:
            raise LinkError("The website returned an error (404)")
        body, ctype = pages[url]
        return body, ctype, url

    monkeypatch.setattr(weblink, "fetch", fake_fetch)
    return pages


def test_import_endpoint_prefills_without_saving(env, fake_web):
    u = env.admin
    res = u.ok("post", "/api/v1/link/import", json={"url": "https://www.examplehardware.com/p/123"})
    d = res["details"]
    assert d["name"] == "DEWALT 20V MAX Cordless Drill/Driver Kit" and d["price"] == "99.00"
    assert res["photo"]["mime_type"] == "image/jpeg" and res["image_error"] is None
    assert u.ok("get", "/api/v1/items")["total"] == 0  # nothing saved
    # The user reviews and saves; the link is kept on the product.
    item = u.item(d["name"], manufacturer=d["manufacturer"], model_number=d["model_number"], url=d["url"],
                  purchase_price=d["price"], currency=d["currency"], retailer=d["retailer"], photo_ids=[res["photo"]["id"]])
    assert item["product_url"] == "https://www.examplehardware.com/p/123"
    assert item["product"]["url"] == item["product_url"]
    assert item["photo"]["id"] == res["photo"]["id"]


def test_import_endpoint_errors_and_existing_match(env, fake_web):
    u = env.admin
    r = u.post("/api/v1/link/import", json={"url": "https://www.examplehardware.com/missing"})
    assert r.status_code == 422 and "404" in r.json()["detail"]
    u.item("Drill", barcode="076174123456")
    res = u.ok("post", "/api/v1/link/import", json={"url": "https://www.examplehardware.com/p/123", "download_image": False})
    assert res["existing_product"]["name"] == "Drill" and res["photo"] is None


def test_product_url_validation(env):
    r = env.admin.post("/api/v1/items", json={"name": "x", "url": "javascript:alert(1)"})
    assert r.status_code == 422


def test_ai_fallback_when_no_structured_data(ai_env, fake_ai, monkeypatch):
    page = b"<html><head><title>Item 4471 | Shop</title></head><body><p>The Acme TurboVac 3000 cordless vacuum.</p></body></html>"
    monkeypatch.setattr(weblink, "fetch", lambda url, m, a: (page, "text/html", url))
    fake_ai.responses.append(json.dumps({"name": "Cordless vacuum TurboVac 3000", "description": None, "category": "Cleaning",
                                         "manufacturer": "Acme", "model_number": "TurboVac 3000", "confidence": 0.8, "notes": None}))
    res = ai_env.admin.ok("post", "/api/v1/link/import", json={"url": "https://shop.example/4471"})
    assert res["details"]["name"] == "Cordless vacuum TurboVac 3000"
    assert res["details"]["manufacturer"] == "Acme" and res["details"]["source"] == "meta+ai"


def test_image_failure_returns_fixed_message(env, fake_web):
    fake_web["https://cdn.example.com/drill.jpg"] = (b"<html>not an image</html>", "text/html")
    res = env.admin.ok("post", "/api/v1/link/import", json={"url": "https://www.examplehardware.com/p/123"})
    assert res["photo"] is None
    assert res["image_error"] == "The product photo could not be downloaded or is not a supported image."
    assert res["details"]["name"]  # details still returned


# ---------------------------------------------------------------- DNS rebinding


def _fake_dns(monkeypatch, answers):
    """Resolve hostnames from ``answers`` (a list per name, consumed in order)."""
    import socket

    calls = []

    def getaddrinfo(host, port, *a, **kw):
        calls.append(host)
        queue = answers[host]
        ip = queue.pop(0) if len(queue) > 1 else queue[0]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(weblink.socket, "getaddrinfo", getaddrinfo)
    return calls


def _mock_client(monkeypatch, handler):
    import httpx

    real_client = httpx.Client
    seen = []

    def factory(**kw):
        assert kw.get("trust_env") is False, "environment proxies must not be used"
        return real_client(transport=httpx.MockTransport(lambda r: (seen.append(r), handler(r))[1]), **kw)

    monkeypatch.setattr(httpx, "Client", factory)
    return seen


def test_connects_to_the_checked_ip_not_a_second_lookup(monkeypatch):
    import httpx

    # First answer is public, any later answer would be private (rebinding).
    calls = _fake_dns(monkeypatch, {"shop.example": ["93.184.215.14", "127.0.0.1"]})
    seen = _mock_client(monkeypatch, lambda r: httpx.Response(200, text="<html><title>Ok</title></html>"))
    body, _ctype, final = weblink.fetch("https://shop.example/p/1", 10_000, "text/html")
    assert b"Ok" in body and final == "https://shop.example/p/1"
    assert calls == ["shop.example"]  # resolved exactly once
    req = seen[0]
    assert req.url.host == "93.184.215.14"  # connected to the validated address
    assert req.headers["host"] == "shop.example"
    assert req.extensions["sni_hostname"] == "shop.example"  # TLS checks the real name


def test_rebinding_on_redirect_hop_blocked(monkeypatch):
    import httpx

    _fake_dns(monkeypatch, {"shop.example": ["93.184.215.14"], "cdn.example": ["10.0.0.5"]})
    _mock_client(monkeypatch, lambda r: httpx.Response(302, headers={"location": "https://cdn.example/img.jpg"}))
    with pytest.raises(LinkError, match="private"):
        weblink.fetch("https://shop.example/p/1", 10_000, "*/*")


def test_mixed_public_private_answers_rejected(monkeypatch):
    import socket

    def getaddrinfo(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", port))]

    monkeypatch.setattr(weblink.socket, "getaddrinfo", getaddrinfo)
    with pytest.raises(LinkError, match="private"):
        weblink.fetch("https://shop.example/p/1", 10_000, "*/*")


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "169.254.169.254", "fe80::1", "224.0.0.1", "100.64.0.1",
                                "::ffff:192.168.0.1", "0.0.0.0", "fc00::1"])
def test_non_public_addresses_rejected(monkeypatch, ip):
    _fake_dns(monkeypatch, {"evil.example": [ip]})
    with pytest.raises(LinkError):
        weblink.fetch("http://evil.example/", 1000, "*/*")


def test_peer_address_mismatch_rejected():
    import httpx

    class Stream:
        def get_extra_info(self, key):
            return ("10.0.0.9", 443) if key == "server_addr" else None

    resp = httpx.Response(200, extensions={"network_stream": Stream()})
    with pytest.raises(LinkError, match="changed"):
        weblink._check_peer(resp, "93.184.215.14")
    weblink._check_peer(httpx.Response(200), "93.184.215.14")  # no stream info: nothing to check


def test_image_download_uses_pinned_fetch(monkeypatch):
    import httpx

    _fake_dns(monkeypatch, {"img.example": ["93.184.215.14", "192.168.0.2"]})
    seen = _mock_client(monkeypatch, lambda r: httpx.Response(200, content=jpeg_bytes(), headers={"content-type": "image/jpeg"}))
    assert weblink.download_image("https://img.example/a.jpg")[:2] == b"\xff\xd8"
    assert seen[0].url.host == "93.184.215.14"


def test_rate_limited(env, monkeypatch):
    monkeypatch.setattr(weblink, "import_from_url", lambda url, ai: (_ for _ in ()).throw(LinkError("nope")))
    codes = [env.admin.post("/api/v1/link/import", json={"url": "https://x.example/"}).status_code for _ in range(12)]
    assert codes[:10] == [422] * 10 and codes[10] == 429
