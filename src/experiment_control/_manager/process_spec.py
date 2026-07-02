from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from ..utils.config_parsing import (
    ConfigError,
    normalize_list,
    optional_dict,
    optional_str,
    require_dict,
    require_str,
)
from ..utils.yaml_helpers import load_yaml_file

Json = dict[str, Any]


def _require_process_or_argv(raw_obj: Json) -> tuple[Any, Any]:
    process_raw = raw_obj.get("process")
    argv_raw = raw_obj.get("argv")
    if process_raw is None and argv_raw is None:
        raise ConfigError("<root>", "process or argv must be provided")
    if process_raw is not None and argv_raw is not None:
        raise ConfigError("<root>", "process and argv are mutually exclusive")
    return process_raw, argv_raw


def _parse_heartbeat_period(raw_obj: Json) -> float | None:
    heartbeat_period_s_raw = raw_obj.get("heartbeat_period_s")
    if heartbeat_period_s_raw is None:
        return None
    return float(heartbeat_period_s_raw)


def _validated_init_kwargs(raw_obj: Json) -> Json:
    init_kwargs = optional_dict(raw_obj.get("init_kwargs"), path=["init_kwargs"])
    forbidden = {
        "process_id",
        "manager_rpc",
        "manager_pub",
        "heartbeat_endpoint",
        "process_data_endpoint",
    }
    bad_keys = sorted(set(init_kwargs) & forbidden)
    if bad_keys:
        raise ConfigError(
            "init_kwargs",
            f"contains reserved keys: {', '.join(bad_keys)}",
        )
    return init_kwargs


def _resolve_process_file(process_obj: Json) -> str:
    process_file = process_obj.get("file")
    process_module = process_obj.get("module")
    if process_file and process_module:
        raise ConfigError("process", "file and module are mutually exclusive")
    if not process_file and not process_module:
        raise ConfigError("process", "file or module must be provided")
    if process_module:
        module_name = require_str(process_module, path=["process", "module"])
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            raise ConfigError("process.module", f"module not found: {module_name!r}")
        process_file = spec.origin
    return require_str(process_file, path=["process", "file"])


def _build_process_class_argv(
    *,
    process_raw: Any,
    init_kwargs: Json,
    manager_rpc: str,
    manager_pub: str,
    heartbeat_period_s: float | None,
) -> list[str]:
    process_obj = require_dict(process_raw, path=["process"])
    process_file = _resolve_process_file(process_obj)
    class_name = require_str(process_obj.get("class_name"), path=["process", "class_name"])
    argv = [
        sys.executable,
        "-m",
        "experiment_control.cli.start_process",
        "--process-class-path",
        process_file,
        "--process-class-name",
        class_name,
        "--process-init-json",
        json.dumps(init_kwargs),
        "--manager-rpc",
        manager_rpc,
        "--manager-pub",
        manager_pub,
    ]
    if heartbeat_period_s is not None:
        argv += ["--heartbeat-period-s", str(heartbeat_period_s)]
    return argv


def _build_explicit_argv(argv_raw: Any) -> list[str]:
    argv = normalize_list(argv_raw, path=["argv"])
    if not all(isinstance(a, str) for a in argv):
        raise ConfigError("argv", "must be a list[str]")
    return argv


def _coerce_restart_policy(value: Any, *, restart_policy_enum: Any) -> Any:
    restart_policy = value
    if isinstance(restart_policy, str):
        restart_policy = restart_policy_enum(restart_policy)
    if not isinstance(restart_policy, restart_policy_enum):
        raise ConfigError("restart_policy", "must be a RestartPolicy or string")
    return restart_policy


def _resolve_config_relative_path(value: Any, *, config_dir: Path) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    path = Path(text).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    return str((config_dir / path).resolve())


def process_spec_kwargs_from_yaml(
    path: str | Path,
    *,
    manager_rpc: str,
    manager_pub: str,
    restart_policy_enum: Any,
) -> Json:
    config_path = Path(path).expanduser().resolve()
    config_dir = config_path.parent.parent if config_path.parent.name == "processes" else config_path.parent
    raw, _ = load_yaml_file(config_path, return_text=True)
    try:
        raw_obj = require_dict(raw, path=[])
        process_id = require_str(raw_obj.get("process_id"), path=["process_id"])
        process_raw, argv_raw = _require_process_or_argv(raw_obj)
        heartbeat_period_s = _parse_heartbeat_period(raw_obj)
        init_kwargs = _validated_init_kwargs(raw_obj)
        init_kwargs = {
            key: _resolve_config_relative_path(value, config_dir=config_dir)
            if key in {"sequence_library_path", "autoload_path"}
            else value
            for key, value in init_kwargs.items()
        }
        if process_raw is not None:
            argv = _build_process_class_argv(
                process_raw=process_raw,
                init_kwargs=init_kwargs,
                manager_rpc=manager_rpc,
                manager_pub=manager_pub,
                heartbeat_period_s=heartbeat_period_s,
            )
        else:
            argv = _build_explicit_argv(argv_raw)
        restart_policy = _coerce_restart_policy(
            raw_obj.get("restart_policy", restart_policy_enum.NEVER),
            restart_policy_enum=restart_policy_enum,
        )
        cwd = _resolve_config_relative_path(
            optional_str(raw_obj.get("cwd"), path=["cwd"]),
            config_dir=config_dir,
        )
        env = optional_dict(raw_obj.get("env"), path=["env"])
    except ConfigError as e:
        raise TypeError(str(e)) from None
    return {
        "process_id": process_id,
        "argv": argv,
        "cwd": cwd,
        "env": env or None,
        "heartbeat_period_s": (
            float(raw_obj.get("heartbeat_period_s", 1.0))
            if heartbeat_period_s is None
            else heartbeat_period_s
        ),
        "heartbeat_timeout_s": float(raw_obj.get("heartbeat_timeout_s", 3.0)),
        "shutdown_timeout_s": float(raw_obj.get("shutdown_timeout_s", 3.0)),
        "restart_policy": restart_policy,
        "restart_backoff_s": float(raw_obj.get("restart_backoff_s", 0.5)),
        "max_restarts": raw_obj.get("max_restarts"),
        "heartbeat_endpoint": raw_obj.get("heartbeat_endpoint"),
        "process_data_endpoint": raw_obj.get("process_data_endpoint"),
    }
