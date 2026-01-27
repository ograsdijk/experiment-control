import subprocess
import sys
from pathlib import Path

import zmq

from experiment_control.utils.config_parsing import optional_dict, require_dict
from experiment_control.utils.manager_network import resolve_manager_network
from experiment_control.utils.yaml_helpers import load_yaml_file
from experiment_control.utils.zmq_helpers import json_dumps


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


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[2]
    stack_path = Path(__file__).resolve().parent / "stack.yaml"
    manager_rpc = _manager_rpc_from_stack(stack_path)

    manager_proc = subprocess.Popen(
        [sys.executable, "-m", "experiment_control.cli.run_stack", str(stack_path)],
        cwd=repo_root,
    )

    try:
        manager_proc.wait()
    finally:
        _shutdown_manager(manager_rpc)
        if manager_proc.poll() is None:
            manager_proc.wait(timeout=5.0)
        if manager_proc.poll() is None:
            manager_proc.terminate()
            manager_proc.wait(timeout=3.0)
