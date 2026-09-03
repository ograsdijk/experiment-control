from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from experiment_control.utils.config_parsing import optional_dict, require_dict
from experiment_control.utils.manager_network import resolve_manager_network
from experiment_control.utils.yaml_helpers import load_yaml_file

from generate_instance import GENERATED_ROOT, PRESET_NAMES, generate


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=PRESET_NAMES, default="baseline")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument(
        "--ui-dist",
        default="",
        help="Production Vite dist to serve (defaults to the packaged UI).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    # The stack launcher owns regeneration.  Reusing its output here matters on
    # Windows, where replacing the generated directory would try to unlink the
    # manager's open log files.  Gateway-first usage still creates the preset.
    generated = GENERATED_ROOT / args.preset
    if not (generated / "stack.yaml").is_file():
        generated = generate(args.preset)
    stack_path = generated / "stack.yaml"
    raw = require_dict(load_yaml_file(stack_path), path=[])
    manager = resolve_manager_network(optional_dict(raw.get("manager"), path=["manager"]))
    env = dict(os.environ)
    env.update(
        {
            "EXPERIMENT_CONTROL_INSTANCE_ID": str(raw["instance_id"]),
            "EXPERIMENT_CONTROL_ROUTER_RPC": manager.local_rpc_connect,
            "EXPERIMENT_CONTROL_MANAGER_PUB": manager.local_pub_connect,
            "EXPERIMENT_CONTROL_ROUTER_RPC_HINT": manager.public_rpc_hint,
            "EXPERIMENT_CONTROL_MANAGER_PUB_HINT": manager.public_pub_hint,
            "EXPERIMENT_CONTROL_SERVE_UI": "1",
        }
    )
    if args.ui_dist:
        env["EXPERIMENT_CONTROL_UI_DIST"] = str(Path(args.ui_dist).resolve())
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "experiment_control.fastapi.app:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    print(f"[performance-gateway] preset={args.preset} url=http://{args.host}:{args.port}")
    return subprocess.call(command, cwd=Path(__file__).resolve().parents[2], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
