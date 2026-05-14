from __future__ import annotations

import argparse
import os
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
from ..utils.logging_levels import (
    LOG_SEVERITY_NAMES,
    is_valid_log_severity,
    normalize_log_severity,
)
from ..utils.instance_lock import InstanceLock, InstanceLockActiveError
from ..utils.manager_network import ManagerNetworkConfig, resolve_manager_network
from ..utils.process_lifecycle import cleanup_orphan_children
from ..utils.yaml_helpers import load_yaml_file
from ..utils.zmq_helpers import json_dumps, safe_json_loads
from ..manager import Manager, device_spec_from_yaml, process_spec_from_yaml

Json = dict[str, Any]


class _NoopInstanceLock:
    def acquire(self) -> None:
        return

    def release(self) -> None:
        return


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control.cli.run_stack")
    p.add_argument("path", help="Path to stack YAML")
    p.add_argument("--no-tui", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--cleanup-orphans",
        action="store_true",
        help="Run stale orphan child cleanup before stack startup.",
    )
    p.add_argument(
        "--instance-lock",
        action="store_true",
        help="Acquire a per-instance startup lock while manager is running.",
    )
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
    out = dict(tui)

    def parse_int(name: str, *, default: int, min_value: int) -> int:
        value = tui.get(name, default)
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as e:
            raise ConfigError(f"tui.{name}", f"must be an int: {e}") from e
        if parsed < min_value:
            raise ConfigError(f"tui.{name}", f"must be >= {min_value}")
        return parsed

    if "event_log_max_lines" in tui:
        out["event_log_max_lines"] = parse_int(
            "event_log_max_lines", default=10_000, min_value=100
        )
    if "pub_queue_maxsize" in tui:
        out["pub_queue_maxsize"] = parse_int(
            "pub_queue_maxsize", default=10_000, min_value=1
        )

    if "event_log_default_hidden_topics" in tui:
        hidden_topics_raw = tui.get("event_log_default_hidden_topics")
        if hidden_topics_raw is None:
            out["event_log_default_hidden_topics"] = None
        else:
            items = normalize_list(
                hidden_topics_raw,
                path=["tui", "event_log_default_hidden_topics"],
            )
            hidden_topics: list[str] = []
            for idx, item in enumerate(items):
                text = str(item or "").strip()
                if not text:
                    raise ConfigError(
                        f"tui.event_log_default_hidden_topics[{idx}]",
                        "must be a non-empty string",
                    )
                hidden_topics.append(text)
            out["event_log_default_hidden_topics"] = hidden_topics

    if "event_log_manager_min_severity" in tui:
        min_severity_raw = tui.get("event_log_manager_min_severity")
        min_severity = str(min_severity_raw or "").strip().lower()
        if not min_severity:
            raise ConfigError(
                "tui.event_log_manager_min_severity", "must be a non-empty string"
            )
        if not is_valid_log_severity(min_severity):
            raise ConfigError(
                "tui.event_log_manager_min_severity",
                f"must be one of: {', '.join(LOG_SEVERITY_NAMES)}",
            )
        out["event_log_manager_min_severity"] = normalize_log_severity(
            min_severity, default="warning"
        )

    if "pub_queue_overflow_policy" in tui:
        overflow_policy_raw = str(
            tui.get("pub_queue_overflow_policy", "drop_newest") or ""
        ).strip().lower()
        if overflow_policy_raw not in {"drop_newest", "drop_oldest"}:
            raise ConfigError(
                "tui.pub_queue_overflow_policy",
                "must be one of: drop_newest, drop_oldest",
            )
        out["pub_queue_overflow_policy"] = overflow_policy_raw

    return out


