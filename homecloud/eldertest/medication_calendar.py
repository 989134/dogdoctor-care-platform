"""Small persistent medication schedule used by the focused care dashboard."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path


TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
VALID_DAYS = tuple(range(7))  # Monday = 0


class MedicationCalendar:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([
                self._entry("早晨藥物", "08:00", VALID_DAYS, "請依藥袋與醫囑服用"),
                self._entry("中午藥物", "12:00", VALID_DAYS, "請依藥袋與醫囑服用"),
                self._entry("晚上藥物", "20:00", VALID_DAYS, "請依藥袋與醫囑服用"),
            ])

    @staticmethod
    def _entry(name, at, days, note=""):
        return {
            "id": uuid.uuid4().hex,
            "name": str(name).strip(),
            "time": str(at).strip(),
            "days": sorted({int(day) for day in days}),
            "note": str(note).strip(),
            "created_at": time.time(),
        }

    def _read(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []
        return raw if isinstance(raw, list) else []

    def _write(self, entries):
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def list(self):
        with self.lock:
            return sorted(self._read(), key=lambda item: (item.get("time", ""), item.get("name", "")))

    def add(self, name, at, days, note=""):
        name = " ".join(str(name or "").split())
        at = str(at or "").strip()
        if not name or len(name) > 60:
            raise ValueError("藥品名稱必須為 1 到 60 個字")
        if not TIME_RE.fullmatch(at):
            raise ValueError("時間格式必須是 HH:MM")
        if not isinstance(days, list) or not days:
            raise ValueError("請至少選擇一天")
        try:
            normalized_days = sorted({int(day) for day in days})
        except (TypeError, ValueError):
            raise ValueError("星期資料格式錯誤") from None
        if any(day not in VALID_DAYS for day in normalized_days):
            raise ValueError("星期必須介於 0 到 6")
        entry = self._entry(name, at, normalized_days, str(note or "")[:120])
        with self.lock:
            entries = self._read()
            entries.append(entry)
            self._write(entries)
        return entry

    def remove(self, ident):
        with self.lock:
            entries = self._read()
            remaining = [item for item in entries if item.get("id") != ident]
            if len(remaining) == len(entries):
                return False
            self._write(remaining)
            return True

    def get(self, ident):
        return next((item for item in self.list() if item.get("id") == ident), None)
