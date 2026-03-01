from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import zmq

from ..federation import parse_federation_config
from ..utils.config_parsing import (
    ConfigError,
    normalize_list,
    optional_dict,
    require_dict,
    require_str,
)
from ..utils.manager_network import ManagerNetworkConfig, resolve_manager_network
from ..utils.yaml_helpers import load_yaml_file
from ..utils.zmq_helpers import json_dumps, safe_json_loads
from ..manager import Manager, device_spec_from_yaml, process_spec_from_yaml

Json = dict[str, Any]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control.cli.run_stack")
    p.add_argument("path", help="Path to stack YAML")
    p.add_argument("--no-tui", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def _load_yaml(path: Path) -> Json:
    raw = load_yaml_file(path)
    return require_dict(raw, path=[])


def _parse_instance_id(raw: Json) -> str:
    return require_str(raw.get("instance_id"), path=["instance_id"])


def _resolve_paths(items: list[Any], *, base: Path, label: str) -> list[Path]:
    paths: list[Path] = []
    for idx, item in enumerate(items):
        if not isinstance(item, str):
            raise ConfigError(f"{label}[{idx}]", "must be a string path")
        p = Path(item)
        if not p.is_absolute():
            p = base / p
        paths.append(p)
    return paths


def _collect_config_paths(
    section: Json | None,
    *,
    base: Path,
    label: str,
) -> list[Path]:
    if section is None:
        return []
    if not isinstance(section, dict):
        raise ConfigError(label, "must be a dict")

    dirs = normalize_list(section.get("dirs"), path=[label, "dirs"])
    files = normalize_list(section.get("files"), path=[label, "files"])
    glob_pat = section.get("glob", "*.yaml")
    if not isinstance(glob_pat, str) or not glob_pat:
        raise ConfigError(f"{label}.glob", "must be a non-empty string")

    paths: list[Path] = []
    for dir_path in _resolve_paths(dirs, base=base, label=f"{label}.dirs"):
        if not dir_path.exists() or not dir_path.is_dir():
            raise ConfigError(f"{label}.dirs", f"missing dir: {str(dir_path)!r}")
        paths.extend(sorted(dir_path.glob(glob_pat)))

    for file_path in _resolve_paths(files, base=base, label=f"{label}.files"):
        if not file_path.exists() or not file_path.is_file():
            raise ConfigError(f"{label}.files", f"missing file: {str(file_path)!r}")
        paths.append(file_path)

    return paths


def _parse_startup(raw: Json) -> Json:
    startup = raw.get("startup")
    if startup is None:
        return {}
    if not isinstance(startup, dict):
        raise ConfigError("startup", "must be a dict")
    return startup


def _parse_tui(raw: Json) -> Json:
    tui = raw.get("tui")
    if tui is None:
        return {}
    if not isinstance(tui, dict):
        raise ConfigError("tui", "must be a dict")
    return tui


def _order_processes(
    process_ids: list[str], order_list: list[str] | None
) -> list[str]:
    strict = True
    if not order_list:
        order_list = ["hdf_writer"]
        strict = False
    missing = [pid for pid in order_list if pid not in process_ids]
    if missing and strict:
        raise ConfigError("startup.process_order", f"unknown process_id(s): {missing}")
    ordered: list[str] = []
    seen = set()
    for pid in order_list:
        if pid not in process_ids:
            continue
        if pid in seen:
            continue
        ordered.append(pid)
        seen.add(pid)
    for pid in sorted(process_ids):
        if pid not in seen:
            ordered.append(pid)
    return ordered


def _shutdown_manager(manager_rpc: str) -> None:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, 1000)
    sock.connect(manager_rpc)
    try:
        sock.send(json_dumps({"type": "manager.shutdown"}))
        sock.recv()
    except Exception:
        pass
    sock.close(0)


def _wait_for_exit(proc: subprocess.Popen[str], timeout_s: float) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=timeout_s)
        return
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=3.0)
    except Exception:
        pass


def _probe_manager_ready(
    manager_rpc: str,
    *,
    timeout_ms: int = 250,
    expected_instance_id: str | None = None,
) -> bool:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(manager_rpc)
    request_id = f"startup-{time.monotonic_ns()}"
    try:
        sock.send(json_dumps({"type": "manager.identity", "request_id": request_id}))
        if not sock.poll(int(timeout_ms), zmq.POLLIN):
            return False
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
        if expected_instance_id is None:
            return True
        result = resp.get("result", {})
        if not isinstance(result, dict):
            return False
        return str(result.get("instance_id", "")) == expected_instance_id
    except Exception:
        return False
    finally:
        sock.close(0)


