"""The integration only ever talks to the App on this machine or your own
network: requests carry the App token and the person's Home Assistant user,
so a public address is refused before anything is sent."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.home_inventory.api import (
    InventoryClient,
    InventoryNotLocalError,
    ensure_local_url,
    local_host_kind,
)
from custom_components.home_inventory.const import DOMAIN


@pytest.mark.parametrize("url", [
    "http://homeinventory:8100", "http://a0d7b954-homeinventory:8100", "http://localhost:8100",
    "http://127.0.0.1:8100", "http://192.168.1.20:8100", "http://10.0.0.5", "http://172.30.32.1:8100",
    "http://[fe80::1]:8100", "http://nas.local:8100", "http://nas.lan", "http://box.home.arpa:8100",
])
def test_local_addresses_are_accepted(url):
    assert local_host_kind(url) == "local"


@pytest.mark.parametrize("url", [
    "http://8.8.8.8:8100", "https://1.1.1.1", "http://203.0.113.7:8100", "ftp://homeinventory", "http://user:pw@homeinventory:8100", "not a url",
])
def test_public_or_odd_addresses_are_refused(url):
    assert local_host_kind(url) == "refuse"


async def test_a_name_is_accepted_only_if_it_resolves_privately(monkeypatch):
    import asyncio

    loop = asyncio.get_running_loop()

    async def fake(host, *_a, **_k):
        ip = {"nas.example.com": "192.168.1.9", "evil.example.com": "203.0.113.7"}[host]
        return [(2, 1, 6, "", (ip, 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake)
    await ensure_local_url("http://nas.example.com:8100")
    with pytest.raises(InventoryNotLocalError):
        await ensure_local_url("http://evil.example.com:8100")


async def test_nothing_is_sent_to_a_public_address(hass: HomeAssistant):
    sent = []

    class Session:
        def request(self, *a, **k):
            sent.append(a)
            raise AssertionError("must not send")

        get = request

    client = InventoryClient(Session(), "http://8.8.8.8:8100", "secret-token")
    with pytest.raises(InventoryNotLocalError):
        await client.request("GET", "search", params={"q": "drill"})
    with pytest.raises(InventoryNotLocalError):
        await client.health()
    assert sent == []


async def test_setup_refuses_a_public_address(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "http://8.8.8.8:8100", "api_token": "secret"})
    assert result["errors"] == {"base": "not_local"}


@pytest.mark.parametrize("url, kind", [
    ("http://134744072:8100", "refuse"),      # 8.8.8.8 written as one number
    ("http://0x08080808:8100", "refuse"),
    ("http://010.0.0.1:8100", "refuse"),      # octal: 8.0.0.1
    ("http://3232235777:8100", "local"),      # 192.168.1.1
    ("http://0x7f000001:8100", "local"),      # 127.0.0.1
])
def test_numeric_ipv4_spellings_count_as_their_address(url, kind):
    assert local_host_kind(url) == kind


async def test_every_request_is_checked_and_pinned_to_the_checked_address(monkeypatch):
    """A name is resolved for each request, and the request goes to the checked
    address with the name in Host, so DNS can't change between check and send."""
    import asyncio

    loop = asyncio.get_running_loop()
    answers = iter(["192.168.1.9", "203.0.113.7"])

    async def fake(host, *_a, **_k):
        return [(2, 1, 6, "", (next(answers), 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake)
    sent = []

    class Resp:
        status = 200

        async def text(self):
            return "{}"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class Session:
        def request(self, method, url, **kw):
            sent.append((url, kw["headers"].get("Host")))
            return Resp()

    client = InventoryClient(Session(), "http://nas.example.com:8100", "tok")
    await client.request("GET", "stats")
    assert sent == [("http://192.168.1.9:8100/api/v1/stats", "nas.example.com:8100")]
    with pytest.raises(InventoryNotLocalError):  # the name now points outside: refused, nothing sent
        await client.request("GET", "stats")
    assert len(sent) == 1


async def test_https_name_keeps_its_certificate_check(monkeypatch):
    import asyncio

    loop = asyncio.get_running_loop()

    async def fake(host, *_a, **_k):
        return [(2, 1, 6, "", ("192.168.1.9", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake)
    client = InventoryClient(object(), "https://nas.example.com", "tok")
    base, headers, extra = await client._target()
    assert base == "https://192.168.1.9" and headers == {"Host": "nas.example.com"}
    assert extra == {"server_hostname": "nas.example.com"}


async def test_photo_view_forwards_only_the_user_id():
    from types import SimpleNamespace

    from custom_components.home_inventory.photo import _user_id

    assert _user_id({"hass_user": SimpleNamespace(id="abc123", name="Sam", is_admin=True)}) == "abc123"
    assert _user_id({}) is None
