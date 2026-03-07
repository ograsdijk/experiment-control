from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any


_CHILD_MODULE_MARKERS = (
    "experiment_control.cli.start_driver",
    "experiment_control.cli.start_process",
)


def _normalize_instance_id(raw: str) -> str:
    return str(raw or "").strip()


def _command_has_instance(command: str, instance_id: str) -> bool:
    needle = _normalize_instance_id(instance_id)
    if not needle:
        return False
    cmd = str(command or "")
    token_prefixes = (
        f"--instance-id {needle}",
        f'--instance-id "{needle}"',
        f"--instance-id='{needle}'",
        f"--instance-id={needle}",
        f'--instance-id="{needle}"',
        f"--instance-id='{needle}'",
    )
    return any(prefix in cmd for prefix in token_prefixes)


def _extract_int_flag(command: str, *, flag: str) -> int | None:
    text = str(command or "")
    patterns = [
        rf"--{re.escape(flag)}\s+(-?\d+)",
        rf"--{re.escape(flag)}=(-?\d+)",
        rf'--{re.escape(flag)}="(-?\d+)"',
        rf"--{re.escape(flag)}='(-?\d+)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match is None:
            continue
        try:
            return int(match.group(1))
        except Exception:
            continue
    return None


def _command_is_child_runner(command: str) -> bool:
    cmd = str(command or "")
    return any(marker in cmd for marker in _CHILD_MODULE_MARKERS)


def _list_process_commands_posix() -> list[tuple[int, str]]:
    try:
        proc = subprocess.run(
            ["ps", "-ax", "-o", "pid=", "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    out: list[tuple[int, str]] = []
    for line in proc.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_text, cmd = parts
        try:
            pid = int(pid_text)
        except Exception:
            continue
        if pid <= 0:
            continue
        out.append((pid, cmd))
    return out


def _list_process_commands_windows() -> list[tuple[int, str]]:
    powershell = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
    ]
    try:
        proc = subprocess.run(
            powershell,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    text = proc.stdout.strip()
    if not text:
        return []
    try:
        import json

        decoded = json.loads(text)
    except Exception:
        return []
    if isinstance(decoded, dict):
        rows = [decoded]
    elif isinstance(decoded, list):
        rows = [item for item in decoded if isinstance(item, dict)]
    else:
        rows = []
    out: list[tuple[int, str]] = []
    for row in rows:
        raw_pid = row.get("ProcessId")
        raw_cmd = row.get("CommandLine")
        if raw_cmd is None:
            continue
        try:
            pid = int(raw_pid)
        except Exception:
            continue
        if pid <= 0:
            continue
        cmd = str(raw_cmd)
        if not cmd.strip():
            continue
        out.append((pid, cmd))
    return out


def _list_process_commands() -> list[tuple[int, str]]:
    if sys.platform == "win32":
        return _list_process_commands_windows()
    return _list_process_commands_posix()


def _process_exists(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    if sys.platform == "win32":
        return _process_exists_windows(int(pid))
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _process_exists_windows(pid: int) -> bool:
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


def _terminate_pid_posix(pid: int, *, timeout_s: float) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return True
        time.sleep(0.05)
    return not _process_exists(pid)


def _terminate_pid_windows(pid: int, *, timeout_s: float) -> bool:
    if pid <= 0:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return False

    process_terminate = 0x0001
    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102

    kernel32.OpenProcess.argtypes = [
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(
        process_terminate | synchronize,
        0,
        int(pid),
    )
    if not handle:
        return not _process_exists(pid)
    try:
        ok = kernel32.TerminateProcess(handle, 1)
        if not ok:
            return not _process_exists(pid)
        wait_ms = int(max(100.0, float(timeout_s) * 1000.0))
        rc = int(kernel32.WaitForSingleObject(handle, wait_ms))
        if rc == wait_object_0:
            return True
        if rc == wait_timeout:
            return not _process_exists(pid)
        return not _process_exists(pid)
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass


def _terminate_pid(pid: int, *, timeout_s: float) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _terminate_pid_windows(pid, timeout_s=timeout_s)
    return _terminate_pid_posix(pid, timeout_s=timeout_s)


def cleanup_orphan_children(
    *,
    instance_id: str,
    exclude_pids: set[int] | None = None,
    current_parent_pid: int | None = None,
    timeout_s: float = 2.0,
    stale_only: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    target_instance = _normalize_instance_id(instance_id)
    excluded = {int(pid) for pid in (exclude_pids or set()) if int(pid) > 0}
    current_parent = int(current_parent_pid) if current_parent_pid else None
    candidates: list[int] = []
    skipped_live_parent: list[int] = []
    for pid, cmd in _list_process_commands():
        if pid in excluded:
            continue
        if not _command_is_child_runner(cmd):
            continue
        if not _command_has_instance(cmd, target_instance):
            continue
        parent_pid = _extract_int_flag(cmd, flag="parent-pid")
        if current_parent is not None and parent_pid == current_parent:
            continue
        if stale_only and parent_pid is not None and _process_exists(parent_pid):
            skipped_live_parent.append(pid)
            continue
        candidates.append(pid)
    matched = len(candidates)
    terminated: list[int] = []
    failed: list[int] = []
    if not dry_run:
        for pid in candidates:
            if _terminate_pid(pid, timeout_s=timeout_s):
                terminated.append(pid)
            else:
                failed.append(pid)
    return {
        "instance_id": target_instance,
        "matched": matched,
        "dry_run": bool(dry_run),
        "stale_only": bool(stale_only),
        "skipped_live_parent": skipped_live_parent,
        "candidates": candidates,
        "terminated": terminated,
        "failed": failed,
    }


def _start_parent_watchdog_thread(*, expected_parent_pid: int) -> None:
    def _watch() -> None:
        while True:
            if os.getppid() != expected_parent_pid:
                os._exit(1)
            time.sleep(0.5)

    thread = threading.Thread(
        target=_watch,
        daemon=True,
        name="ec-parent-watchdog",
    )
    thread.start()


def _configure_linux_pdeathsig(*, expected_parent_pid: int) -> bool:
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except Exception:
        return False
    prctl = getattr(libc, "prctl", None)
    if prctl is None:
        return False
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    pr_set_pdeathsig = 1
    rc = int(prctl(pr_set_pdeathsig, signal.SIGTERM, 0, 0, 0))
    if rc != 0:
        return False
    if os.getppid() != expected_parent_pid:
        os._exit(1)
    return True


def configure_child_parent_guard(*, parent_pid: int | None) -> None:
    if parent_pid is None:
        return
    expected_parent_pid = int(parent_pid)
    if expected_parent_pid <= 0:
        return
    if sys.platform.startswith("linux"):
        if _configure_linux_pdeathsig(expected_parent_pid=expected_parent_pid):
            return
        _start_parent_watchdog_thread(expected_parent_pid=expected_parent_pid)
        return
    if sys.platform == "darwin":
        _start_parent_watchdog_thread(expected_parent_pid=expected_parent_pid)


@dataclass
class ProcessGuardian:
    _job: "_WindowsJobObject | None" = None

    def __init__(self) -> None:
        self._job = None
        if sys.platform == "win32":
            try:
                self._job = _WindowsJobObject()
            except Exception:
                self._job = None

    def adopt_popen(self, popen: subprocess.Popen[str] | None) -> None:
        if self._job is None or popen is None:
            return
        pid = int(getattr(popen, "pid", 0) or 0)
        if pid <= 0:
            return
        self._job.assign_pid(pid)

    def close(self) -> None:
        if self._job is None:
            return
        self._job.close()
        self._job = None


class _WindowsJobObject:
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows-only helper")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        self._configure_signatures()
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            self._raise_last_error("CreateJobObjectW failed")
        info = self._extended_info_type()
        info.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            self._handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self.close()
            self._raise_last_error("SetInformationJobObject failed")

    def _configure_signatures(self) -> None:
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._kernel32.SetInformationJobObject.restype = ctypes.c_int
        self._kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.AssignProcessToJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int

    @staticmethod
    def _extended_info_type() -> type[ctypes.Structure]:
        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        return _JOBOBJECT_EXTENDED_LIMIT_INFORMATION

    def _raise_last_error(self, message: str) -> None:
        err = ctypes.get_last_error()
        raise OSError(err, message)

    def assign_pid(self, pid: int) -> None:
        if pid <= 0:
            return
        proc_handle = self._kernel32.OpenProcess(
            self._PROCESS_TERMINATE | self._PROCESS_SET_QUOTA,
            0,
            int(pid),
        )
        if not proc_handle:
            self._raise_last_error(f"OpenProcess failed for pid={pid}")
        try:
            ok = self._kernel32.AssignProcessToJobObject(self._handle, proc_handle)
            if not ok:
                self._raise_last_error(f"AssignProcessToJobObject failed for pid={pid}")
        finally:
            try:
                self._kernel32.CloseHandle(proc_handle)
            except Exception:
                pass

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if not handle:
            return
        try:
            self._kernel32.CloseHandle(handle)
        except Exception:
            pass
        self._handle = None