def _parse_command_journal(
    *,
    manager_raw: Json,
    base_dir: Path,
    instance_id: str,
) -> Json:
    raw = manager_raw.get("command_journal")
    if raw is None:
        return {
            "enabled": True,
            "path": None,
            "queue_max": 10_000,
            "batch_size": 200,
            "flush_interval_ms": 200,
            "retention_max_rows": 1_000_000,
            "retention_max_age_days": None,
        }
    if not isinstance(raw, dict):
        raise ConfigError("manager.command_journal", "must be a dict")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("manager.command_journal.enabled", "must be a bool")

    path_value = raw.get("path")
    if path_value is None:
        path = base_dir / ".state" / str(instance_id) / "command_journal.sqlite3"
    else:
        if not isinstance(path_value, str) or not path_value.strip():
            raise ConfigError(
                "manager.command_journal.path",
                "must be a non-empty string",
            )
        path = Path(path_value.strip()).expanduser()
        if not path.is_absolute():
            path = base_dir / path

    def parse_int(name: str, default: int, min_value: int) -> int:
        value = raw.get(name, default)
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as e:
            raise ConfigError(
                f"manager.command_journal.{name}",
                f"must be an int: {e}",
            ) from e
        if parsed < min_value:
            raise ConfigError(
                f"manager.command_journal.{name}",
                f"must be >= {min_value}",
            )
        return parsed

    queue_max = parse_int("queue_max", 10_000, 100)
    batch_size = parse_int("batch_size", 200, 1)
    flush_interval_ms = parse_int("flush_interval_ms", 200, 10)

    retention_raw = raw.get("retention", {})
    if retention_raw is None:
        retention_raw = {}
    if not isinstance(retention_raw, dict):
        raise ConfigError("manager.command_journal.retention", "must be a dict")

    retention_max_rows_raw = retention_raw.get("max_rows", 1_000_000)
    retention_max_rows: int | None
    if retention_max_rows_raw is None:
        retention_max_rows = None
    else:
        try:
            retention_max_rows = int(retention_max_rows_raw)
        except (TypeError, ValueError, OverflowError) as e:
            raise ConfigError(
                "manager.command_journal.retention.max_rows",
                f"must be an int or null: {e}",
            ) from e
        if retention_max_rows < 1_000:
            raise ConfigError(
                "manager.command_journal.retention.max_rows",
                "must be >= 1000",
            )

    retention_max_age_days_raw = retention_raw.get("max_age_days")
    retention_max_age_days: float | None
    if retention_max_age_days_raw is None:
        retention_max_age_days = None
    else:
        try:
            retention_max_age_days = float(retention_max_age_days_raw)
        except (TypeError, ValueError, OverflowError) as e:
            raise ConfigError(
                "manager.command_journal.retention.max_age_days",
                f"must be a float or null: {e}",
            ) from e
        if retention_max_age_days <= 0:
            raise ConfigError(
                "manager.command_journal.retention.max_age_days",
                "must be > 0",
            )

    return {
        "enabled": enabled,
        "path": path.resolve(),
        "queue_max": queue_max,
        "batch_size": batch_size,
        "flush_interval_ms": flush_interval_ms,
        "retention_max_rows": retention_max_rows,
        "retention_max_age_days": retention_max_age_days,
    }


def _parse_manager_logging(
    *,
    manager_raw: Json,
    base_dir: Path,
) -> Json:
    raw = manager_raw.get("logging")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("manager.logging", "must be a dict")

    stderr_value = raw.get("stderr")
    if stderr_value is not None and not isinstance(stderr_value, bool):
        raise ConfigError("manager.logging.stderr", "must be a bool")

    file_value = raw.get("file")
    file_path: Path | None = None
    if file_value is not None:
        if not isinstance(file_value, str) or not file_value.strip():
            raise ConfigError("manager.logging.file", "must be a non-empty string")
        file_path = Path(file_value.strip()).expanduser()
        if not file_path.is_absolute():
            file_path = base_dir / file_path

    min_level_value = raw.get("min_level")
    min_level: str | None = None
    if min_level_value is not None:
        text = str(min_level_value).strip().lower()
        if not text:
            raise ConfigError("manager.logging.min_level", "must be a non-empty string")
        if not is_valid_log_severity(text):
            raise ConfigError(
                "manager.logging.min_level",
                f"must be one of: {', '.join(LOG_SEVERITY_NAMES)}",
            )
        min_level = normalize_log_severity(text, default="error")

    return {
        "stderr": stderr_value,
        "file": file_path.resolve() if file_path is not None else None,
        "min_level": min_level,
    }


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
    sock.setsockopt(zmq.RCVTIMEO, 300)
    sock.connect(manager_rpc)
    try:
        sock.send(json_dumps({"type": "manager.control.shutdown"}))
        if sock.poll(300, zmq.POLLIN):
            sock.recv()
    except (zmq.ZMQError, TypeError, ValueError):
        pass
    sock.close(0)


