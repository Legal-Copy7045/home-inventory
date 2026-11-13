"""Voice history: what was asked of Home Inventory by voice, and the answer.

Kept in ``<data>/voice_history.json`` on this machine only: the newest 500
entries from the last 90 days.  Written by the Home Assistant integration
after it answers (Alexa, Assist, Siri through Assist).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MAX_ENTRIES = 500
MAX_AGE = timedelta(days=90)
_LOCK = threading.Lock()


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / "voice_history.json"


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _keep(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = (datetime.now(UTC) - MAX_AGE).isoformat()
    return [e for e in entries if str(e.get("at", "")) >= cutoff][-MAX_ENTRIES:]


def _short(v: Any, n: int) -> str | None:
    return None if v is None else str(v)[:n]


def add(data_dir: Path, entry: dict[str, Any], user: str | None) -> dict[str, Any]:
    slots = entry.get("slots") if isinstance(entry.get("slots"), dict) else {}
    clean = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": _short(entry.get("source"), 30) or "voice",
        "device": _short(entry.get("device"), 120),
        "user": _short(user, 120),
        "intent": _short(entry.get("intent"), 60),
        "slots": {str(k)[:30]: _short(v, 200) for k, v in list(slots.items())[:6]},
        "text": _short(entry.get("text"), 300),
        "answer": _short(entry.get("answer"), 600),
        "question": bool(entry.get("question")),
    }
    path = _path(data_dir)
    with _LOCK:
        entries = _keep(_load(path) + [clean])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return clean


def recent(data_dir: Path, limit: int = 200) -> list[dict[str, Any]]:
    with _LOCK:
        return list(reversed(_keep(_load(_path(data_dir)))))[:limit]


def clear(data_dir: Path) -> None:
    with _LOCK:
        try:
            _path(data_dir).unlink()
        except FileNotFoundError:
            pass
