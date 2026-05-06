from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experiment_control.manager import device_spec_from_yaml, process_spec_from_yaml
from experiment_control.manager_process_spec import _validated_init_kwargs
from experiment_control.utils.config_parsing import require_dict, require_str
from experiment_control.utils.yaml_helpers import load_yaml_file

Json = dict[str, Any]


@dataclass(frozen=True)
class ValidationDiagnostic:
    path: str
    field_path: str
    severity: str
    message: str


def validate_instance_config(instance_root: Path) -> list[ValidationDiagnostic]:
    root = Path(instance_root).expanduser().resolve()
    diagnostics: list[ValidationDiagnostic] = []
    device_ids: set[str] = set()
    device_actions: dict[str, set[str]] = {}
    device_signals: dict[str, set[str]] = {}

    for path in sorted((root / "devices").glob("*.yaml")):
        raw = _load_yaml(path, diagnostics)
        if not isinstance(raw, dict):
            continue
        device_id = str(raw.get("device_id", path.stem)).strip()
        if device_id:
            device_ids.add(device_id)
        diagnostics.extend(_validate_device(path, root=root, raw=raw))
        actions, signals = _static_device_metadata(path, root=root, raw=raw)
        if device_id:
            device_actions[device_id] = actions
            device_signals[device_id] = signals

    for path in sorted((root / "processes").glob("*.yaml")):
        raw = _load_yaml(path, diagnostics)
        if not isinstance(raw, dict):
            continue
        diagnostics.extend(_validate_process(path, root=root, raw=raw))
        diagnostics.extend(
            _validate_process_references(
                path,
                root=root,
                raw=raw,
                device_ids=device_ids,
                device_actions=device_actions,
                device_signals=device_signals,
            )
        )

    return diagnostics


def _diag(path: Path, field_path: str, message: str, *, severity: str = "error") -> ValidationDiagnostic:
    return ValidationDiagnostic(
        path=str(path),
        field_path=field_path,
        severity=severity,
        message=message,
    )


def _load_yaml(path: Path, diagnostics: list[ValidationDiagnostic]) -> Any:
    try:
        return load_yaml_file(path)
    except Exception as exc:
        diagnostics.append(_diag(path, "<root>", f"failed to parse YAML: {exc}"))
        return None


def _validate_device(path: Path, *, root: Path, raw: Json) -> list[ValidationDiagnostic]:
    diagnostics: list[ValidationDiagnostic] = []
    try:
        device_spec_from_yaml(path)
    except Exception as exc:
        diagnostics.append(_diag(path, "<root>", str(exc)))

    try:
        driver = require_dict(raw.get("driver"), path=["driver"])
        class_name = require_str(driver.get("class_name"), path=["driver", "class_name"])
    except Exception:
        return diagnostics

    module, class_obj = _load_config_class(
        path,
        root=root,
        section=driver,
        section_name="driver",
        class_name=class_name,
        diagnostics=diagnostics,
    )
    if module is None or class_obj is None:
        return diagnostics
    diagnostics.extend(
        _validate_init_kwargs(
            path,
            raw=raw,
            class_obj=class_obj,
            field_path="init_kwargs",
            injected_keys=set(),
        )
    )
    return diagnostics


def _validate_process(path: Path, *, root: Path, raw: Json) -> list[ValidationDiagnostic]:
    diagnostics: list[ValidationDiagnostic] = []
    try:
        process_spec_from_yaml(
            path,
            manager_rpc="tcp://127.0.0.1:1",
            manager_pub="tcp://127.0.0.1:2",
        )
    except Exception as exc:
        diagnostics.append(_diag(path, "<root>", str(exc)))

    process = raw.get("process")
    if process is None:
        return diagnostics
    try:
        process_obj = require_dict(process, path=["process"])
        class_name = require_str(process_obj.get("class_name"), path=["process", "class_name"])
        _validated_init_kwargs(raw)
    except Exception as exc:
        diagnostics.append(_diag(path, "process", str(exc)))
        return diagnostics

    module, class_obj = _load_config_class(
        path,
        root=root,
        section=process_obj,
        section_name="process",
        class_name=class_name,
        diagnostics=diagnostics,
    )
    if module is None or class_obj is None:
        return diagnostics
    diagnostics.extend(
        _validate_init_kwargs(
            path,
            raw=raw,
            class_obj=class_obj,
            field_path="init_kwargs",
            injected_keys={
                "process_id",
                "manager_rpc",
                "manager_pub",
                "heartbeat_endpoint",
                "process_data_endpoint",
                "heartbeat_period_s",
                "ctx",
            },
        )
    )
    return diagnostics


def _load_config_class(
    config_path: Path,
    *,
    root: Path,
    section: Json,
    section_name: str,
    class_name: str,
    diagnostics: list[ValidationDiagnostic],
) -> tuple[Any | None, type[Any] | None]:
    module_name = section.get("module")
    file_name = section.get("file")
    if module_name and file_name:
        return None, None
    try:
        if module_name:
            module = importlib.import_module(require_str(module_name, path=[section_name, "module"]))
        elif file_name:
            file_path = _resolve_file(root, require_str(file_name, path=[section_name, "file"]))
            if not file_path.exists():
                diagnostics.append(
                    _diag(config_path, f"{section_name}.file", f"file does not exist: {file_name}")
                )
                return None, None
            module = _import_module_from_path(file_path)
        else:
            return None, None
    except Exception as exc:
        diagnostics.append(
            _diag(config_path, section_name, f"failed to import configured module/file: {exc}")
        )
        return None, None

    class_obj = getattr(module, class_name, None)
    if not isinstance(class_obj, type):
        diagnostics.append(_diag(config_path, f"{section_name}.class_name", f"class not found: {class_name!r}"))
        return module, None
    return module, class_obj