def _wait_for_exit(proc: subprocess.Popen[str], timeout_s: float) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=timeout_s)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        pass


def _emit_lifecycle_startup_summary(
    *,
    mode: str,
    cleanup_orphans: bool,
    instance_lock: bool,
    preflight_ran: bool,
) -> None:
    cleanup_mode = "on" if cleanup_orphans else "off"
    lock_mode = "on" if instance_lock else "off"
    preflight_mode = "run" if preflight_ran else "skip"
    sys.stderr.write(
        "[run_stack] lifecycle: "
        f"mode={mode} cleanup={cleanup_mode} lock={lock_mode} preflight={preflight_mode}\n"
    )


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
        sock.send(json_dumps({"type": "manager.info.identity", "request_id": request_id}))
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
    except (zmq.ZMQError, TypeError, ValueError):
        return False
    finally:
        sock.close(0)


def _preflight_instance_cleanup(*, instance_id: str, manager_rpc: str) -> None:
    _assert_instance_not_running(instance_id=instance_id, manager_rpc=manager_rpc)
    summary = cleanup_orphan_children(
        instance_id=str(instance_id),
        exclude_pids={os.getpid()},
        current_parent_pid=os.getpid(),
        timeout_s=2.0,
        stale_only=True,
        dry_run=False,
    )
    matched = int(summary.get("matched", 0) or 0)
    if matched <= 0:
        return
    terminated = summary.get("terminated", [])
    failed = summary.get("failed", [])
    sys.stderr.write(
        "[run_stack] orphan cleanup: "
        f"matched={matched}, terminated={len(terminated)}, failed={len(failed)}\n"
    )
    if failed:
        sys.stderr.write(
            f"[run_stack] orphan cleanup failed pids: {', '.join(str(pid) for pid in failed)}\n"
        )


