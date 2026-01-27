from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from experiment_control.utils.config_parsing import (
    optional_dict,
    require_dict,
    require_str,
)
from experiment_control.utils.manager_network import (
    ManagerNetworkConfig,
    resolve_manager_network,
)
from experiment_control.utils.yaml_helpers import load_yaml_file


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        "Run FastAPI gateway for dummy_frequency_trace_sequencer_3ch stack."
    )
    p.add_argument(
        "--stack",
        default=str(Path(__file__).resolve().parent / "stack.yaml"),
        help="Path to stack YAML (default: examples/dummy_frequency_trace_sequencer_3ch/stack.yaml)",
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="Uvicorn bind host (default: 0.0.0.0)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Uvicorn bind port (default: 8000)",
    )
    p.add_argument(
        "--ui-dist",
        default="",
        help="Optional explicit React dist directory path.",
    )
    p.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable UI static serving (API/WebSocket only).",
    )
    p.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn --reload.",
    )
    return p.parse_args(argv)


def _read_stack_config(stack_path: Path) -> tuple[ManagerNetworkConfig, str]:
    raw = load_yaml_file(stack_path)
    raw_obj = require_dict(raw, path=[])
    manager_raw = optional_dict(raw_obj.get("manager"), path=["manager"])
    instance_id = require_str(raw_obj.get("instance_id"), path=["instance_id"])
    return resolve_manager_network(manager_raw), instance_id


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    stack_path = Path(ns.stack).expanduser().resolve()

    manager_network, instance_id = _read_stack_config(stack_path)
    manager_rpc = manager_network.local_rpc_connect
    manager_pub = manager_network.local_pub_connect

    env = dict(os.environ)
    env["EXPERIMENT_CONTROL_INSTANCE_ID"] = instance_id
    env["EXPERIMENT_CONTROL_ROUTER_RPC"] = manager_rpc
    env["EXPERIMENT_CONTROL_MANAGER_PUB"] = manager_pub
    env["EXPERIMENT_CONTROL_ROUTER_RPC_HINT"] = manager_network.public_rpc_hint
    env["EXPERIMENT_CONTROL_MANAGER_PUB_HINT"] = manager_network.public_pub_hint
    env["EXPERIMENT_CONTROL_SERVE_UI"] = "0" if ns.no_ui else "1"
    if ns.ui_dist:
        env["EXPERIMENT_CONTROL_UI_DIST"] = str(Path(ns.ui_dist).expanduser().resolve())

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "experiment_control.fastapi.app:app",
        "--host",
        str(ns.host),
        "--port",
        str(int(ns.port)),
    ]
    if ns.reload:
        cmd.append("--reload")

    print(f"[fastapi-helper] stack: {str(stack_path)}")
    print(f"[fastapi-helper] instance id: {instance_id}")
    print(f"[fastapi-helper] router rpc: {manager_rpc}")
    print(f"[fastapi-helper] manager pub: {manager_pub}")
    print(f"[fastapi-helper] router rpc hint: {manager_network.public_rpc_hint}")
    print(f"[fastapi-helper] manager pub hint: {manager_network.public_pub_hint}")
    print(f"[fastapi-helper] ui serving: {'off' if ns.no_ui else 'on'}")
    print(f"[fastapi-helper] uvicorn: {' '.join(cmd)}")

    proc = subprocess.Popen(cmd, cwd=repo_root, env=env)
    try:
        return int(proc.wait())
    except KeyboardInterrupt:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return 130


if __name__ == "__main__":
    raise SystemExit(main())


