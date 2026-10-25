"""Launch the examples/performance_test stack + gateway and sample their CPU."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PERF_DIR = REPO_ROOT / "examples" / "performance_test"

try:  # optional: `uv run --with psutil ...` enables backend CPU sampling
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


def _get_json(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def wait_for_gateway(base_url: str, *, min_devices: int = 1, timeout: float = 120.0) -> None:
    """Block until the gateway answers and reports at least `min_devices` devices."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = _get_json(f"{base_url}/api/devices")
            items = payload.get("result", []) if isinstance(payload, dict) else payload
            online = [d for d in items if d.get("liveness") == "ONLINE"]
            if len(online) >= min_devices:
                return
        except Exception as exc:  # gateway or manager still starting
            last_error = exc
        time.sleep(0.5)
    raise TimeoutError(f"gateway at {base_url} not ready: {last_error!r}")


def preset_device_count(preset: str) -> int:
    import yaml

    raw = yaml.safe_load((PERF_DIR / "performance_presets.yaml").read_text())
    cfg = raw["presets"][preset]
    return int(cfg["telemetry_devices"]) + int(cfg["trace_devices"])


class PerfStack:
    """Owns the stack and gateway subprocesses for one preset."""

    def __init__(
        self,
        preset: str,
        *,
        port: int = 8088,
        ui_dist: Path | None = None,
        ui_processes: bool = False,
    ) -> None:
        self.preset = preset
        self.ui_processes = ui_processes
        self.port = port
        self.ui_dist = ui_dist
        self.base_url = f"http://127.0.0.1:{port}"
        self._stack: subprocess.Popen[bytes] | None = None
        self._gateway: subprocess.Popen[bytes] | None = None
        self._logs: list[Any] = []

    def __enter__(self) -> "PerfStack":
        try:
            self.start()
        except BaseException:
            self.stop()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _spawn(self, args: list[str], log_name: str) -> subprocess.Popen[bytes]:
        log_dir = REPO_ROOT / "bench" / "ui" / "results" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / f"{log_name}-{self.preset}.log", "wb")
        self._logs.append(log)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        return subprocess.Popen(
            [sys.executable, *args],
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )

    def start(self) -> None:
        stack_args = [str(PERF_DIR / "run_performance_test.py"), "--preset", self.preset]
        if self.ui_processes:
            stack_args.append("--ui-processes")
        self._stack = self._spawn(stack_args, "stack")
        # The gateway reuses the stack's generated config; give generation a head start.
        time.sleep(3.0)
        gateway_args = [
            str(PERF_DIR / "run_performance_test_fastapi.py"),
            "--preset",
            self.preset,
            "--port",
            str(self.port),
        ]
        if self.ui_dist is not None:
            gateway_args += ["--ui-dist", str(self.ui_dist)]
        self._gateway = self._spawn(gateway_args, "gateway")
        wait_for_gateway(self.base_url, min_devices=preset_device_count(self.preset))

    def stop(self) -> None:
        if self._gateway is not None and self._gateway.poll() is None:
            _terminate_tree(self._gateway.pid)
        if self._stack is not None and self._stack.poll() is None:
            # Same clean path the launcher uses on exit: the manager stops its runners.
            shutdown_manager(self.preset)
            try:
                self._stack.wait(timeout=30)
            except subprocess.TimeoutExpired:
                _terminate_tree(self._stack.pid)
        for log in self._logs:
            log.close()
        self._logs.clear()


def shutdown_manager(preset: str) -> None:
    import zmq

    from experiment_control.utils.config_parsing import optional_dict, require_dict
    from experiment_control.utils.manager_network import resolve_manager_network
    from experiment_control.utils.yaml_helpers import load_yaml_file
    from experiment_control.utils.zmq_helpers import json_dumps

    stack_path = PERF_DIR / ".generated" / preset / "stack.yaml"
    if not stack_path.is_file():
        return
    raw = require_dict(load_yaml_file(stack_path), path=[])
    manager = resolve_manager_network(optional_dict(raw.get("manager"), path=["manager"]))
    sock = zmq.Context.instance().socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, 2000)
    sock.connect(manager.local_rpc_connect)
    try:
        sock.send(json_dumps({"type": "manager.control.shutdown"}))
        sock.recv()
    except Exception:
        pass
    finally:
        sock.close(0)


def _terminate_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.call(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    if psutil is not None:
        try:
            parent = psutil.Process(pid)
            procs = [parent, *parent.children(recursive=True)]
        except psutil.NoSuchProcess:
            return
        for proc in procs:
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(procs, timeout=10)
        return
    os.kill(pid, 15)


# ---------------------------------------------------------------------------
# Backend CPU sampling
# ---------------------------------------------------------------------------


def find_gateway_pid(port: int) -> int | None:
    if psutil is None:
        return None
    for conn in psutil.net_connections(kind="tcp"):
        if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
            return conn.pid
    return None


def find_stack_pids() -> list[int]:
    """The performance-test stack: its run_stack process and every descendant.

    Only stacks started from examples/performance_test count, so other
    experiment-control instances running on the same machine (e.g. the lab's
    own on an acquisition PC) are not mixed into the stack CPU figure.
    """
    if psutil is None:
        return []
    pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmd = " ".join(proc.info.get("cmdline") or [])
        if "experiment_control.cli.run_stack" not in cmd or "performance_test" not in cmd:
            continue
        pids.add(proc.info["pid"])
        try:
            pids.update(child.pid for child in proc.children(recursive=True))
        except psutil.NoSuchProcess:
            pass
    return sorted(pids)


class CpuSampler:
    """Process CPU-seconds delta over an interval (all cores; 100% = one core)."""

    def __init__(self, pids: list[int]) -> None:
        self.procs = []
        if psutil is None:
            return
        for pid in pids:
            try:
                self.procs.append(psutil.Process(pid))
            except psutil.NoSuchProcess:
                pass
        self._t0 = 0.0
        self._cpu0: dict[int, float] = {}

    @staticmethod
    def _cpu(proc: Any) -> float:
        try:
            t = proc.cpu_times()
            return float(t.user + t.system)
        except Exception:
            return 0.0

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._cpu0 = {p.pid: self._cpu(p) for p in self.procs}

    def stop(self) -> dict[str, float] | None:
        if not self.procs:
            return None
        wall = max(1e-6, time.monotonic() - self._t0)
        total = sum(self._cpu(p) - self._cpu0.get(p.pid, 0.0) for p in self.procs)
        rss = 0
        for p in self.procs:
            try:
                rss += p.memory_info().rss
            except Exception:
                pass
        return {"cpu_pct": 100.0 * total / wall, "rss_mb": rss / 2**20, "processes": len(self.procs)}
