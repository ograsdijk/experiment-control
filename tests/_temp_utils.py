from __future__ import annotations

import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_ROOT = Path(__file__).resolve().parents[1]
_TMP_ROOT = _ROOT / ".tmp_tests_local"


def repo_temp_root() -> Path:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return _TMP_ROOT


def _cleanup_tree(path: Path) -> None:
    if not path.exists():
        return
    for _ in range(20):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except Exception:
            time.sleep(0.05)
    shutil.rmtree(path, ignore_errors=True)


@contextmanager
def repo_temp_dir(prefix: str = "tmp") -> Iterator[Path]:
    root = repo_temp_root()
    name = f"{prefix}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    path = root / name
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        _cleanup_tree(path)