def _assert_instance_not_running(*, instance_id: str, manager_rpc: str) -> None:
    if _probe_manager_ready(
        manager_rpc=str(manager_rpc),
        timeout_ms=250,
        expected_instance_id=str(instance_id),
    ):
        raise SystemExit(
            "[run_stack] startup error: "
            f"instance {instance_id!r} is already running at {manager_rpc!r}"
        )


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
    instance_lock: bool = False,
) -> None:
    manager_rpc = manager_network.local_rpc_connect
    manager_pub = manager_network.local_pub_connect
    rpc_timeout_ms = int(tui_raw.get("rpc_timeout_ms", 1500))
    snapshot_period_s = float(tui_raw.get("snapshot_period_s", 2.0))
    startup_delay_s = float(tui_raw.get("startup_delay_s", 1.0))
    startup_timeout_s = float(tui_raw.get("startup_timeout_s", 15.0))
    event_log_max_lines = int(tui_raw.get("event_log_max_lines", 10_000))
    hidden_topics_raw = tui_raw.get("event_log_default_hidden_topics")
    event_log_default_hidden_topics = (
        list(hidden_topics_raw) if isinstance(hidden_topics_raw, list) else None
    )
    event_log_manager_min_severity = str(
        tui_raw.get("event_log_manager_min_severity", "warning")
    )
    pub_queue_maxsize = int(tui_raw.get("pub_queue_maxsize", 10_000))
    pub_queue_overflow_policy = str(
        tui_raw.get("pub_queue_overflow_policy", "drop_newest")
    )
    probe_timeout_ms = min(500, max(100, rpc_timeout_ms))

    child_cmd = [
        sys.executable,
        "-m",
        "experiment_control.cli.run_stack",
        str(stack_path),
        "--no-tui",
    ]
    if instance_lock:
        child_cmd.append("--instance-lock")
    child_env = os.environ.copy()
    child_env["MANAGER_LOG_STDERR"] = "0"
    manager_proc = subprocess.Popen(child_cmd, env=child_env)
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
            event_log_max_lines=event_log_max_lines,
            event_log_default_hidden_topics=event_log_default_hidden_topics,
            event_log_manager_min_severity=event_log_manager_min_severity,
            pub_queue_maxsize=pub_queue_maxsize,
            pub_queue_overflow_policy=pub_queue_overflow_policy,
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
        run_tui_mode = bool(tui_raw.get("enabled")) and not bool(ns.no_tui)
        preflight_ran = False
        if run_tui_mode:
            if bool(ns.cleanup_orphans):
                _preflight_instance_cleanup(
                    instance_id=instance_id,
                    manager_rpc=manager_network.local_rpc_connect,
                )
                preflight_ran = True
            _emit_lifecycle_startup_summary(
                mode="tui",
                cleanup_orphans=bool(ns.cleanup_orphans),
                instance_lock=bool(ns.instance_lock),
                preflight_ran=preflight_ran,
            )
            _run_with_tui(
                instance_id=instance_id,
                stack_path=stack_path,
                manager_network=manager_network,
                tui_raw=tui_raw,
                instance_lock=bool(ns.instance_lock),
            )
            return

        if bool(ns.cleanup_orphans):
            _preflight_instance_cleanup(
                instance_id=instance_id,
                manager_rpc=manager_network.local_rpc_connect,
            )
            preflight_ran = True
        _emit_lifecycle_startup_summary(
            mode="headless",
            cleanup_orphans=bool(ns.cleanup_orphans),
            instance_lock=bool(ns.instance_lock),
            preflight_ran=preflight_ran,
        )
        instance_lock: InstanceLock | _NoopInstanceLock
        if bool(ns.instance_lock):
            instance_lock = InstanceLock(
                instance_id=instance_id,
                manager_rpc=manager_network.local_rpc_connect,
            )
        else:
            instance_lock = _NoopInstanceLock()
        try:
            instance_lock.acquire()
        except InstanceLockActiveError as e:
            raise SystemExit(f"[run_stack] startup error: {e}") from None

        try:

            base_dir = stack_path.parent
            command_journal = _parse_command_journal(
                manager_raw=manager_raw,
                base_dir=base_dir,
                instance_id=instance_id,
            )
            manager_logging = _parse_manager_logging(
                manager_raw=manager_raw,
                base_dir=base_dir,
            )
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
                router_manager_worker_queue_max=int(
                    manager_raw.get("router_manager_worker_queue_max", 8192)
                ),
                router_process_worker_queue_max=int(
                    manager_raw.get("router_process_worker_queue_max", 8192)
                ),
                router_device_worker_queue_max=int(
                    manager_raw.get("router_device_worker_queue_max", 16384)
                ),
                router_mirrored_worker_queue_max=int(
                    manager_raw.get("router_mirrored_worker_queue_max", 8192)
                ),
                router_reply_queue_max=int(
                    manager_raw.get("router_reply_queue_max", 32768)
                ),
                router_inflight_max=int(
                    manager_raw.get("router_inflight_max", 32768)
                ),
                auto_connect_on_register=bool(
                    manager_raw.get("auto_connect_on_register", True)
                ),
                command_journal_enabled=bool(command_journal["enabled"]),
                command_journal_path=command_journal["path"],
                command_journal_queue_max=int(command_journal["queue_max"]),
                command_journal_batch_size=int(command_journal["batch_size"]),
                command_journal_flush_interval_ms=int(
                    command_journal["flush_interval_ms"]
                ),
                command_journal_retention_max_rows=command_journal["retention_max_rows"],
                command_journal_retention_max_age_days=command_journal[
                    "retention_max_age_days"
                ],
                telemetry_cache_max_devices=int(
                    manager_raw.get("telemetry_cache_max_devices", 4096)
                ),
                telemetry_cache_max_signals_per_device=int(
                    manager_raw.get("telemetry_cache_max_signals_per_device", 4096)
                ),
                chunk_cache_max_devices=int(
                    manager_raw.get("chunk_cache_max_devices", 4096)
                ),
                chunk_cache_max_streams_per_device=int(
                    manager_raw.get("chunk_cache_max_streams_per_device", 2048)
                ),
                manager_log_stderr=manager_logging["stderr"],
                manager_log_file=manager_logging["file"],
                manager_log_min_level=manager_logging["min_level"],
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
        finally:
            instance_lock.release()
    except ConfigError as e:
        raise SystemExit(f"[run_stack] config error: {e}") from None


if __name__ == "__main__":
    main()

