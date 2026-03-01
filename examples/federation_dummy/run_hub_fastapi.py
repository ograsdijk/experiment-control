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
    parser = argparse.ArgumentParser(
        "Run FastAPI gateway for the federation_dummy hub stack."
    )
    parser.add_argument(
        "--stack",
        default=str(Path(__file__).resolve().parent / "hub" / "stack.yaml"),
        help="Path to hub stack YAML (default: examples/federation_dummy/hub/stack.yaml)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Uvicorn bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8010,
        help="Uvicorn bind port (default: 8010)",
    )
    parser.add_argument(
        "--ui-dist",
        default="",
        help="Optional explicit React dist directory path.",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable UI static serving (API/WebSocket only).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn --reload.",
    )
    return parser.parse_args(argv)


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
        env["EXPERIMENT_CONTROL_UI_DIST"] = str(
            Path(ns.ui_dist).expanduser().resolve()
        )

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

    print(f"[federation-dummy-fastapi] stack: {stack_path}")
    print(f"[federation-dummy-fastapi] instance id: {instance_id}")
    print(f"[federation-dummy-fastapi] router rpc: {manager_rpc}")
    print(f"[federation-dummy-fastapi] manager pub: {manager_pub}")
    print(
        "[federation-dummy-fastapi] router rpc hint: "
        f"{manager_network.public_rpc_hint}"
    )
    print(
        "[federation-dummy-fastapi] manager pub hint: "
        f"{manager_network.public_pub_hint}"
    )
    print(f"[federation-dummy-fastapi] ui serving: {'off' if ns.no_ui else 'on'}")
    print(f"[federation-dummy-fastapi] url: http://{ns.host}:{int(ns.port)}")

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
