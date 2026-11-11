"""Identity, trust boundaries and roles."""

from fastapi.testclient import TestClient

from home_inventory_server.models import Role


def test_ingress_requires_trusted_peer(env):
    # A client that is not the Supervisor's Ingress proxy is rejected even
    # with forged identity headers.
    env.settings.trusted_proxies[:] = ["172.30.32.2"]
    r = env.raw_ingress().get("/api/v1/me", headers={"X-Remote-User-Id": "u-admin"})
    assert r.status_code == 403
    env.settings.trusted_proxies[:] = ["testclient"]


def test_ingress_requires_identity_header(env):
    r = env.raw_ingress().get("/api/v1/me")
    assert r.status_code == 401


def test_health_is_public_and_minimal(env):
    r = env.raw_ingress().get("/api/health")
    assert r.status_code == 200 and set(r.json()) == {"status", "version"}


def test_first_user_admin_then_members(env):
    me = env.admin.ok("get", "/api/v1/me")
    assert me["role"] == "admin" and me["name"] == "Alex Admin"
    sam = env.user("u-sam", "Sam Member")
    assert sam.ok("get", "/api/v1/me")["role"] == "member"
    # members can edit inventory but not administer
    sam.location("Loft")
    assert sam.get("/api/v1/users").status_code == 403
    assert sam.patch("/api/v1/settings", json={"currency": "GBP"}).status_code == 403
    assert sam.get("/api/v1/backup/export").status_code == 403


def test_read_only_role(env):
    viewer = env.user("u-kid", "Kid Viewer")
    viewer.get("/api/v1/me")
    users = env.admin.ok("get", "/api/v1/users")
    kid = next(u for u in users if u["name"] == "Kid Viewer")
    env.admin.ok("patch", f"/api/v1/users/{kid['id']}", json={"role": "read_only"})
    assert viewer.ok("get", "/api/v1/me")["role"] == "read_only"
    assert viewer.get("/api/v1/items").status_code == 200
    assert viewer.post("/api/v1/locations", json={"name": "x"}).status_code == 403


def test_admin_cannot_demote_self(env):
    me = env.admin.ok("get", "/api/v1/me")
    assert env.admin.patch(f"/api/v1/users/{me['id']}", json={"role": "member"}).status_code == 409


def test_configured_admin_users(tmp_path):
    from tests.conftest import Env

    e = Env(tmp_path, admin_users=["jo"], read_only_users=["guest"])
    jo = e.user("u-jo", "Jo Bloggs")
    assert jo.ok("get", "/api/v1/me")["role"] == "admin"
    guest = e.user("u-guest", "Guest User")
    assert guest.ok("get", "/api/v1/me")["role"] == "read_only"
    e.state.db.dispose()


def test_ha_admin_directory_used(env, monkeypatch):
    monkeypatch.setattr(env.state.admin_directory, "is_admin", lambda ha_id: ha_id == "u-owner")
    owner = env.user("u-owner", "Owner Person")
    assert owner.ok("get", "/api/v1/me")["role"] == "admin"
    other = env.user("u-other", "Other Person")
    assert other.ok("get", "/api/v1/me")["role"] == "member"


def test_audit_identifies_user(env):
    sam = env.user("u-sam", "Sam Member")
    item = sam.item("Torch")
    hist = env.admin.ok("get", "/api/v1/history", params={"entity_type": "item", "entity_id": item["id"]})
    assert hist["events"][0]["user"] == "Sam Member"


def test_api_channel_requires_token(env):
    api: TestClient = env.raw_api()
    assert api.get("/api/v1/stats").status_code == 401
    assert api.get("/api/v1/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = env.integration().get("/api/v1/stats")
    assert r.status_code == 200
    # identity headers alone don't work on the API port
    assert api.get("/api/v1/stats", headers={"X-Remote-User-Id": "u-admin"}).status_code == 401


def test_api_channel_acts_for_ha_user(env):
    svc = env.integration(**{"X-Remote-User-Id": "ha-123", "X-Remote-User-Display-Name": "Pat", "X-Remote-User-Is-Admin": "false"})
    item = svc.item("Spade")
    hist = env.admin.ok("get", "/api/v1/history", params={"entity_type": "item", "entity_id": item["id"]})
    assert hist["events"][0]["user"] == "Pat"
    sys_client = env.integration()
    assert sys_client.ok("get", "/api/v1/me")["name"] == "Home Assistant"


def test_api_docs_not_on_ingress(env):
    assert env.raw_ingress().get("/api/openapi.json").status_code == 404
    assert env.raw_api().get("/api/openapi.json").status_code == 200


def test_security_headers(env):
    r = env.admin.get("/api/v1/me")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]


def test_role_enum_values():
    assert {r.value for r in Role} == {"admin", "member", "read_only"}


def test_ui_static_files_are_confined(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from home_inventory_server import main
    from tests.conftest import Env

    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>Home Inventory</title>")
    (ui / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("top secret")
    monkeypatch.setattr(main, "FRONTEND_DIR", ui)
    e = Env(tmp_path / "env")
    client = TestClient(main.create_app(e.state, "ingress"))
    r = client.get("/")
    assert r.status_code == 200 and "Home Inventory" in r.text
    assert r.headers["cache-control"] == "no-cache"  # updates are picked up
    assert client.get("/assets/app.js").text == "console.log(1)"
    for evil in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt", "/..%2fsecret.txt"):
        r = client.get(evil)
        assert "top secret" not in r.text, evil
    # API routes still win over the UI mount
    assert client.get("/api/health").json()["status"] == "ok"
    e.state.db.dispose()
