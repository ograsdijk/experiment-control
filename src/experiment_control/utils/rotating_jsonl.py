from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import Any, TextIO


class RotatingJsonlSink:
    """Line-buffered JSONL sink with UTC-day and size rotation."""

    def __init__(
        self,
        *,
        directory: Path,
        prefix: str = "manager",
        max_bytes: int = 100 * 1024 * 1024,
        max_age_days: float | None = 30.0,
        max_total_bytes: int | None = 5 * 1024 * 1024 * 1024,
    ) -> None:
        self._directory = Path(directory)
        self._prefix = str(prefix).strip() or "manager"
        self._max_bytes = max(1, int(max_bytes))
        self._max_age_days = max_age_days
        self._max_total_bytes = max_total_bytes
        self._handle: TextIO | None = None
        self._path: Path | None = None
        self._utc_day = ""
        self._directory.mkdir(parents=True, exist_ok=True)
        self._open_for_day(self._current_utc_day())
        self._apply_retention()

    @property
    def path(self) -> Path | None:
        return self._path

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        encoded_size = len(line.encode("utf-8")) + 1
        utc_day = self._current_utc_day()
        if utc_day != self._utc_day or self._would_exceed_size(encoded_size):
            self._rotate(utc_day)
        handle = self._handle
        if handle is None:
            raise OSError("rotating JSONL sink is closed")
        handle.write(line + "\n")

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            handle.close()

    @staticmethod
    def _current_utc_day() -> str:
        return datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    def _matching_files(self) -> list[Path]:
        return sorted(self._directory.glob(f"{self._prefix}-*.jsonl"))

    def _path_for(self, utc_day: str, shard: int) -> Path:
        return self._directory / f"{self._prefix}-{utc_day}-{shard:03d}.jsonl"

    def _open_for_day(self, utc_day: str, *, force_new: bool = False) -> None:
        shard = 0
        path = self._path_for(utc_day, shard)
        while path.exists():
            shard += 1
            path = self._path_for(utc_day, shard)
        if shard > 0:
            shard -= 1
            path = self._path_for(utc_day, shard)
        if force_new or (path.exists() and path.stat().st_size >= self._max_bytes):
            shard += 1
            path = self._path_for(utc_day, shard)
            while path.exists():
                shard += 1
                path = self._path_for(utc_day, shard)
        self._handle = path.open("a", encoding="utf-8", buffering=1)
        self._path = path
        self._utc_day = utc_day

    def _would_exceed_size(self, encoded_size: int) -> bool:
        path = self._path
        if path is None or not path.exists():
            return False
        return path.stat().st_size > 0 and path.stat().st_size + encoded_size > self._max_bytes

    def _rotate(self, utc_day: str) -> None:
        same_day = utc_day == self._utc_day
        self.close()
        self._open_for_day(utc_day, force_new=same_day)
        self._apply_retention()

    def _apply_retention(self) -> None:
        active = self._path
        files = self._matching_files()
        if self._max_age_days is not None:
            cutoff = time.time() - float(self._max_age_days) * 86_400.0
            for path in files:
                if path != active and path.stat().st_mtime < cutoff:
                    self._unlink_quietly(path)

        if self._max_total_bytes is None:
            return
        files = [path for path in self._matching_files() if path.exists()]
        total = sum(path.stat().st_size for path in files)
        for path in sorted(files, key=lambda item: (item.stat().st_mtime, item.name)):
            if total <= self._max_total_bytes:
                break
            if path == active:
                continue
            size = path.stat().st_size
            if self._unlink_quietly(path):
                total -= size

    @staticmethod
    def _unlink_quietly(path: Path) -> bool:
        try:
            path.unlink()
        except OSError:
            return False
        return True