def _resolve_file(root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (root / path).resolve()


def _import_module_from_path(path: Path) -> Any:
    module_name = f"_ec_validation_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"could not create import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _validate_init_kwargs(
    path: Path,
    *,
    raw: Json,
    class_obj: type[Any],
    field_path: str,
    injected_keys: set[str],
) -> list[ValidationDiagnostic]:
    init_kwargs = raw.get("init_kwargs") or {}
    if not isinstance(init_kwargs, dict):
        return []
    try:
        sig = inspect.signature(class_obj.__init__)
    except Exception:
        return []
    params = list(sig.parameters.values())
    if any(p.kind == p.VAR_KEYWORD for p in params):
        return []
    accepted = {
        p.name
        for p in params
        if p.name != "self"
        and p.kind
        in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    }
    accepted.update(injected_keys)
    unknown = sorted(str(key) for key in init_kwargs if str(key) not in accepted)
    if not unknown:
        return []
    return [_diag(path, field_path, f"unknown constructor argument(s): {', '.join(unknown)}")]


def _static_device_metadata(path: Path, *, root: Path, raw: Json) -> tuple[set[str], set[str]]:
    actions: set[str] = set()
    signals: set[str] = set()
    telemetry = raw.get("telemetry_calls")
    if isinstance(telemetry, list):
        for item in telemetry:
            if isinstance(item, dict) and isinstance(item.get("signal"), str):
                signals.add(item["signal"])
    try:
        driver = require_dict(raw.get("driver"), path=["driver"])
        class_name = require_str(driver.get("class_name"), path=["driver", "class_name"])
    except Exception:
        return actions, signals
    diagnostics: list[ValidationDiagnostic] = []
    _module, class_obj = _load_config_class(
        path,
        root=root,
        section=driver,
        section_name="driver",
        class_name=class_name,
        diagnostics=diagnostics,
    )
    if class_obj is None:
        return actions, signals
    try:
        method_obj = getattr(class_obj, "get_capabilities")
        caps = method_obj(class_obj) if callable(method_obj) else None
        members = caps.get("members") if isinstance(caps, dict) else None
        if isinstance(members, list):
            for member in members:
                if isinstance(member, dict) and isinstance(member.get("name"), str):
                    actions.add(member["name"])
    except Exception:
        pass
    return actions, signals


def _validate_process_references(
    path: Path,
    *,
    root: Path,
    raw: Json,
    device_ids: set[str],
    device_actions: dict[str, set[str]],
    device_signals: dict[str, set[str]],
) -> list[ValidationDiagnostic]:
    diagnostics: list[ValidationDiagnostic] = []
    init_kwargs = raw.get("init_kwargs")
    if not isinstance(init_kwargs, dict):
        return diagnostics
    rules = init_kwargs.get("rules")
    if not isinstance(rules, list):
        return diagnostics
    process_file_dir = _process_file_dir(path, root=root, raw=raw)
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        prefix = f"init_kwargs.rules[{idx}]"
        device_id = rule.get("device_id")
        if isinstance(device_id, str):
            if device_ids and device_id not in device_ids:
                diagnostics.append(_diag(path, f"{prefix}.device_id", f"unknown device_id: {device_id!r}"))
            actions = device_actions.get(device_id, set())
            action = rule.get("trigger_action")
            if actions and isinstance(action, str) and action not in actions:
                diagnostics.append(
                    _diag(path, f"{prefix}.trigger_action", f"unknown action for {device_id!r}: {action!r}")
                )
            signals = device_signals.get(device_id, set())
            signal = rule.get("current_freq_signal")
            if signals and isinstance(signal, str) and signal not in signals:
                diagnostics.append(
                    _diag(path, f"{prefix}.current_freq_signal", f"unknown signal for {device_id!r}: {signal!r}")
                )
        csv_path = rule.get("csv_path")
        if isinstance(csv_path, str) and csv_path.strip():
            base_dir = process_file_dir or path.parent
            resolved = Path(csv_path)
            if not resolved.is_absolute():
                resolved = (base_dir / resolved).resolve()
            if not resolved.exists():
                diagnostics.append(_diag(path, f"{prefix}.csv_path", f"file does not exist: {csv_path}"))
        effects = rule.get("effects")
        if isinstance(effects, list):
            for effect_idx, effect in enumerate(effects):
                if not isinstance(effect, dict):
                    continue
                action = effect.get("action")
                actions = device_actions.get(device_id, set()) if isinstance(device_id, str) else set()
                if actions and isinstance(action, str) and action not in actions:
                    diagnostics.append(
                        _diag(
                            path,
                            f"{prefix}.effects[{effect_idx}].action",
                            f"unknown action for {device_id!r}: {action!r}",
                        )
                    )
    return diagnostics


def _process_file_dir(path: Path, *, root: Path, raw: Json) -> Path | None:
    process = raw.get("process")
    if not isinstance(process, dict):
        return None
    file_name = process.get("file")
    if not isinstance(file_name, str) or not file_name.strip():
        return None
    return _resolve_file(root, file_name).parent
