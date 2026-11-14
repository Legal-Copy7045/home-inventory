"""Integration-test fixtures: a real Home Inventory App process."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AppProcess:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.ingress_port = _free_port()
        self.api_port = _free_port()
        env = {
            **os.environ,
            "HINV_DATA_DIR": str(data_dir),
            "HINV_INGRESS_PORT": str(self.ingress_port),
            "HINV_API_PORT": str(self.api_port),
            "HINV_TRUSTED_PROXIES": "127.0.0.1",
            "HINV_AI_PROVIDER": "none",
            "HINV_BARCODE_OPEN_FACTS": "false",
            "HINV_BARCODE_UPCITEMDB": "false",
            "HINV_LOG_LEVEL": "warning",
        }
        python = os.environ.get("HINV_PYTHON", sys.executable)
        self.proc = subprocess.Popen([python, "-m", "home_inventory_server"], env=env)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.api_port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            self.proc.kill()
            raise RuntimeError("Home Inventory App did not start")
        self.token = (data_dir / "integration_token").read_text().strip()
        self.api_url = f"http://127.0.0.1:{self.api_port}"

    def ui(self, method: str, path: str, body=None, user="ui-user"):
        """Call the App the way the Ingress proxy would (identity headers)."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.ingress_port}/api/v1/{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Remote-User-Id": user, "X-Remote-User-Display-Name": "Pat UI", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def upload_photo(self, item_id: int, user="ui-user") -> dict:
        """Attach a small JPEG to an item (multipart, the way the web app does)."""
        import io

        import requests
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (30, 120, 200)).save(buf, "JPEG")
        r = requests.post(f"http://127.0.0.1:{self.ingress_port}/api/v1/attachments",
                          headers={"X-Remote-User-Id": user, "X-Remote-User-Display-Name": "Pat UI"},
                          files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")},
                          data={"kind": "photo", "item_id": str(item_id), "recognise": "false"}, timeout=20)
        r.raise_for_status()
        return r.json()

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="session")
def inventory_app(tmp_path_factory):
    import pytest_socket

    # These tests talk to a real App process on 127.0.0.1.
    pytest_socket.enable_socket()
    app = AppProcess(tmp_path_factory.mktemp("hinv"))
    # Seed some data once.
    shed = app.ui("POST", "locations", {"name": "Shed"})
    garage = app.ui("POST", "locations", {"name": "Garage"})
    box = app.ui("POST", "containers", {"name": "Box", "number": "15", "location_id": shed["id"]})
    app.ui("POST", "items", {"name": "Axle stand", "quantity": 2, "location_id": garage["id"], "purchase_price": "40"})
    app.ui("POST", "items", {"name": "Extension cord", "quantity": 3, "container_id": box["id"]})
    app.seed = {"shed": shed, "garage": garage, "box": box}
    yield app
    app.stop()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations, socket_enabled):
    yield
