from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from home_inventory_server.ai import AIService
from home_inventory_server.config import Settings
from home_inventory_server.main import build_state, create_app


class FakeProvider:
    name = "fake"
    model = "fake-vision-1"

    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, prompt, schema, image, timeout):
        self.calls.append({"prompt": prompt, "image": image is not None})
        if not self.responses:
            raise AssertionError("unexpected AI call")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_settings(tmp_path: Path, **overrides) -> Settings:
    s = Settings(data_dir=tmp_path / "data")
    s.trusted_proxies = ["testclient", "172.30.32.2"]
    s.ai_provider = "none"
    s.ai_retries = 1
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def jpeg_bytes(color=(200, 30, 30), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


class Env:
    def __init__(self, tmp_path: Path, ai_provider=None, barcode_providers=None, configured_admin=True, **settings) -> None:
        if configured_admin:
            # Nobody is promoted automatically, so the test administrator is
            # configured the way a real install would list one.
            settings["admin_users"] = ["u-admin", *settings.get("admin_users", [])]
        self.settings = make_settings(tmp_path, **settings)
        self.fake_ai = ai_provider
        ai = AIService(self.settings, provider=ai_provider) if ai_provider else AIService(self.settings)
        self.state = build_state(self.settings, ai=ai, barcode_providers=barcode_providers if barcode_providers is not None else [])
        self.ingress_app = create_app(self.state, "ingress")
        self.api_app = create_app(self.state, "api")
        self._ingress = TestClient(self.ingress_app)
        self._api = TestClient(self.api_app)
        self.admin = self.user("u-admin", "Alex Admin")
        if configured_admin:
            self.admin.get("/api/v1/me").raise_for_status()

    def user(self, ha_id: str, name: str) -> UserClient:
        return UserClient(self._ingress, {"X-Remote-User-Id": ha_id, "X-Remote-User-Name": name.split()[0].lower(), "X-Remote-User-Display-Name": name})

    def integration(self, **extra) -> UserClient:
        return UserClient(self._api, {"Authorization": f"Bearer {self.state.api_token}", **extra})

    def raw_ingress(self) -> TestClient:
        return self._ingress

    def raw_api(self) -> TestClient:
        return self._api


class UserClient:
    def __init__(self, client: TestClient, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers

    def _h(self, kw):
        kw.setdefault("headers", {})
        kw["headers"] = {**self.headers, **kw["headers"]}
        return kw

    def get(self, url, **kw):
        return self.client.get(url, **self._h(kw))

    def post(self, url, **kw):
        return self.client.post(url, **self._h(kw))

    def patch(self, url, **kw):
        return self.client.patch(url, **self._h(kw))

    def put(self, url, **kw):
        return self.client.put(url, **self._h(kw))

    def delete(self, url, **kw):
        return self.client.delete(url, **self._h(kw))

    # helpers
    def ok(self, method: str, url: str, expected: int | tuple = (200, 201, 204), **kw):
        r = getattr(self, method)(url, **kw)
        exp = expected if isinstance(expected, tuple) else (expected,)
        assert r.status_code in exp, f"{method.upper()} {url} -> {r.status_code}: {r.text}"
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r

    def location(self, name, parent_id=None, **kw):
        return self.ok("post", "/api/v1/locations", json={"name": name, "parent_id": parent_id, **kw})

    def container(self, name, number=None, location_id=None, parent_container_id=None):
        return self.ok(
            "post",
            "/api/v1/containers",
            json={"name": name, "number": number, "location_id": location_id, "parent_container_id": parent_container_id},
        )

    def item(self, name, **kw):
        return self.ok("post", "/api/v1/items", json={"name": name, **kw})["items"][0]

    def upload(self, data: bytes | None = None, kind="photo", filename="photo.jpg", **form):
        return self.ok(
            "post",
            "/api/v1/attachments",
            files={"file": (filename, data or jpeg_bytes(), "application/octet-stream")},
            data={"kind": kind, **{k: str(v) for k, v in form.items()}},
        )


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.state.db.dispose()


@pytest.fixture
def fake_ai():
    return FakeProvider()


@pytest.fixture
def ai_env(tmp_path, fake_ai):
    e = Env(tmp_path, ai_provider=fake_ai)
    yield e
    e.state.db.dispose()
