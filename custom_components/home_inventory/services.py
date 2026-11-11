"""Home Assistant actions (services) backed by the App's API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import InventoryApiError, InventoryClient, InventoryError
from .const import DOMAIN


def _client(hass: HomeAssistant) -> InventoryClient:
    entries = hass.data.get(DOMAIN) or {}
    for runtime in entries.values():
        return runtime.client
    raise HomeAssistantError("Home Inventory is not set up")


async def _user(hass: HomeAssistant, call: ServiceCall):
    if call.context.user_id:
        return await hass.auth.async_get_user(call.context.user_id)
    return None


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    out = {
        "item_id": item["id"],
        "name": item["display_name"],
        "quantity": item["quantity"],
        "location": item["placement"]["path"],
        "container": (item["placement"].get("container") or {}).get("label"),
        "status": item["status"],
    }
    if item.get("home_placement"):
        out["normally_in"] = item["home_placement"]["path"]
    if item.get("loan"):
        out["lent_to"] = item["loan"]["borrower"]
        out["due_back"] = item["loan"]["expected_return"]
    if item.get("expires_on"):
        out["expires_on"] = item["expires_on"]
    return out


async def _call(hass: HomeAssistant, method: str, path: str, **kwargs) -> Any:
    try:
        return await _client(hass).request(method, path, **kwargs)
    except InventoryApiError as err:
        if err.status in (404, 409, 422):
            raise ServiceValidationError(str(err)) from err
        raise HomeAssistantError(str(err)) from err
    except InventoryError as err:
        raise HomeAssistantError(str(err)) from err


async def _container_id(hass: HomeAssistant, ref: str | int, user) -> int:
    c = await _call(hass, "GET", "containers/lookup", params={"ref": str(ref)}, user=user)
    return c["id"]


async def _location_id(hass: HomeAssistant, ref: str | int, user) -> int:
    if isinstance(ref, int) or str(ref).isdigit():
        return int(ref)
    locs = await _call(hass, "GET", "locations", user=user)
    want = str(ref).strip().lower()
    for loc in locs:
        if loc["path"].lower() == want or loc["name"].lower() == want:
            return loc["id"]
    raise ServiceValidationError(f"No location called '{ref}'")


SEARCH_SCHEMA = vol.Schema({vol.Required("query"): cv.string, vol.Optional("limit", default=10): vol.All(int, vol.Range(1, 100))})
LOCATION_SCHEMA = vol.Schema({vol.Required("name"): cv.string})
CONTENTS_SCHEMA = vol.Schema({vol.Required("container"): cv.string})
MOVE_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required("item_id"): vol.Coerce(int),
        vol.Exclusive("container", "target"): cv.string,
        vol.Exclusive("location", "target"): cv.string,
        vol.Optional("quantity"): vol.All(int, vol.Range(min=1)),
        vol.Optional("temporary", default=False): cv.boolean,
    }
)
MOVE_CONTAINER_SCHEMA = vol.Schema(
    {
        vol.Required("container"): cv.string,
        vol.Exclusive("into_container", "target"): cv.string,
        vol.Exclusive("location", "target"): cv.string,
        vol.Optional("temporary", default=False): cv.boolean,
    }
)
ADJUST_SCHEMA = vol.Schema({vol.Required("item_id"): vol.Coerce(int), vol.Required("delta"): vol.All(int, vol.Range(-10000, 10000))})


async def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "search"):
        return

    async def search(call: ServiceCall) -> ServiceResponse:
        user = await _user(hass, call)
        r = await _call(hass, "GET", "search", params={"q": call.data["query"], "limit": call.data["limit"]}, user=user)
        return {"answer": r["answer"], "total_quantity": r["total_quantity"], "matches": r["total_matches"],
                "items": [_brief(i) for i in r["results"]]}

    async def get_item_location(call: ServiceCall) -> ServiceResponse:
        user = await _user(hass, call)
        r = await _call(hass, "GET", "search", params={"q": call.data["name"], "limit": 10}, user=user)
        return {"answer": r["answer"], "found": bool(r["results"]), "items": [_brief(i) for i in r["results"]]}

    async def get_container_contents(call: ServiceCall) -> ServiceResponse:
        user = await _user(hass, call)
        c = await _call(hass, "GET", "containers/lookup", params={"ref": call.data["container"]}, user=user)
        return {
            "container": c["label"],
            "location": (c.get("placement") or {}).get("path"),
            "items": [_brief(i) for i in c.get("items", [])],
            "containers": [{"name": cc["label"], "item_count": cc["item_count"]} for cc in c.get("containers", [])],
        }

    async def move_item(call: ServiceCall) -> ServiceResponse:
        user = await _user(hass, call)
        body: dict[str, Any] = {"temporary": call.data["temporary"], "quantity": call.data.get("quantity")}
        if "container" in call.data:
            body["container_id"] = await _container_id(hass, call.data["container"], user)
        elif "location" in call.data:
            body["location_id"] = await _location_id(hass, call.data["location"], user)
        else:
            raise ServiceValidationError("Give a container or a location to move to")
        r = await _call(hass, "POST", f"items/{call.data['item_id']}/move", json_body=body, user=user)
        return _brief(r)

    async def move_container(call: ServiceCall) -> ServiceResponse:
        user = await _user(hass, call)
        cid = await _container_id(hass, call.data["container"], user)
        body: dict[str, Any] = {"temporary": call.data["temporary"]}
        if "into_container" in call.data:
            body["parent_container_id"] = await _container_id(hass, call.data["into_container"], user)
        elif "location" in call.data:
            body["location_id"] = await _location_id(hass, call.data["location"], user)
        else:
            raise ServiceValidationError("Give a container or a location to move to")
        r = await _call(hass, "POST", f"containers/{cid}/move", json_body=body, user=user)
        return {"container": r["label"], "location": r["path"]}

    async def adjust_quantity(call: ServiceCall) -> ServiceResponse:
        user = await _user(hass, call)
        r = await _call(hass, "POST", f"items/{call.data['item_id']}/adjust", json_body={"delta": call.data["delta"], "note": "Home Assistant action"}, user=user)
        return _brief(r)

    async def refresh(call: ServiceCall) -> None:
        for runtime in (hass.data.get(DOMAIN) or {}).values():
            await runtime.coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "search", search, SEARCH_SCHEMA, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "get_item_location", get_item_location, LOCATION_SCHEMA, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "get_container_contents", get_container_contents, CONTENTS_SCHEMA, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "move_item", move_item, MOVE_ITEM_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "move_container", move_container, MOVE_CONTAINER_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "adjust_quantity", adjust_quantity, ADJUST_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "refresh", refresh)


def async_unload_services(hass: HomeAssistant) -> None:
    for name in ("search", "get_item_location", "get_container_contents", "move_item", "move_container", "adjust_quantity", "refresh"):
        hass.services.async_remove(DOMAIN, name)
