"""Identity, trust boundaries and roles."""

from fastapi.testclient import TestClient

from home_inventory_server.models import Role
from tests.conftest import Env


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


def test_configured_admin_then_members(env):
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


def test_api_docs_only_while_debugging_and_never_on_ingress(env, tmp_path):
    assert env.raw_ingress().get("/api/openapi.json").status_code == 404
    assert env.raw_api().get("/api/openapi.json").status_code == 404
    dbg = Env(tmp_path / "debug", log_level="debug")
    assert dbg.raw_ingress().get("/api/openapi.json").status_code == 404
    assert dbg.raw_api().get("/api/openapi.json").status_code == 200
    dbg.state.db.dispose()


def test_data_is_never_cached_and_not_embeddable(env):
    r = env.admin.get("/api/v1/me")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert "server" not in {k.lower() for k in r.headers} or "uvicorn" not in r.headers.get("server", "")


def test_qr_link_address_must_be_a_web_address(env):
    bad = env.admin.patch("/api/v1/settings", json={"qr_base_url": "javascript:alert(1)"})
    assert bad.status_code == 422
    ok = env.admin.ok("patch", "/api/v1/settings", json={"qr_base_url": "https://ha.example.org/app/"})
    assert ok["qr_base_url"] == "https://ha.example.org/app"


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


# ---------------------------------------------------------------------------
# administrator bootstrap (fail closed)
# ---------------------------------------------------------------------------


def _bare_env(tmp_path, **kw):
    from tests.conftest import Env

    return Env(tmp_path, configured_admin=False, **kw)


def _code(e) -> str:
    return e.state.admin_bootstrap._code


def test_ha_lookup_unavailable_never_promotes_first_user(tmp_path):
    e = _bare_env(tmp_path)
    first = e.user("u-first", "First Person")
    me = first.ok("get", "/api/v1/me")
    assert me["role"] == "member" and me["is_admin"] is False
    assert me["admin_setup"] is True  # setup code is offered instead
    assert first.get("/api/v1/users").status_code == 403


def test_configured_admin_arriving_first(tmp_path):
    e = _bare_env(tmp_path, admin_users=["jo"])
    jo = e.user("u-jo", "Jo Bloggs")
    me = jo.ok("get", "/api/v1/me")
    assert me["role"] == "admin" and me["admin_setup"] is False


def test_configured_read_only_user_arriving_first(tmp_path):
    e = _bare_env(tmp_path, read_only_users=["guest"])
    guest = e.user("u-guest", "Guest Viewer")
    me = guest.ok("get", "/api/v1/me")
    assert me["role"] == "read_only"
    # even with the right code, a configured read-only user can't claim admin
    r = guest.post("/api/v1/setup/claim-admin", json={"code": _code(e)})
    assert r.status_code == 403
    assert guest.ok("get", "/api/v1/me")["role"] == "read_only"


def test_member_first_then_setup_code_claim(tmp_path):
    e = _bare_env(tmp_path)
    sam = e.user("u-sam", "Sam Member")
    assert sam.ok("get", "/api/v1/me")["role"] == "member"
    assert sam.post("/api/v1/setup/claim-admin", json={"code": "WRONG-CODE-XXXX"}).status_code == 403
    code = _code(e)
    sam.ok("post", "/api/v1/setup/claim-admin", json={"code": code.lower()})
    me = sam.ok("get", "/api/v1/me")
    assert me["role"] == "admin" and me["admin_setup"] is False
    # the code is single use and no longer offered
    other = e.user("u-other", "Other Person")
    assert other.post("/api/v1/setup/claim-admin", json={"code": code}).status_code == 409
    assert other.ok("get", "/api/v1/me")["role"] == "member"


def test_setup_code_rotates_after_failed_attempts(tmp_path):
    e = _bare_env(tmp_path)
    sam = e.user("u-sam", "Sam Member")
    sam.get("/api/v1/me")
    first = _code(e)
    for _ in range(5):
        assert sam.post("/api/v1/setup/claim-admin", json={"code": "AAAA-BBBB-CCCC"}).status_code == 403
    assert _code(e) != first
    assert sam.post("/api/v1/setup/claim-admin", json={"code": first}).status_code == 403


def test_setup_code_not_offered_through_integration_api(tmp_path):
    e = _bare_env(tmp_path)
    integ = e.integration(**{"X-Remote-User-Id": "u-int", "X-Remote-User-Name": "int"})
    assert integ.ok("get", "/api/v1/me")["admin_setup"] is False
    assert integ.post("/api/v1/setup/claim-admin", json={"code": "AAAA-BBBB-CCCC"}).status_code == 403


class _Directory:
    def __init__(self, answers):
        self.answers = answers
        self.available = True

    def is_admin(self, ha_id):
        return self.answers.get(ha_id) if self.available else None


def test_existing_admin_kept_when_home_assistant_unavailable(tmp_path):
    e = _bare_env(tmp_path)
    directory = _Directory({"u-owner": True, "u-sam": False})
    e.state.admin_directory = e.ingress_app.state.admin_directory = directory
    owner = e.user("u-owner", "Owner Person")
    assert owner.ok("get", "/api/v1/me")["role"] == "admin"
    # Home Assistant goes away: the stored flag is kept, nobody else is promoted
    directory.available = False
    me = owner.ok("get", "/api/v1/me")
    assert me["role"] == "admin" and me["admin_setup"] is False
    sam = e.user("u-sam", "Sam Member")
    assert sam.ok("get", "/api/v1/me")["role"] == "member"
    # a user Home Assistant has never reported (None) is not promoted either
    assert e.user("u-new", "New Person").ok("get", "/api/v1/me")["role"] == "member"


def test_home_assistant_admin_flag_can_be_revoked(tmp_path):
    e = _bare_env(tmp_path)
    directory = _Directory({"u-owner": True})
    e.state.admin_directory = e.ingress_app.state.admin_directory = directory
    owner = e.user("u-owner", "Owner Person")
    assert owner.ok("get", "/api/v1/me")["role"] == "admin"
    directory.answers["u-owner"] = False
    assert owner.ok("get", "/api/v1/me")["role"] == "member"


def test_concurrent_first_requests(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    e = _bare_env(tmp_path)
    clients = [e.user(f"u-{i}", f"Person {i}") for i in range(8)]
    # the same person twice at once, plus several others
    clients += [e.user("u-0", "Person 0")]
    with ThreadPoolExecutor(max_workers=9) as pool:
        results = list(pool.map(lambda c: c.get("/api/v1/me"), clients))
    assert all(r.status_code == 200 for r in results), [r.text for r in results if r.status_code != 200]
    assert {r.json()["role"] for r in results} == {"member"}
    code = _code(e)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda c: c.post("/api/v1/setup/claim-admin", json={"code": code}), clients[:8]))
    assert sorted(r.status_code for r in claims).count(200) == 1
    admins = [c for c in clients[:8] if c.ok("get", "/api/v1/me")["role"] == "admin"]
    assert len(admins) == 1


def test_claim_works_when_everyone_defaults_to_read_only(tmp_path):
    e = _bare_env(tmp_path, default_role="read_only")
    first = e.user("u-first", "First Person")
    assert first.ok("get", "/api/v1/me")["role"] == "read_only"
    first.ok("post", "/api/v1/setup/claim-admin", json={"code": _code(e)})
    assert first.ok("get", "/api/v1/me")["role"] == "admin"
