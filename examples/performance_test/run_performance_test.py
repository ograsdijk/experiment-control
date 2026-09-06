from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import zmq

from experiment_control.utils.config_parsing import optional_dict, require_dict
from experiment_control.utils.manager_network import resolve_manager_network
from experiment_control.utils.yaml_helpers import load_yaml_file
from experiment_control.utils.zmq_helpers import json_dumps

from generate_instance import PRESET_NAMES, generate


def _shutdown_manager(manager_rpc: str) -> None:
    context = zmq.Context.instance()
    socket = context.socket(zmq.DEALER)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVTIMEO, 1000)
    socket.connect(manager_rpc)
    try:
        socket.send(json_dumps({"type": "manager.control.shutdown"}))
        socket.recv()
    except BaseException:
        pass
    socket.close(0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=PRESET_NAMES, default="baseline")
    return parser.parse_args()


def main() -> int:
    preset = _parse_args().preset
    generated = generate(preset)
    stack_path = generated / "stack.yaml"
    raw = require_dict(load_yaml_file(stack_path), path=[])
    manager = resolve_manager_network(optional_dict(raw.get("manager"), path=["manager"]))
    _shutdown_manager(manager.local_rpc_connect)
    time.sleep(0.2)
    command = [
        sys.executable,
        "-m",
        "experiment_control.cli.run_stack",
        str(stack_path),
    ]
    repo_root = Path(__file__).resolve().parents[2]
    if os.name == "nt":
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        process = subprocess.Popen(command, cwd=repo_root, start_new_session=True)
    print(f"[performance-test] preset={preset} stack={stack_path}")
    try:
        return int(process.wait())
    except KeyboardInterrupt:
        print("\n[performance-test] stopping stack")
        return 130
    finally:
        _shutdown_manager(manager.local_rpc_connect)
        if process.poll() is None:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5.0)


if __name__ == "__main__":
    raise SystemExit(main())
