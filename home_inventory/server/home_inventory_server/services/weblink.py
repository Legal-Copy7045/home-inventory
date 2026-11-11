"""Import product details from a web page.

Most shops publish machine-readable product data. In order of preference:

1. schema.org ``Product`` in JSON-LD (name, brand, model, MPN, GTIN, SKU,
   image, price, currency, seller, category);
2. Open Graph / product meta tags (``og:title``, ``og:image``,
   ``product:price:amount``...);
3. the page ``<title>`` and meta description;
4. optionally, AI extraction from the page text when 1–2 found nothing.

Results only pre-fill the form; the user reviews before saving.

Safety: the request is made from the user's Home Assistant host, so a link
must not be usable to probe the local network (SSRF).  Every hop (including
redirects and the image download) must resolve exclusively to public IP
addresses; responses are size- and time-limited.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 HomeInventory/0.1"
)
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = 12.0


class LinkError(ValueError):
    """User-facing problem with the link."""


# ---------------------------------------------------------------------------
# Fetching with SSRF protection
# ---------------------------------------------------------------------------


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LinkError("Only http:// and https:// links are supported")
    if parsed.username or parsed.password:
        raise LinkError("Links with embedded credentials are not supported")
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as err:
        raise LinkError(f"Could not find the website {host}") from err
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global or addr.is_multicast:
            raise LinkError("Links to local or private network addresses are not allowed")


def fetch(url: str, max_bytes: int, accept: str) -> tuple[bytes, str, str]:
    """GET ``url`` following redirects safely; returns (body, content type, final url)."""
    current = url
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                                                        "Accept-Language": "en-US,en;q=0.8"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_host(current)
            try:
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise LinkError("The website sent an invalid redirect")
                        current = urljoin(current, location)
                        continue
                    if resp.status_code in (401, 403, 429, 503):
                        raise LinkError(
                            "The website refused automatic reading (it may block bots). Enter the details by hand, "
                            "or try the manufacturer's page instead of the shop's."
                        )
                    if resp.status_code >= 400:
                        raise LinkError(f"The website returned an error ({resp.status_code})")
                    declared = int(resp.headers.get("content-length") or 0)
                    if declared > max_bytes:
                        raise LinkError("The page is too large to read")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise LinkError("The page is too large to read")
                        chunks.append(chunk)
                    return b"".join(chunks), resp.headers.get("content-type", ""), current
            except httpx.TimeoutException as err:
                raise LinkError("The website took too long to respond") from err
            except httpx.HTTPError as err:
                raise LinkError(f"Could not load the page: {err.__class__.__name__}") from err
    raise LinkError("Too many redirects")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.jsonld: list[str] = []
        self.title = ""
        self.text: list[str] = []
        self._in: str | None = None
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"]
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in, self._buf = "jsonld", []
        elif tag == "title" and not self.title:
            self._in, self._buf = "title", []
        elif tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self._skip += 1

    def handle_endtag(self, tag):
        if self._in == "jsonld" and tag == "script":
            self.jsonld.append("".join(self._buf))
            self._in = None
        elif self._in == "title" and tag == "title":
            self.title = " ".join("".join(self._buf).split())
            self._in = None
        elif tag in ("script", "style", "noscript", "svg", "nav", "footer", "header") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)
        elif not self._skip:
            t = data.strip()
            if t:
                self.text.append(t)


def _walk(node: Any):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "itemListElement", "item"):
            if key in node:
                yield from _walk(node[key])


def _is_product(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.split("/")[-1] in ("Product", "ProductModel", "IndividualProduct") for x in types)


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("name") or v.get("@value") or v.get("url")
    if isinstance(v, list):
        v = next((_text(x) for x in v if _text(x)), None)
    if v is None:
        return None
    s = " ".join(unescape(str(v)).split())
    s = re.sub(r"<[^>]+>", "", s)
    return s or None


def _image(v: Any) -> str | None:
    if isinstance(v, list):
        for x in v:
            r = _image(x)
            if r:
                return r
        return None
    if isinstance(v, dict):
        return _text(v.get("url") or v.get("contentUrl"))
    return _text(v)


def _offer(v: Any) -> dict[str, Any]:
    offers = v if isinstance(v, list) else [v]
    for o in offers:
        if not isinstance(o, dict):
            continue
        if "offers" in o and isinstance(o["offers"], (dict, list)):  # AggregateOffer
            inner = _offer(o["offers"])
            if inner:
                return inner
        price = o.get("price") or o.get("lowPrice")
        if price is None and isinstance(o.get("priceSpecification"), dict):
            price = o["priceSpecification"].get("price")
        if price is not None:
            return {
                "price": _price(price),
                "currency": _text(o.get("priceCurrency"))
                or (_text(o["priceSpecification"].get("priceCurrency")) if isinstance(o.get("priceSpecification"), dict) else None),
                "seller": _text(o.get("seller")),
            }
    return {}


def _price(v: Any) -> str | None:
    """Normalise "1,299.99", "1.299,99", "$12", 12.5 -> "1299.99", "1299.99", "12", "12.5"."""
    if v is None:
        return None
    m = re.search(r"\d[\d.,]*", str(v))
    if not m:
        return None
    s = m.group(0).rstrip(".,")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else s.replace(",", "")
    elif s.count(".") > 1:
        head, _, tail = s.rpartition(".")
        s = f"{head.replace('.', '')}.{tail}" if len(tail) in (1, 2) else s.replace(".", "")
    return s or None


def _clean_title(title: str | None, site: str | None) -> str | None:
    if not title:
        return None
    t = title
    for sep in (" | ", " - ", " – ", " — ", " : "):
        parts = t.split(sep)
        if len(parts) > 1:
            last = parts[-1].strip().lower()
            if (site and site.lower() in last) or len(parts[-1]) < 25:
                t = sep.join(parts[:-1])
    return t.strip() or None


def _site_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    host = re.sub(r"^(www\d?|m|shop|store)\.", "", host)
    base = host.split(".")[0] if host else ""
    return base[:1].upper() + base[1:] if base else ""


@dataclass
class ProductPage:
    url: str
    name: str | None = None
    description: str | None = None
    manufacturer: str | None = None
    model_number: str | None = None
    barcode: str | None = None
    sku: str | None = None
    category: str | None = None
    image_url: str | None = None
    price: str | None = None
    currency: str | None = None
    retailer: str | None = None
    source: str = "none"  # json-ld | meta | ai | none
    text: str = field(default="", repr=False)

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "text"}
        return d


def parse_page(html: str, url: str) -> ProductPage:
    p = _PageParser()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML; use what was parsed
        pass
    page = ProductPage(url=url)
    meta = p.meta
    site = _text(meta.get("og:site_name"))

    product: dict | None = None
    for raw in p.jsonld:
        try:
            data = json.loads(raw.strip().rstrip(";"))
        except ValueError:
            continue
        product = next((n for n in _walk(data) if _is_product(n)), None)
        if product:
            break

    if product:
        page.source = "json-ld"
        page.name = _text(product.get("name"))
        page.description = _text(product.get("description"))
        page.manufacturer = _text(product.get("brand")) or _text(product.get("manufacturer"))
        page.model_number = _text(product.get("model")) or _text(product.get("mpn"))
        page.sku = _text(product.get("sku"))
        for key in ("gtin13", "gtin12", "gtin14", "gtin8", "gtin", "isbn"):
            if product.get(key):
                page.barcode = re.sub(r"\D", "", str(product[key])) or None
                if page.barcode:
                    break
        page.category = _text(product.get("category"))
        page.image_url = _image(product.get("image"))
        offer = _offer(product.get("offers"))
        page.price, page.currency = offer.get("price"), offer.get("currency")
        page.retailer = offer.get("seller")

    # Fill gaps from meta tags
    if not page.name:
        page.name = _clean_title(_text(meta.get("og:title")) or _text(meta.get("twitter:title")) or p.title, site)
        if page.name and page.source == "none":
            page.source = "meta"
    page.description = page.description or _text(meta.get("og:description")) or _text(meta.get("description"))
    page.manufacturer = page.manufacturer or _text(meta.get("product:brand")) or _text(meta.get("og:brand"))
    page.image_url = page.image_url or _text(meta.get("og:image")) or _text(meta.get("og:image:url")) or _text(meta.get("twitter:image"))
    page.price = page.price or _price(meta.get("product:price:amount") or meta.get("og:price:amount"))
    page.currency = page.currency or _text(meta.get("product:price:currency") or meta.get("og:price:currency"))
    page.retailer = page.retailer or site or _site_name(url)
    if page.image_url:
        page.image_url = urljoin(url, page.image_url)
    if page.description and len(page.description) > 2000:
        page.description = page.description[:1997].rstrip() + "…"
    if page.currency:
        page.currency = page.currency.upper()[:3]
    page.text = " ".join(p.text)[:8000]
    return page


def import_from_url(url: str, ai=None) -> ProductPage:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    body, ctype, final_url = fetch(url, MAX_PAGE_BYTES, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5")
    if "html" not in ctype.lower() and not body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        raise LinkError("That link is not a web page")
    charset = re.search(r"charset=([\w-]+)", ctype)
    try:
        html = body.decode(charset.group(1) if charset else "utf-8", errors="replace")
    except LookupError:
        html = body.decode("utf-8", errors="replace")
    page = parse_page(html, final_url)
    # AI fallback when the page has no structured product data.
    if page.source != "json-ld" and ai is not None and page.text:
        try:
            res = ai.extract_product_from_text(page.text, final_url)
            d = res.data
            page.name = d.get("name") or page.name
            page.manufacturer = page.manufacturer or d.get("manufacturer")
            page.model_number = page.model_number or d.get("model_number")
            page.category = page.category or d.get("category")
            page.description = page.description or d.get("description")
            page.source = "ai" if page.source == "none" else f"{page.source}+ai"
        except Exception:  # noqa: BLE001 - AI is optional; keep what we parsed
            pass
    if not page.name:
        raise LinkError("No product details were found on that page")
    return page


def download_image(url: str) -> bytes:
    body, _ctype, _ = fetch(url, MAX_IMAGE_BYTES, "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8")
    return body
