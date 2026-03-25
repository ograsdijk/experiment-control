# ruff: noqa: E402

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
import unittest

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils.config_parsing import optional_dict, require_dict
from experiment_control.utils.manager_network import resolve_manager_network
from experiment_control.utils.yaml_helpers import load_yaml_file
from experiment_control.utils.zmq_helpers import json_dumps, safe_json_loads


def _resolve_manager_rpc(stack_path: Path) -> str:
    raw = load_yaml_file(stack_path)
    obj = require_dict(raw, path=[])
    manager_raw = optional_dict(obj.get("manager"), path=["manager"])
    return resolve_manager_network(manager_raw).local_rpc_connect


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _rpc_request(endpoint: str, payload: dict[str, object], timeout_ms: int) -> dict[str, object] | None:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(str(endpoint))
    try:
        sock.send(json_dumps(payload))
        if not sock.poll(int(timeout_ms), zmq.POLLIN):
            return None
        resp = safe_json_loads(sock.recv())
        if isinstance(resp, dict):
            return resp
        return None
    except Exception:
        return None
    finally:
        sock.close(0)


def _wait_for_manager_identity(endpoint: str, *, timeout_s: float) -> dict[str, object] | None:
    deadline = time.monotonic() + float(timeout_s)
    while time.monotonic() < deadline:
        request_id = uuid.uuid4().hex
        resp = _rpc_request(
            endpoint,
            {"type": "manager.info.identity", "request_id": request_id},
            timeout_ms=500,
        )
        if isinstance(resp, dict) and resp.get("ok"):
            return resp
        time.sleep(0.2)
    return None


def _wait_for_http_health(port: int, *, timeout_s: float) -> bool:
    url = f"http://127.0.0.1:{int(port)}/api/health"
    deadline = time.monotonic() + float(timeout_s)
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if int(resp.status) == 200:
                    return True
        except urllib.error.URLError:
            pass
        except TimeoutError:
            pass
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _terminate_process(proc: subprocess.Popen[str], *, timeout_s: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout_s)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=3.0)
    except Exception:
        pass


@unittest.skipUnless(sys.platform == "win32", "windows-only smoke test")
class WindowsLifecycleSmokeTests(unittest.TestCase):
    def test_run_stack_no_tui_instance_lock_survives_fastapi_start(self) -> None:
        if os.environ.get("EC_ENABLE_WINDOWS_LIFECYCLE_SMOKE", "") != "1":
            self.skipTest("set EC_ENABLE_WINDOWS_LIFECYCLE_SMOKE=1 to run")

        stack_path = ROOT / "examples" / "dummy_frequency_trace_sequencer" / "stack.yaml"
        fastapi_script = (
            ROOT
            / "examples"
            / "dummy_frequency_trace_sequencer"
            / "run_dummy_frequency_trace_fastapi.py"
        )
        manager_rpc = _resolve_manager_rpc(stack_path)

        env = dict(os.environ)
        pythonpath = str(SRC)
        if env.get("PYTHONPATH"):
            env["PYTHONPATH"] = pythonpath + os.pathsep + str(env["PYTHONPATH"])
        else:
            env["PYTHONPATH"] = pythonpath

        # Best-effort cleanup from prior interrupted test runs.
        _rpc_request(
            manager_rpc,
            {"type": "manager.control.shutdown"},
            timeout_ms=300,
        )
        time.sleep(0.3)

        manager_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "experiment_control.cli.run_stack",
                str(stack_path),
                "--no-tui",
                "--instance-lock",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        fastapi_proc: subprocess.Popen[str] | None = None

        try:
            identity_before = _wait_for_manager_identity(manager_rpc, timeout_s=25.0)
            self.assertIsNotNone(identity_before, "manager did not become ready")
            assert isinstance(identity_before, dict)
            result_before = identity_before.get("result", {})
            self.assertIsInstance(result_before, dict)
            assert isinstance(result_before, dict)
            manager_pid_before = int(result_before.get("manager_pid", -1))
            self.assertGreater(manager_pid_before, 0)
            self.assertIsNone(manager_proc.poll(), "manager exited before FastAPI start")

            fastapi_port = _free_tcp_port()
            fastapi_proc = subprocess.Popen(
                [
                    sys.executable,
                    str(fastapi_script),
                    "--stack",
                    str(stack_path),
                    "--port",
                    str(fastapi_port),
                    "--no-ui",
                ],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.assertTrue(
                _wait_for_http_health(fastapi_port, timeout_s=25.0),
                "FastAPI health endpoint did not become ready",
            )
            self.assertIsNone(
                manager_proc.poll(),
                "manager exited after FastAPI startup",
            )

            identity_after = _wait_for_manager_identity(manager_rpc, timeout_s=6.0)
            self.assertIsNotNone(identity_after, "manager identity probe failed after FastAPI start")
            assert isinstance(identity_after, dict)
            result_after = identity_after.get("result", {})
            self.assertIsInstance(result_after, dict)
            assert isinstance(result_after, dict)
            manager_pid_after = int(result_after.get("manager_pid", -1))
            self.assertEqual(manager_pid_after, manager_pid_before)
        finally:
            if fastapi_proc is not None:
                _terminate_process(fastapi_proc)
            _rpc_request(
                manager_rpc,
                {"type": "manager.control.shutdown"},
                timeout_ms=700,
            )
            _terminate_process(manager_proc)


if __name__ == "__main__":
    unittest.main()

