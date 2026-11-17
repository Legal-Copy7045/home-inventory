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
