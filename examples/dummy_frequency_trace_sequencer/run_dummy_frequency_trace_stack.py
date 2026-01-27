from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import zmq

from experiment_control.utils.config_parsing import optional_dict, require_dict
from experiment_control.utils.manager_network import resolve_manager_network
from experiment_control.utils.yaml_helpers import load_yaml_file
from experiment_control.utils.zmq_helpers import json_dumps, json_loads

Json = dict[str, Any]


def _shutdown_manager(manager_rpc: str) -> None:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, 1000)
    sock.connect(manager_rpc)
    try:
        sock.send(json_dumps({"type": "manager.shutdown"}))
        sock.recv()
    except BaseException:
        pass
    sock.close(0)


def _manager_rpc_from_stack(stack_path: Path) -> str:
    default = "tcp://127.0.0.1:6000"
    try:
        raw = load_yaml_file(stack_path)
        raw_obj = require_dict(raw, path=[])
        manager_raw = optional_dict(raw_obj.get("manager"), path=["manager"])
        return resolve_manager_network(manager_raw).local_rpc_connect
    except Exception:
        return default


def _rpc(sock: zmq.Socket, req: Json) -> Json:
    try:
        sock.send(json_dumps(req))
        raw = sock.recv()
        resp = json_loads(raw)
        if isinstance(resp, dict):
            return resp
        return {"ok": False, "error": {"code": "bad_response"}}
    except Exception as e:
        return {
            "ok": False,
            "error": {"code": "rpc_failed", "message": str(e)},
        }


def _wait_for_sequencer_rpc_ready(sock: zmq.Socket, timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = _rpc(
            sock,
            {
                "type": "process.rpc",
                "process_id": "sequencer",
                "request": {"type": "sequencer.status", "params": {}},
            },
        )
        if probe.get("ok", False):
            return True
        time.sleep(0.2)
    return False


def _autoload_sequence(sock: zmq.Socket, sequence_path: Path) -> None:
    if not _wait_for_sequencer_rpc_ready(sock):
        print("[dummy-frequency-trace] sequencer RPC not ready; skipping autoload")
        return
    load_resp = _rpc(
        sock,
        {
            "type": "process.rpc",
            "process_id": "sequencer",
            "request": {"type": "sequencer.load", "params": {"path": str(sequence_path)}},
        },
    )
    if load_resp.get("ok", False):
        print(f"[dummy-frequency-trace] loaded sequence: {str(sequence_path)}")
        return
    err = load_resp.get("error")
    print(f"[dummy-frequency-trace] sequence autoload failed: {err!r}")


def _start_processes(
    sock: zmq.Socket, process_ids: list[str], timeout_s: float = 20.0
) -> None:
    for process_id in process_ids:
        deadline = time.monotonic() + max(0.1, timeout_s)
        while True:
            resp = _rpc(sock, {"type": "process.start", "process_id": process_id})
            if resp.get("ok", False):
                print(f"[dummy-frequency-trace] started process: {process_id}")
                break
            if time.monotonic() >= deadline:
                print(
                    f"[dummy-frequency-trace] failed to start process {process_id}: {resp.get('error')!r}"
                )
                break
            time.sleep(0.2)


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[2]
    example_dir = Path(__file__).resolve().parent
    stack_path = example_dir / "stack.yaml"
    sequence_path = (example_dir / "sequence_frequency_sweep.yaml").resolve()
    manager_rpc = _manager_rpc_from_stack(stack_path)

    # Best-effort cleanup in case a previous stack run was interrupted and
    # left manager/router sockets bound.
    _shutdown_manager(manager_rpc)
    time.sleep(0.2)

    manager_proc = subprocess.Popen(
        [sys.executable, "-m", "experiment_control.cli.run_stack", str(stack_path)],
        cwd=repo_root,
    )

    ctx = zmq.Context.instance()
    rpc_sock = ctx.socket(zmq.DEALER)
    rpc_sock.setsockopt(zmq.LINGER, 0)
    rpc_sock.setsockopt(zmq.RCVTIMEO, 1000)
    rpc_sock.connect(manager_rpc)

    try:
        _start_processes(rpc_sock, ["sequencer", "stream_analysis", "hdf_writer"])
        _autoload_sequence(rpc_sock, sequence_path)
        manager_proc.wait()
    finally:
        rpc_sock.close(0)
        _shutdown_manager(manager_rpc)
        if manager_proc.poll() is None:
            manager_proc.wait(timeout=5.0)
        if manager_proc.poll() is None:
            manager_proc.terminate()
            manager_proc.wait(timeout=3.0)
