"""Retailer providers: what each shop can actually do.

Research (November 2026, see docs/replenishment.md):

* **Amazon**: the documented *Add to Cart form*
  (``/gp/aws/cart/add.html?ASIN.1=…&Quantity.1=…``) opens Amazon, which asks
  the shopper to confirm before adding the items.  There is no API to change
  or read a shopper's cart: cart operations were removed from PA-API 5, which
  has itself been retired; its Creators API successor needs an approved,
  selling Associates account and has no cart operations either.
* **Lowe's** and **Tractor Supply**: no public API (Lowe's APIs are for
  approved partners; Tractor Supply only runs an affiliate program).  We can
  open the exact product page or a search for the item.

Nothing here scrapes pages, stores retailer credentials or automates a
browser.  ``cart_handoff`` only ever *opens* a link; whether anything ended
up in the cart is confirmed by the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlparse


@dataclass(frozen=True)
class Capabilities:
    product_url: bool = True
    search_url: bool = True
    cart_handoff: bool = False  # open a link that offers to add items to the cart
    cart_api: bool = False  # change the cart directly (none of the supported retailers)
    cart_inspection: bool = False
    price_lookup: bool = False
    availability: bool = False

    def as_dict(self) -> dict[str, bool]:
        return dict(self.__dict__)


class Provider:
    key = "other"
    name = "Other shop"
    domains: tuple[str, ...] = ()
    capabilities = Capabilities(search_url=False)
    id_label = "Product number"

    def validate_url(self, url: str | None) -> str | None:
        if not url:
            return None
        p = urlparse(url.strip())
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("Enter a full web address starting with https://")
        if self.domains and not any(p.hostname == d or p.hostname.endswith("." + d) for d in self.domains):
            raise ValueError(f"That isn't a {self.name} link")
        return url.strip()

    def normalise_id(self, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    def id_from_url(self, url: str | None) -> str | None:
        return None

    def product_url(self, source: Any, settings: dict[str, str] | None = None) -> str | None:
        return source.url

    def search_url(self, term: str, settings: dict[str, str] | None = None) -> str | None:
        return None

    def cart_handoff_url(self, lines: list[tuple[Any, int]], settings: dict[str, str]) -> str | None:
        return None


class Amazon(Provider):
    key = "amazon"
    name = "Amazon"
    domains = ("amazon.com", "amazon.ca", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.it", "amazon.es",
               "amazon.com.au", "amazon.co.jp", "amazon.in", "amazon.com.mx", "a.co", "amzn.to")
    capabilities = Capabilities(cart_handoff=True)
    id_label = "ASIN"
    ASIN = re.compile(r"^[A-Z0-9]{10}$")
    URL_ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?=[/?#]|$)", re.I)

    def normalise_id(self, value: str | None) -> str | None:
        v = (value or "").strip().upper()
        if not v:
            return None
        if not self.ASIN.match(v):
            raise ValueError("An Amazon ASIN is 10 letters and digits, e.g. B000BQ7JXA")
        return v

    def id_from_url(self, url: str | None) -> str | None:
        m = self.URL_ASIN.search(url or "")
        return m.group(1).upper() if m else None

    @staticmethod
    def _domain(settings: dict[str, str]) -> str:
        d = (settings.get("amazon_domain") or "amazon.com").strip().lower()
        return d if re.fullmatch(r"(www\.)?amazon\.[a-z.]{2,8}", d) else "amazon.com"

    def product_url(self, source: Any, settings: dict[str, str] | None = None) -> str | None:
        if source.url:
            return source.url
        if source.retailer_product_id:
            return f"https://www.{self._domain(settings or {}).removeprefix('www.')}/dp/{source.retailer_product_id}"
        return None

    def search_url(self, term: str, settings: dict[str, str] | None = None) -> str | None:
        return f"https://www.{self._domain(settings or {}).removeprefix('www.')}/s?k={quote(term)}"

    def cart_handoff_url(self, lines: list[tuple[Any, int]], settings: dict[str, str]) -> str | None:
        """Amazon's Add to Cart form: Amazon shows the items and asks the shopper to confirm."""
        params: list[tuple[str, str]] = []
        n = 0
        for source, qty in lines:
            if not source.retailer_product_id:
                continue
            n += 1
            params += [(f"ASIN.{n}", source.retailer_product_id), (f"Quantity.{n}", str(qty))]
        if not n:
            return None
        tag = (settings.get("amazon_associate_tag") or "").strip()
        if tag and re.fullmatch(r"[A-Za-z0-9-]{3,40}", tag):
            params.append(("AssociateTag", tag))
        return f"https://www.{self._domain(settings).removeprefix('www.')}/gp/aws/cart/add.html?{urlencode(params)}"


class Lowes(Provider):
    key = "lowes"
    name = "Lowe's"
    domains = ("lowes.com",)
    capabilities = Capabilities()
    id_label = "Item #"

    def id_from_url(self, url: str | None) -> str | None:
        m = re.search(r"/pd/[^/]+/(\d{4,12})(?=[/?#]|$)", url or "")
        return m.group(1) if m else None

    def search_url(self, term: str, settings: dict[str, str] | None = None) -> str | None:
        return f"https://www.lowes.com/search?searchTerm={quote(term)}"


class TractorSupply(Provider):
    key = "tractor_supply"
    name = "Tractor Supply"
    domains = ("tractorsupply.com",)
    capabilities = Capabilities()
    id_label = "SKU"

    def id_from_url(self, url: str | None) -> str | None:
        m = re.search(r"[?&]sku=(\d{4,12})", url or "") or re.search(r"/product/[^?#]*?-(\d{6,12})(?=[/?#]|$)", url or "")
        return m.group(1) if m else None

    def search_url(self, term: str, settings: dict[str, str] | None = None) -> str | None:
        return f"https://www.tractorsupply.com/tsc/search/{quote(term)}"


PROVIDERS: dict[str, Provider] = {p.key: p for p in (Amazon(), Lowes(), TractorSupply(), Provider())}


def provider(key: str) -> Provider:
    return PROVIDERS.get(key, PROVIDERS["other"])


def providers_out() -> list[dict[str, Any]]:
    return [{"key": p.key, "name": p.name, "id_label": p.id_label, "capabilities": p.capabilities.as_dict()}
            for p in PROVIDERS.values()]


def link_for(source: Any, term: str, settings: dict[str, str]) -> tuple[str | None, str]:
    """Best link for one source: ('url', 'product'|'search'|'none')."""
    prov = provider(source.retailer)
    url = prov.product_url(source, settings)
    if url:
        return url, "product"
    search = prov.search_url(source.retailer_product_id or source.gtin or term, settings) if prov.capabilities.search_url else None
    return (search, "search") if search else (None, "none")
