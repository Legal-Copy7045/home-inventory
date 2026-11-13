"""Photo dates from EXIF, the AI job pool and busy-AI document readings."""

from __future__ import annotations

import io
import threading
import time

from fastapi import HTTPException
from PIL import Image

from home_inventory_server.services import jobs
from home_inventory_server.services.media import taken_on
from home_inventory_server.services.textmatch import stem
from tests.conftest import Env, FakeProvider, jpeg_bytes
from tests.test_documents import RECEIPT
from tests.test_parts import _pdf


def _jpeg_taken(when: str) -> bytes:
    img = Image.new("RGB", (40, 30), (10, 120, 10))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = when
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_taken_on_reads_exif_date():
    assert taken_on(_jpeg_taken("2026:05:02 14:31:07")) == "2026-05-02"
    assert taken_on(jpeg_bytes()) is None  # no EXIF
    assert taken_on(_jpeg_taken("0000:00:00 00:00:00")) is None
    assert taken_on(b"%PDF-1.4 nothing") is None


def test_upload_returns_photo_date_even_though_exif_is_stripped(env):
    u = env.admin
    item = u.item("Mower")
    att = u.upload(_jpeg_taken("2026:05:02 14:31:07"), item_id=item["id"])
    assert att["taken_on"] == "2026-05-02"
    stored = env.state.media.read(env.state.db.SessionLocal().get(__import__(
        "home_inventory_server.models", fromlist=["Attachment"]).Attachment, att["id"]).stored_path)
    assert taken_on(stored) is None  # the saved copy has no EXIF (privacy)


def test_jobs_run_at_most_max_workers_at_once():
    running, peak, lock = 0, 0, threading.Lock()

    def job():
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1

    futures = [jobs.submit(job, f"t{i}") for i in range(jobs.MAX_WORKERS * 3)]
    for f in futures:
        f.result(timeout=5)
    assert peak <= jobs.MAX_WORKERS


def test_document_upload_when_ai_busy_is_recorded_not_skipped(tmp_path):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin

    def busy(user_id, bucket):
        raise HTTPException(429, "Too many requests")

    env.state.rate_limiter.check = busy
    drill = u.item("Cordless drill")
    fake.responses.append(RECEIPT)
    att = u.upload(_pdf(["RECEIPT", "TOTAL 9.99"]), kind="document", filename="r.pdf", item_id=drill["id"])
    assert att["reading_id"]  # the upload itself succeeded
    r = u.ok("get", f"/api/v1/document-readings/{att['reading_id']}")
    assert r["status"] == "failed" and "busy" in r["error"]
    assert not fake.calls
    env.state.db.dispose()


def test_one_plural_rule():
    assert [stem(w) for w in ("shelves", "batteries", "boxes", "brushes", "glass", "bus", "drills")] == [
        "shelf", "battery", "box", "brush", "glass", "bus", "drill"]
