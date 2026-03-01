from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

import zmq

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
from experiment_control.utils.zmq_helpers import json_dumps, safe_json_loads


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        "Run the federation_dummy example: leaf stack + hub stack + hub FastAPI UI."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="FastAPI host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8010,
        help="FastAPI port (default: 8010)",
    )
    parser.add_argument(
        "--ui-dist",
        default="",
        help="Optional explicit React dist directory path.",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable static UI serving (API/WebSocket only).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn --reload for the FastAPI process.",
    )
    parser.add_argument(
        "--startup-timeout-s",
        type=float,
        default=20.0,
        help="Per-service startup timeout in seconds (default: 20.0)",
    )
    return parser.parse_args(argv)


def _read_stack_config(stack_path: Path) -> tuple[ManagerNetworkConfig, str]:
    raw = load_yaml_file(stack_path)
    raw_obj = require_dict(raw, path=[])
    manager_raw = optional_dict(raw_obj.get("manager"), path=["manager"])
    instance_id = require_str(raw_obj.get("instance_id"), path=["instance_id"])
    return resolve_manager_network(manager_raw), instance_id


def _probe_manager_ready(
    manager_rpc: str,
    *,
    expected_instance_id: str,
    timeout_ms: int = 400,
) -> bool:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, int(timeout_ms))
    sock.setsockopt(zmq.SNDTIMEO, int(timeout_ms))
    sock.connect(manager_rpc)
    request_id = f"federation-dummy-{time.monotonic_ns()}"
    try:
        sock.send(
            json_dumps({"type": "manager.identity", "request_id": request_id})
        )
        raw = sock.recv()
        resp = safe_json_loads(raw)
        if not isinstance(resp, dict):
            return False
        if (
            resp.get("request_id") is not None
            and str(resp.get("request_id")) != request_id
        ):
            return False
        if not resp.get("ok"):
            return False
        result = resp.get("result", {})
        if not isinstance(result, dict):
            return False
        return str(result.get("instance_id", "")) == expected_instance_id
    except Exception:
        return False
    finally:
        sock.close(0)


def _wait_for_manager(
    proc: subprocess.Popen[str],
    *,
    label: str,
    manager_rpc: str,
    expected_instance_id: str,
    timeout_s: float,
) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while time.monotonic() < deadline:
        exit_code = proc.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"{label} exited before startup completed (exit code {exit_code})"
            )
        if _probe_manager_ready(
            manager_rpc, expected_instance_id=expected_instance_id
        ):
            return
        time.sleep(0.1)
    raise RuntimeError(
        f"{label} did not become ready at {manager_rpc!r} within {float(timeout_s):.1f}s"
    )


def _wait_for_tcp_listener(
    proc: subprocess.Popen[str],
    *,
    label: str,
    host: str,
    port: int,
    timeout_s: float,
) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while time.monotonic() < deadline:
        exit_code = proc.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"{label} exited before startup completed (exit code {exit_code})"
            )
        try:
            with socket.create_connection((host, int(port)), timeout=0.2):
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(
        f"{label} did not begin listening on {host}:{int(port)} within {float(timeout_s):.1f}s"
    )


def _shutdown_manager(manager_rpc: str) -> None:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, 1000)
    sock.setsockopt(zmq.SNDTIMEO, 1000)
    sock.connect(manager_rpc)
    try:
        sock.send(json_dumps({"type": "manager.shutdown"}))
        sock.recv()
    except Exception:
        pass
    finally:
        sock.close(0)


def _terminate_process(
    proc: subprocess.Popen[str] | None,
    *,
    name: str,
    allow_manager_shutdown: bool = False,
    manager_rpc: str | None = None,
) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    print(f"[federation-dummy] stopping {name}")
    if allow_manager_shutdown and manager_rpc:
        _shutdown_manager(manager_rpc)
        try:
            proc.wait(timeout=5.0)
            return
        except Exception:
            pass
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5.0)
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


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(argv)
    example_dir = Path(__file__).resolve().parent
    repo_root = example_dir.parents[1]

    leaf_stack = example_dir / "leaf" / "stack.yaml"
    hub_stack = example_dir / "hub" / "stack.yaml"

    leaf_network, leaf_instance_id = _read_stack_config(leaf_stack)
    hub_network, hub_instance_id = _read_stack_config(hub_stack)

    leaf_proc: subprocess.Popen[str] | None = None
    hub_proc: subprocess.Popen[str] | None = None
    fastapi_proc: subprocess.Popen[str] | None = None

    try:
        leaf_cmd = [
            sys.executable,
            "-m",
            "experiment_control.cli.run_stack",
            str(leaf_stack),
        ]
        print(f"[federation-dummy] starting leaf: {' '.join(leaf_cmd)}")
        leaf_proc = subprocess.Popen(leaf_cmd, cwd=repo_root)
        _wait_for_manager(
            leaf_proc,
            label="leaf stack",
            manager_rpc=leaf_network.local_rpc_connect,
            expected_instance_id=leaf_instance_id,
            timeout_s=ns.startup_timeout_s,
        )
        print(
            "[federation-dummy] leaf ready at "
            f"{leaf_network.local_rpc_connect} / {leaf_network.local_pub_connect}"
        )

        hub_cmd = [
            sys.executable,
            "-m",
            "experiment_control.cli.run_stack",
            str(hub_stack),
        ]
        print(f"[federation-dummy] starting hub: {' '.join(hub_cmd)}")
        hub_proc = subprocess.Popen(hub_cmd, cwd=repo_root)
        _wait_for_manager(
            hub_proc,
            label="hub stack",
            manager_rpc=hub_network.local_rpc_connect,
            expected_instance_id=hub_instance_id,
            timeout_s=ns.startup_timeout_s,
        )
        print(
            "[federation-dummy] hub ready at "
            f"{hub_network.local_rpc_connect} / {hub_network.local_pub_connect}"
        )

        fastapi_cmd = [
            sys.executable,
            str(example_dir / "run_hub_fastapi.py"),
            "--host",
            str(ns.host),
            "--port",
            str(int(ns.port)),
        ]
        if ns.ui_dist:
            fastapi_cmd.extend(
                ["--ui-dist", str(Path(ns.ui_dist).expanduser().resolve())]
            )
        if ns.no_ui:
            fastapi_cmd.append("--no-ui")
        if ns.reload:
            fastapi_cmd.append("--reload")
        print(f"[federation-dummy] starting FastAPI: {' '.join(fastapi_cmd)}")
        fastapi_proc = subprocess.Popen(fastapi_cmd, cwd=repo_root)
        _wait_for_tcp_listener(
            fastapi_proc,
            label="hub FastAPI",
            host=str(ns.host),
            port=int(ns.port),
            timeout_s=ns.startup_timeout_s,
        )
        print(f"[federation-dummy] hub web UI: http://{ns.host}:{int(ns.port)}")
        print("[federation-dummy] press Ctrl+C to stop all three processes")

        return int(fastapi_proc.wait())
    except KeyboardInterrupt:
        return 130
    finally:
        _terminate_process(fastapi_proc, name="fastapi")
        _terminate_process(
            hub_proc,
            name="hub stack",
            allow_manager_shutdown=True,
            manager_rpc=hub_network.local_rpc_connect,
        )
        _terminate_process(
            leaf_proc,
            name="leaf stack",
            allow_manager_shutdown=True,
            manager_rpc=leaf_network.local_rpc_connect,
        )


if __name__ == "__main__":
    raise SystemExit(main())
