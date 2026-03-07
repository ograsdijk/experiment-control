from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import ctypes
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _safe_instance_name(instance_id: str) -> str:
    raw = str(instance_id or "").strip()
    if not raw:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)


def _lock_root() -> Path:
    return Path(tempfile.gettempdir()) / "experiment_control" / "instance_locks"


def _pid_is_alive(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    if sys.platform == "win32":
        return _pid_is_alive_windows(int(pid))
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    except Exception:
        return False


def _pid_is_alive_windows(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return False

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5

    kernel32.OpenProcess.argtypes = [
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(process_query_limited_information, 0, int(pid))
    if not handle:
        err = int(ctypes.get_last_error())
        if err == error_access_denied:
            return True
        return False
    try:
        exit_code = ctypes.c_uint32(0)
        ok = int(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
        if not ok:
            return True
        return int(exit_code.value) == still_active
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass


def _normalize_effective_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"active", "stale", "missing", "running_unlocked"}:
        return text
    if text == "invalid":
        # Invalid lock payload behaves effectively like "missing" for lifecycle UX.
        return "missing"
    return "unknown"


def _coerce_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except Exception:
        return None
    return pid if pid > 0 else None


@dataclass(frozen=True)
class InstanceLockInfo:
    instance_id: str
    pid: int
    owner_alive: bool
    manager_rpc: str
    lock_path: str
    acquired_wall_s: float | None


class InstanceLockActiveError(RuntimeError):
    def __init__(self, info: InstanceLockInfo) -> None:
        msg = (
            f"instance {info.instance_id!r} is already locked by pid={info.pid} "
            f"(owner_alive={info.owner_alive}, lock={info.lock_path}, "
            f"manager_rpc={info.manager_rpc!r})"
        )
        super().__init__(msg)
        self.info = info


class InstanceLock:
    def __init__(self, *, instance_id: str, manager_rpc: str) -> None:
        self._instance_id = str(instance_id).strip()
        self._manager_rpc = str(manager_rpc).strip()
        lock_name = f"{_safe_instance_name(self._instance_id)}.json"
        self._path = _lock_root() / lock_name
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "instance_id": self._instance_id,
            "pid": int(os.getpid()),
            "manager_rpc": self._manager_rpc,
            "acquired_wall_s": float(time.time()),
        }

    @staticmethod
    def _read_info(path: Path) -> InstanceLockInfo | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        pid_raw = raw.get("pid")
        try:
            pid = int(pid_raw)
        except Exception:
            pid = -1
        return InstanceLockInfo(
            instance_id=str(raw.get("instance_id", "") or "").strip(),
            pid=pid,
            owner_alive=_pid_is_alive(pid),
            manager_rpc=str(raw.get("manager_rpc", "") or "").strip(),
            lock_path=str(path),
            acquired_wall_s=(
                float(raw.get("acquired_wall_s"))
                if isinstance(raw.get("acquired_wall_s"), (int, float))
                else None
            ),
        )

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload_text = json.dumps(self._payload(), sort_keys=True)
        while True:
            try:
                fd = os.open(
                    str(self._path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                info = self._read_info(self._path)
                if info is not None and _pid_is_alive(info.pid):
                    raise InstanceLockActiveError(info)
                try:
                    self._path.unlink()
                except FileNotFoundError:
                    continue
                except Exception:
                    if info is not None:
                        raise InstanceLockActiveError(info) from None
                    raise
                continue
            os.write(fd, payload_text.encode("utf-8"))
            try:
                os.fsync(fd)
            except Exception:
                pass
            self._fd = fd
            return

    def release(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if not self._path.exists():
            return
        info = self._read_info(self._path)
        if info is not None and info.pid != int(os.getpid()):
            return
        try:
            self._path.unlink()
        except Exception:
            pass


def get_instance_lock_path(instance_id: str) -> Path:
    lock_name = f"{_safe_instance_name(str(instance_id).strip())}.json"
    return _lock_root() / lock_name


def derive_lock_effective_status(
    *,
    lock_status: Mapping[str, Any] | None,
    manager_pid: int | None,
    manager_reachable: bool,
    reported_effective_status: Any | None = None,
) -> str:
    status = _normalize_effective_status(reported_effective_status)
    if status == "unknown":
        raw_status = (
            lock_status.get("status") if isinstance(lock_status, Mapping) else None
        )
        status = _normalize_effective_status(raw_status)

    owner_pid = (
        _coerce_pid(lock_status.get("owner_pid"))
        if isinstance(lock_status, Mapping)
        else None
    )
    normalized_manager_pid = _coerce_pid(manager_pid)
    if (
        normalized_manager_pid is not None
        and owner_pid is not None
        and normalized_manager_pid == owner_pid
    ):
        return "active"

    if bool(manager_reachable) and status in {"stale", "missing"}:
        return "running_unlocked"
    return status


def lock_effective_status_help(status: Any) -> str:
    normalized = _normalize_effective_status(status)
    if normalized == "active":
        return "Lock is held by the running manager process."
    if normalized == "running_unlocked":
        return "Manager is reachable, but no active instance lock is held."
    if normalized == "stale":
        return "Lock file exists, but its owner process is not alive."
    if normalized == "missing":
        return "No lock file exists for this instance."
    return "Lock status is unknown."


def read_instance_lock_status(instance_id: str) -> dict[str, Any]:
    path = get_instance_lock_path(instance_id)
    out: dict[str, Any] = {
        "instance_id": str(instance_id).strip(),
        "lock_path": str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        out["status"] = "missing"
        return out
    info = InstanceLock._read_info(path)
    if info is None:
        out["status"] = "invalid"
        return out
    out.update(
        {
            "status": "active" if info.owner_alive else "stale",
            "owner_pid": info.pid,
            "owner_alive": info.owner_alive,
            "manager_rpc": info.manager_rpc,
            "acquired_wall_s": info.acquired_wall_s,
        }
    )
    return out