def _wait_for_manager_ready(
    *,
    manager_rpc: str,
    manager_proc: subprocess.Popen[str],
    expected_instance_id: str | None,
    startup_delay_s: float,
    startup_timeout_s: float,
    probe_timeout_ms: int,
    poll_interval_s: float = 0.1,
    probe_fn: Callable[..., bool] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    clock_fn: Callable[[], float] | None = None,
) -> tuple[bool, str | None]:
    probe = probe_fn or _probe_manager_ready
    sleep = sleep_fn or time.sleep
    clock = clock_fn or time.monotonic

    def _exit_message(exit_code: int | None) -> str:
        return (
            "stack subprocess exited before manager became ready "
            f"(exit code {exit_code})"
        )

    remaining_delay_s = max(0.0, float(startup_delay_s))
    while remaining_delay_s > 0:
        exit_code = manager_proc.poll()
        if exit_code is not None:
            return False, _exit_message(exit_code)
        step_s = remaining_delay_s
        if poll_interval_s > 0:
            step_s = min(step_s, poll_interval_s)
        sleep(step_s)
        remaining_delay_s -= step_s

    deadline = clock() + max(0.0, float(startup_timeout_s))
    while True:
        exit_code = manager_proc.poll()
        if exit_code is not None:
            return False, _exit_message(exit_code)
        if probe(
            manager_rpc,
            timeout_ms=int(probe_timeout_ms),
            expected_instance_id=expected_instance_id,
        ):
            return True, None
        remaining_s = deadline - clock()
        if remaining_s <= 0:
            target = "manager"
            if expected_instance_id:
                target = f"manager for instance {expected_instance_id!r}"
            return (
                False,
                f"{target} did not become ready at {manager_rpc!r} "
                f"within {float(startup_timeout_s):.1f}s",
            )
        if poll_interval_s > 0:
            sleep(min(poll_interval_s, remaining_s))


def _run_with_tui(
    *,
    instance_id: str,
    stack_path: Path,
    manager_network: ManagerNetworkConfig,
    tui_raw: Json,
) -> None:
    manager_rpc = manager_network.local_rpc_connect
    manager_pub = manager_network.local_pub_connect
    rpc_timeout_ms = int(tui_raw.get("rpc_timeout_ms", 1500))
    snapshot_period_s = float(tui_raw.get("snapshot_period_s", 2.0))
    startup_delay_s = float(tui_raw.get("startup_delay_s", 1.0))
    startup_timeout_s = float(tui_raw.get("startup_timeout_s", 15.0))
    probe_timeout_ms = min(500, max(100, rpc_timeout_ms))

    manager_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "experiment_control.cli.run_stack",
            str(stack_path),
            "--no-tui",
        ],
    )
    manager_ready = False

    try:
        manager_ready, startup_error = _wait_for_manager_ready(
            manager_rpc=str(manager_rpc),
            manager_proc=manager_proc,
            expected_instance_id=instance_id,
            startup_delay_s=startup_delay_s,
            startup_timeout_s=startup_timeout_s,
            probe_timeout_ms=probe_timeout_ms,
        )
        if not manager_ready:
            raise SystemExit(f"[run_stack] startup error: {startup_error}")
        from ..tui_manager import ManagerTUI

        app = ManagerTUI(
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            rpc_timeout_ms=rpc_timeout_ms,
            snapshot_period_s=snapshot_period_s,
        )
        app.run()
    finally:
        if manager_ready:
            _shutdown_manager(str(manager_rpc))
            _wait_for_exit(manager_proc, 5.0)
        else:
            _wait_for_exit(manager_proc, 0.0)


