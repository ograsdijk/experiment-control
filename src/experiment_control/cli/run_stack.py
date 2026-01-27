from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import zmq

from ..utils.config_parsing import (
    ConfigError,
    normalize_list,
    optional_dict,
    require_dict,
    require_str,
)
from ..utils.manager_network import ManagerNetworkConfig, resolve_manager_network
from ..utils.yaml_helpers import load_yaml_file
from ..utils.zmq_helpers import json_dumps
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


def _run_with_tui(
    *,
    stack_path: Path,
    manager_network: ManagerNetworkConfig,
    tui_raw: Json,
) -> None:
    manager_rpc = manager_network.local_rpc_connect
    manager_pub = manager_network.local_pub_connect
    rpc_timeout_ms = int(tui_raw.get("rpc_timeout_ms", 1500))
    snapshot_period_s = float(tui_raw.get("snapshot_period_s", 2.0))
    startup_delay_s = float(tui_raw.get("startup_delay_s", 1.0))

    manager_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "experiment_control.cli.run_stack",
            str(stack_path),
            "--no-tui",
        ],
    )

    try:
        if startup_delay_s > 0:
            time.sleep(startup_delay_s)
        from ..tui_manager import ManagerTUI

        app = ManagerTUI(
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            rpc_timeout_ms=rpc_timeout_ms,
            snapshot_period_s=snapshot_period_s,
        )
        app.run()
    finally:
        _shutdown_manager(str(manager_rpc))
        _wait_for_exit(manager_proc, 5.0)


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
                stack_path=stack_path,
                manager_network=manager_network,
                tui_raw=tui_raw,
            )
            return

        manager = Manager(
            instance_id=instance_id,
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

        base_dir = stack_path.parent
        device_paths = _collect_config_paths(
            raw.get("devices"), base=base_dir, label="devices"
        )
        process_paths = _collect_config_paths(
            raw.get("processes"), base=base_dir, label="processes"
        )
        process_manager_rpc = manager_network.local_rpc_connect
        process_manager_pub = manager_network.local_pub_connect

        seen_devices: set[str] = set()
        for dev_path in device_paths:
            spec = device_spec_from_yaml(dev_path)
            if spec.device_id in seen_devices:
                raise ConfigError(
                    "devices", f"duplicate device_id {spec.device_id!r}"
                )
            seen_devices.add(spec.device_id)
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
