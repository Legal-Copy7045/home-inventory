"""Route-level authorization regression tests.

These walk the real route table, so a new endpoint is covered automatically:
every state-changing route must refuse read-only users, and the integration
API must refuse requests without the bearer token.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

PUBLIC = {"/api/health"}
# Mutations that are deliberately allowed for read-only users: none today.
# The setup-code claim authenticates but skips the read-only rule (so it still
# works with default_role: read_only); it refuses configured read-only users
# and is only offered while no administrator exists (tests in test_auth.py).
#
# Scanning a QR label or NFC tag is a lookup (POST only because the payload
# is a link); read-only users may scan.
READ_ONLY_ALLOWED: set[tuple[str, str]] = {
    ("POST", "/api/v1/setup/claim-admin"),
    ("POST", "/api/v1/qr/resolve"),
    ("POST", "/api/v1/tags/resolve"),
}


def _routes(app):
    # Every router in the api package, so a new module can't be missed.
    import importlib
    import pkgutil

    import home_inventory_server.api as api_pkg

    routers = [importlib.import_module(f"{api_pkg.__name__}.{m.name}").router for m in pkgutil.iter_modules(api_pkg.__path__)]
    routes = [r for rt in routers for r in rt.routes if isinstance(r, APIRoute)]
    assert len(routes) > 50, "route walk found too few routes"
    for r in routes:
        for method in r.methods - {"HEAD", "OPTIONS"}:
            yield method, r.path


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


@pytest.fixture
def viewer(env):
    v = env.user("u-viewer", "Vic Viewer")
    v.get("/api/v1/me")
    uid = next(u["id"] for u in env.admin.ok("get", "/api/v1/users") if u["name"] == "Vic Viewer")
    env.admin.ok("patch", f"/api/v1/users/{uid}", json={"role": "read_only"})
    return v


def test_every_mutating_route_refuses_read_only_users(env, viewer):
    checked = 0
    for method, path in _routes(env.ingress_app):
        if method == "GET" or (method, path) in READ_ONLY_ALLOWED or path in PUBLIC:
            continue
        r = getattr(viewer, method.lower())(_concrete(path))
        assert r.status_code in (401, 403), f"{method} {path} -> {r.status_code} for a read-only user"
        checked += 1
    assert checked > 40  # sanity: the walk found the API


def test_every_route_on_the_api_port_requires_the_token(env):
    raw = env.raw_api()
    for method, path in _routes(env.api_app):
        if path in PUBLIC:
            continue
        r = raw.request(method, _concrete(path), headers={"Authorization": "Bearer wrong-token"})
        assert r.status_code == 401, f"{method} {path} -> {r.status_code} without a valid token"


def test_ingress_refuses_untrusted_peers_on_every_route(env):
    env.settings.trusted_proxies[:] = ["172.30.32.2"]
    try:
        raw = env.raw_ingress()
        for method, path in _routes(env.ingress_app):
            if path in PUBLIC:
                continue
            r = raw.request(method, _concrete(path), headers={"X-Remote-User-Id": "u-admin"})
            assert r.status_code == 403, f"{method} {path} -> {r.status_code} from an untrusted peer"
    finally:
        env.settings.trusted_proxies[:] = ["testclient", "172.30.32.2"]


ADMIN_ONLY = [
    ("get", "/api/v1/users"),
    ("patch", "/api/v1/users/1"),
    ("patch", "/api/v1/settings"),
    ("get", "/api/v1/backup/export"),
    ("post", "/api/v1/backup/restore"),
]


@pytest.mark.parametrize("method,path", ADMIN_ONLY)
def test_admin_only_routes_refuse_members(env, method, path):
    sam = env.user("u-sam", "Sam Member")
    sam.get("/api/v1/me")
    assert getattr(sam, method)(path).status_code == 403