def main(argv: list[str] | None = None) -> None:
    try:
        ns = _parse_args(argv)
        stack_path = Path(ns.path).expanduser().resolve()
        raw = _load_yaml(stack_path)
        instance_id = _parse_instance_id(raw)

        manager_raw = optional_dict(raw.get("manager"), path=["manager"])
        manager_network = resolve_manager_network(manager_raw)
        tui_raw = _parse_tui(raw)
        if tui_raw.get("enabled") and not ns.no_tui:
            _run_with_tui(
                instance_id=instance_id,
                stack_path=stack_path,
                manager_network=manager_network,
                tui_raw=tui_raw,
            )
            return

        base_dir = stack_path.parent
        device_paths = _collect_config_paths(
            raw.get("devices"), base=base_dir, label="devices"
        )
        process_paths = _collect_config_paths(
            raw.get("processes"), base=base_dir, label="processes"
        )

        device_specs = []
        seen_devices: set[str] = set()
        for dev_path in device_paths:
            spec = device_spec_from_yaml(dev_path)
            if spec.device_id in seen_devices:
                raise ConfigError(
                    "devices", f"duplicate device_id {spec.device_id!r}"
                )
            seen_devices.add(spec.device_id)
            device_specs.append(spec)

        federation_config = parse_federation_config(
            raw.get("federation"),
            local_device_ids=seen_devices,
            manager_raw=manager_raw,
        )

        manager = Manager(
            instance_id=instance_id,
            federation_config=federation_config,
            registry_bind=manager_network.registry_bind,
            internal_rpc_bind=manager_network.internal_rpc_bind,
            external_rpc_bind=manager_network.external_rpc_bind,
            external_pub_bind=manager_network.external_pub_bind,
            external_pub_connect_local=manager_network.local_pub_connect,
            process_hb_bind_base=manager_network.process_hb_bind_base,
            process_data_bind_base=manager_network.process_data_bind_base,
            heartbeat_timeout_s=float(manager_raw.get("heartbeat_timeout_s", 3.0)),
            telemetry_stale_s=float(manager_raw.get("telemetry_stale_s", 10.0)),
            device_rpc_timeout_ms=int(manager_raw.get("device_rpc_timeout_ms", 1500)),
            interceptor_rpc_timeout_ms=int(manager_raw.get("interceptor_rpc_timeout_ms", 500)),
            auto_connect_on_register=bool(
                manager_raw.get("auto_connect_on_register", True)
            ),
        )

        process_manager_rpc = manager_network.local_rpc_connect
        process_manager_pub = manager_network.local_pub_connect

        for spec in device_specs:
            manager.add_device(spec)

        seen_processes: set[str] = set()
        for proc_path in process_paths:
            spec = process_spec_from_yaml(
                proc_path,
                manager_rpc=process_manager_rpc,
                manager_pub=process_manager_pub,
            )
            if spec.process_id in seen_processes:
                raise ConfigError(
                    "processes", f"duplicate process_id {spec.process_id!r}"
                )
            seen_processes.add(spec.process_id)
            manager.add_process(spec)

        startup = _parse_startup(raw)
        start_devices = bool(startup.get("start_devices", True))
        start_processes = bool(startup.get("start_processes", True))
        process_order_raw = startup.get("process_order")
        if process_order_raw is not None and not isinstance(process_order_raw, list):
            raise ConfigError("startup.process_order", "must be a list[str]")
        process_order = None
        if process_order_raw is not None:
            process_order = [str(pid) for pid in process_order_raw]

        if start_processes and seen_processes:
            ordered = _order_processes(sorted(seen_processes), process_order)
            for pid in ordered:
                manager.start_process(pid)

        wait_processes_running = startup.get("wait_processes_running")
        if wait_processes_running is None:
            wait_processes_running = start_processes
        connect = startup.get("connect", None)
        wait_for_registered = bool(startup.get("wait_for_registered", True))
        wait_for_online = bool(startup.get("wait_for_online", True))
        if connect is False and wait_for_online:
            sys.stderr.write(
                "[run_stack] warning: wait_for_online ignored because connect is false.\n"
            )
            wait_for_online = False
        timeout_s = float(startup.get("timeout_s", 10.0))
        poll_ms = int(startup.get("poll_ms", 50))

        try:
            manager.startup_sequence(
                start_drivers=start_devices,
                start_processes=False,
                wait_processes_running=bool(wait_processes_running),
                connect=connect,
                wait_for_registered=wait_for_registered,
                wait_for_online=wait_for_online,
                timeout_s=timeout_s,
                poll_ms=poll_ms,
            )
        except TimeoutError as e:
            sys.stderr.write(f"[run_stack] warning: {e}\n")

        try:
            manager.run_forever()
        except KeyboardInterrupt:
            manager._shutdown_cleanup()
    except ConfigError as e:
        raise SystemExit(f"[run_stack] config error: {e}") from None


if __name__ == "__main__":
    main()
