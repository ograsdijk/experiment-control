# ruff: noqa: E402

import os
import subprocess
import sys
import tempfile
import time
import ctypes
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _pid_is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        if int(pid) <= 0:
            return False
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except Exception:
            return False
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            0,
            int(pid),
        )
        if not handle:
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


def _wait_for_file(path: Path, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def _wait_pid_exit(pid: int, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_is_alive(pid)


class ProcessLifecycleIntegrationTests(unittest.TestCase):
    def _spawn_posix_guard_parent(self, pid_file: Path) -> subprocess.Popen[str]:
        parent_script = r"""
import os
import subprocess
import sys
import time
from pathlib import Path

pid_file = Path(sys.argv[1])
ppid = os.getpid()
child_code = (
    "import sys,time;"
    "from experiment_control.utils.process_lifecycle import configure_child_parent_guard;"
    "configure_child_parent_guard(parent_pid=int(sys.argv[1]));"
    "time.sleep(60)"
)
child = subprocess.Popen([sys.executable, "-c", child_code, str(ppid)])
pid_file.write_text(str(child.pid), encoding="utf-8")
time.sleep(60)
"""
        env = os.environ.copy()
        py_path = str(SRC)
        if env.get("PYTHONPATH"):
            env["PYTHONPATH"] = py_path + os.pathsep + env["PYTHONPATH"]
        else:
            env["PYTHONPATH"] = py_path
        return subprocess.Popen(
            [sys.executable, "-c", parent_script, str(pid_file)],
            env=env,
            text=True,
        )

    def _spawn_windows_job_parent(self, pid_file: Path) -> subprocess.Popen[str]:
        parent_script = r"""
import subprocess
import sys
import time
from pathlib import Path
from experiment_control.utils.process_lifecycle import ProcessGuardian

pid_file = Path(sys.argv[1])
guard = ProcessGuardian()
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
guard.adopt_popen(child)
pid_file.write_text(str(child.pid), encoding="utf-8")
time.sleep(60)
"""
        env = os.environ.copy()
        py_path = str(SRC)
        if env.get("PYTHONPATH"):
            env["PYTHONPATH"] = py_path + os.pathsep + env["PYTHONPATH"]
        else:
            env["PYTHONPATH"] = py_path
        return subprocess.Popen(
            [sys.executable, "-c", parent_script, str(pid_file)],
            env=env,
            text=True,
        )

    def test_parent_kill_cleans_children_and_allows_restart(self) -> None:
        if sys.platform == "win32":
            spawner = self._spawn_windows_job_parent
        else:
            spawner = self._spawn_posix_guard_parent

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_pid_file = tmp_path / "first_child.pid"
            second_pid_file = tmp_path / "second_child.pid"

            first_parent = spawner(first_pid_file)
            try:
                self.assertTrue(_wait_for_file(first_pid_file), "first child pid not written")
                first_child_pid = int(first_pid_file.read_text(encoding="utf-8").strip())
                self.assertTrue(_pid_is_alive(first_child_pid))
                first_parent.kill()
                first_parent.wait(timeout=5.0)
                self.assertTrue(
                    _wait_pid_exit(first_child_pid, timeout_s=6.0),
                    f"first child pid {first_child_pid} still alive after parent kill",
                )
            finally:
                if first_parent.poll() is None:
                    first_parent.kill()
                    first_parent.wait(timeout=5.0)

            second_parent = spawner(second_pid_file)
            try:
                self.assertTrue(
                    _wait_for_file(second_pid_file), "second child pid not written on restart"
                )
                second_child_pid = int(second_pid_file.read_text(encoding="utf-8").strip())
                self.assertTrue(_pid_is_alive(second_child_pid))
                second_parent.kill()
                second_parent.wait(timeout=5.0)
                self.assertTrue(
                    _wait_pid_exit(second_child_pid, timeout_s=6.0),
                    f"second child pid {second_child_pid} still alive after parent kill",
                )
            finally:
                if second_parent.poll() is None:
                    second_parent.kill()
                    second_parent.wait(timeout=5.0)


if __name__ == "__main__":
    unittest.main()
