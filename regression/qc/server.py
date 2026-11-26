"""A throwaway Home Inventory App on its own ports and data folder, with demo data."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import ROOT

# Made-up people, one per role.  The App trusts these headers only from
# 127.0.0.1, the same way it trusts Home Assistant's Ingress proxy.
ROLES = {
    "admin": {"X-Remote-User-Id": "qc-admin", "X-Remote-User-Name": "morgan", "X-Remote-User-Display-Name": "Morgan"},
    "member": {"X-Remote-User-Id": "qc-member", "X-Remote-User-Name": "casey", "X-Remote-User-Display-Name": "Casey"},
    "read_only": {"X-Remote-User-Id": "qc-viewer", "X-Remote-User-Name": "jordan", "X-Remote-User-Display-Name": "Jordan"},
}


class AppServer:
    def __init__(self, workdir: Path, ingress_port: int, api_port: int):
        self.workdir = workdir
        self.data = workdir / "data"
        self.ingress_port = ingress_port
        self.api_port = api_port
        self.proc: subprocess.Popen | None = None
        self.base = f"http://127.0.0.1:{ingress_port}/"
        self.api_base = f"http://127.0.0.1:{api_port}/"

    def start(self, timeout: float = 60) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        env = {**os.environ,
               "HINV_DATA_DIR": str(self.data), "HINV_TRUSTED_PROXIES": "127.0.0.1",
               # e2e-user: the smoke test's person, an administrator as in CI.  alex: the demo seed's.
               "HINV_ADMIN_USERS": "morgan,alex,e2e-user", "HINV_READ_ONLY_USERS": "jordan",
               # No barcode lookups on the internet from a test run.
               "HINV_BARCODE_OPEN_FACTS": "false", "HINV_BARCODE_UPCITEMDB": "false",
               "HINV_INGRESS_PORT": str(self.ingress_port), "HINV_API_PORT": str(self.api_port),
               "HINV_AI_PROVIDER": "none", "HINV_CURRENCY": "USD"}
        self.log = open(self.workdir / "server.log", "w")
        self.proc = subprocess.Popen([sys.executable, "-m", "home_inventory_server"], cwd=ROOT / "home_inventory/server",
                                     env=env, stdout=self.log, stderr=subprocess.STDOUT)
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"App exited; see {self.workdir / 'server.log'}")
            try:
                with _open(self.base + "api/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("App did not start in time")

    def seed(self) -> dict:
        """Demo household (tools/demo/seed.py) plus records the demo leaves out."""
        ids_file = self.workdir / "demo-ids.json"
        env = {**os.environ, "DEMO_BASE": self.base}
        subprocess.run([sys.executable, str(ROOT / "tools/demo/seed.py"), str(ids_file)], env=env, check=True,
                       capture_output=True, timeout=300)
        out = subprocess.run([sys.executable, str(Path(__file__).with_name("seed_extra.py")), self.base],
                             check=True, capture_output=True, text=True, timeout=300)
        ids = json.loads(ids_file.read_text())
        ids.update(json.loads(out.stdout.strip().splitlines()[-1]))
        (self.workdir / "ids.json").write_text(json.dumps(ids, indent=1))
        return ids

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "log", None):
            self.log.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


def _open(url: str, timeout: float = 10, headers: dict | None = None, method: str = "GET"):
    # Local only: never through the outbound proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(urllib.request.Request(url, headers=headers or {}, method=method), timeout=timeout)
