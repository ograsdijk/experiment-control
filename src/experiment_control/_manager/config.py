from __future__ import annotations

import copy
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

from .models import AutoReconnectSpec, ConnectCheckSpec, DeviceSpec, ProcessSpec, RestartPolicy
from .process_spec import process_spec_kwargs_from_yaml
from ..schemas.run_meta import run_meta_calls_from_json
from ..schemas.stream import stream_calls_from_json
from ..schemas.telemetry import telemetry_calls_from_json
from ..types import StreamCall, TelemetryCall
from ..utils.config_parsing import ConfigError, optional_dict, require_dict, require_str
from ..utils.yaml_helpers import load_yaml_file


def _module_name_from_path(path: Path) -> tuple[str | None, Path | None]:
    parts: list[str] = []
    cur = path.parent
    while (cur / "__init__.py").exists():
        parts.append(cur.name)
        cur = cur.parent
    if not parts:
        return None, None
    module_name = ".".join(list(reversed(parts)) + [path.stem])
    return module_name, cur


def _load_module(
    *,
    module_name: str | None,
    file_path: str | Path,
) -> Any:
    if module_name:
        return importlib.import_module(module_name)
    path = Path(file_path).expanduser().resolve()
    inferred_name, root = _module_name_from_path(path)
    if inferred_name and root is not None:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return importlib.import_module(inferred_name)
    module_name = f"_ec_driver_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for {str(path)!r}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _coerce_telemetry_calls(raw: object) -> list[TelemetryCall]:
    if raw is None:
        return []
    if isinstance(raw, list) and raw and all(isinstance(x, TelemetryCall) for x in raw):
        return list(raw)
    if isinstance(raw, list) and not raw:
        return []
    return telemetry_calls_from_json(raw)


def _coerce_stream_calls(raw: object) -> list[StreamCall] | None:
    if raw is None:
        return []
    if isinstance(raw, list) and raw and all(isinstance(x, StreamCall) for x in raw):
        return list(raw)
    if isinstance(raw, list) and not raw:
        return []
    return stream_calls_from_json(raw)


def _coerce_device_metadata(raw: object) -> dict[str, Any]:
    meta = optional_dict(raw, path=["device_metadata"])
    out: dict[str, Any] = {}
    for key, value in meta.items():
        name = str(key).strip()
        if not name:
            raise ConfigError("device_metadata", "keys must be non-empty strings")
        out[name] = value
    return out


def _coerce_stream_metadata(raw: object) -> dict[str, dict[str, Any]]:
    meta = optional_dict(raw, path=["stream_metadata"])
    out: dict[str, dict[str, Any]] = {}
    for stream_raw, attrs_raw in meta.items():
        stream = str(stream_raw).strip()
        if not stream:
            raise ConfigError("stream_metadata", "stream names must be non-empty")
        attrs = require_dict(attrs_raw, path=["stream_metadata", stream])
        normalized_attrs: dict[str, Any] = {}
        for attr_key, attr_value in attrs.items():
            name = str(attr_key).strip()
            if not name:
                raise ConfigError(
                    f"stream_metadata.{stream}",
                    "attribute keys must be non-empty strings",
                )
            normalized_attrs[name] = attr_value
        out[stream] = normalized_attrs
    return out


def _coerce_connect_check(raw: object) -> ConnectCheckSpec:
    if raw is None:
        return ConnectCheckSpec()

    obj = require_dict(raw, path=["connect_check"])
    enabled_raw = obj.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        raise ConfigError("connect_check.enabled", "must be a bool")
    enabled = bool(enabled_raw)

    identity_raw = obj.get("identity", {})
    if identity_raw is None:
        identity_raw = {}
    identity_obj = require_dict(identity_raw, path=["connect_check", "identity"])
    identity: dict[str, Any] = {}
    for key, value in identity_obj.items():
        field_name = str(key).strip()
        if not field_name:
            raise ConfigError(
                "connect_check.identity", "identity keys must be non-empty strings"
            )
        identity[field_name] = copy.deepcopy(value)

    on_fail_raw = str(obj.get("on_fail", "disconnect")).strip().lower()
    if not on_fail_raw:
        on_fail_raw = "disconnect"
    if on_fail_raw not in {"disconnect", "keep_connected"}:
        raise ConfigError(
            "connect_check.on_fail",
            "must be 'disconnect' or 'keep_connected'",
        )

    if enabled and not identity:
        raise ConfigError(
            "connect_check.identity",
            "must be non-empty when connect_check.enabled is true",
        )

    return ConnectCheckSpec(
        enabled=enabled,
        identity=identity,
        on_fail=on_fail_raw,
    )


def _coerce_auto_reconnect(raw: object) -> AutoReconnectSpec:
    if raw is None:
        return AutoReconnectSpec()
    obj = require_dict(raw, path=["auto_reconnect"])
    enabled_raw = obj.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        raise ConfigError("auto_reconnect.enabled", "must be a bool")
    enabled = bool(enabled_raw)

    stale_raw = obj.get("on_telemetry_stale_s")
    stale_s = None if stale_raw is None else float(stale_raw)
    if enabled and (stale_s is None or stale_s <= 0):
        raise ConfigError(
            "auto_reconnect.on_telemetry_stale_s",
            "must be > 0 when enabled",
        )

    cooldown_s = float(obj.get("cooldown_s", 30.0))
    reset_s = float(obj.get("reset_attempts_after_ok_s", 120.0))
    if cooldown_s < 0:
        raise ConfigError("auto_reconnect.cooldown_s", "must be >= 0")
    if reset_s < 0:
        raise ConfigError("auto_reconnect.reset_attempts_after_ok_s", "must be >= 0")

    max_raw = obj.get("max_attempts", 3)
    max_attempts = None if max_raw is None else int(max_raw)
    if max_attempts is not None and max_attempts < 1:
        raise ConfigError("auto_reconnect.max_attempts", "must be >= 1 or null")

    disconnect_timeout_ms = int(obj.get("disconnect_timeout_ms", 1000))
    connect_timeout_raw = obj.get("connect_timeout_ms")
    connect_timeout_ms = None if connect_timeout_raw is None else int(connect_timeout_raw)
    if disconnect_timeout_ms <= 0:
        raise ConfigError("auto_reconnect.disconnect_timeout_ms", "must be > 0")
    if connect_timeout_ms is not None and connect_timeout_ms <= 0:
        raise ConfigError("auto_reconnect.connect_timeout_ms", "must be > 0")

    return AutoReconnectSpec(
        enabled=enabled,
        on_telemetry_stale_s=stale_s,
        cooldown_s=cooldown_s,
        max_attempts=max_attempts,
        reset_attempts_after_ok_s=reset_s,
        disconnect_timeout_ms=disconnect_timeout_ms,
        connect_timeout_ms=connect_timeout_ms,
    )


def _load_driver_defaults(
    *,
    module_name: str | None,
    file_path: str | Path,
    class_name: str,
) -> dict[str, object]:
    try:
        module = _load_module(module_name=module_name, file_path=file_path)
    except Exception:
        return {}

    defaults: dict[str, object] = {}
    class_suffix = class_name.upper()
    telemetry_name = f"DEFAULT_TELEMETRY_CALLS_{class_suffix}"
    stream_name = f"DEFAULT_STREAM_CALLS_{class_suffix}"

    if hasattr(module, telemetry_name):
        defaults["telemetry_calls"] = getattr(module, telemetry_name)
    if hasattr(module, stream_name):
        defaults["stream_calls"] = getattr(module, stream_name)
    return defaults


def device_spec_from_yaml(path: str | Path) -> DeviceSpec:
    config_path = Path(path).expanduser().resolve()
    raw, yaml_text = load_yaml_file(config_path, return_text=True)
    try:
        raw_obj = require_dict(raw, path=[])
        device_id = require_str(raw_obj.get("device_id"), path=["device_id"])
        driver = require_dict(raw_obj.get("driver"), path=["driver"])
        driver_file = driver.get("file")
        driver_module = driver.get("module")
        if driver_file and driver_module:
            raise ConfigError("driver", "file and module are mutually exclusive")
        if not driver_file and not driver_module:
            raise ConfigError("driver", "file or module must be provided")
        module_name = None
        if driver_module:
            module_name = require_str(driver_module, path=["driver", "module"])
            spec = importlib.util.find_spec(module_name)
            if spec is None or spec.origin is None:
                raise ConfigError("driver.module", f"module not found: {module_name!r}")
            device_class_path = spec.origin
        else:
            device_class_path = require_str(driver_file, path=["driver", "file"])
        device_class_name = require_str(
            driver.get("class_name"), path=["driver", "class_name"]
        )
        init_kwargs = optional_dict(raw_obj.get("init_kwargs"), path=["init_kwargs"])
        defaults = _load_driver_defaults(
            module_name=module_name,
            file_path=device_class_path,
            class_name=device_class_name,
        )
        if "telemetry_calls" in raw_obj:
            telemetry_calls = _coerce_telemetry_calls(raw_obj.get("telemetry_calls"))
        else:
            telemetry_calls = _coerce_telemetry_calls(defaults.get("telemetry_calls"))
        if "stream_calls" in raw_obj:
            stream_calls = _coerce_stream_calls(raw_obj.get("stream_calls"))
        else:
            stream_calls = _coerce_stream_calls(defaults.get("stream_calls"))
        run_meta_calls = run_meta_calls_from_json(raw_obj.get("run_meta_calls"))
        device_metadata = _coerce_device_metadata(raw_obj.get("device_metadata"))
        stream_metadata = _coerce_stream_metadata(raw_obj.get("stream_metadata"))
        connect_check = _coerce_connect_check(raw_obj.get("connect_check"))
        auto_reconnect = _coerce_auto_reconnect(raw_obj.get("auto_reconnect"))
        telemetry_period_s = float(raw_obj.get("telemetry_period_s", 1.0))
        heartbeat_period_s = float(raw_obj.get("heartbeat_period_s", 1.0))
        command_poll_period_s = float(raw_obj.get("command_poll_period_s", 0.01))
    except ConfigError as e:
        raise TypeError(str(e)) from None

    return DeviceSpec(
        device_id=device_id,
        device_class_path=device_class_path,
        device_class_name=device_class_name,
        device_init_kwargs=init_kwargs,
        telemetry_calls=telemetry_calls,
        stream_calls=stream_calls,
        run_meta_calls=run_meta_calls,
        device_metadata=device_metadata,
        stream_metadata=stream_metadata,
        connect_check=connect_check,
        auto_reconnect=auto_reconnect,
        config_yaml_text=yaml_text,
        config_path=config_path,
        telemetry_period_s=telemetry_period_s,
        heartbeat_period_s=heartbeat_period_s,
        command_poll_period_s=command_poll_period_s,
    )


def process_spec_from_yaml(
    path: str | Path,
    *,
    manager_rpc: str,
    manager_pub: str,
) -> ProcessSpec:
    return ProcessSpec(
        **process_spec_kwargs_from_yaml(
            path,
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            restart_policy_enum=RestartPolicy,
        )
    )
