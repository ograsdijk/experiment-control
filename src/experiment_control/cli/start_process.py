from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any


_RESERVED_INIT_KEYS = {
    "process_id",
    "manager_rpc",
    "manager_pub",
    "heartbeat_endpoint",
    "process_data_endpoint",
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control managed process runner")
    p.add_argument("--process-class-path", required=True)
    p.add_argument("--process-class-name", required=True)
    p.add_argument("--process-init-json", required=True)
    p.add_argument("--manager-rpc", default=None)
    p.add_argument("--manager-pub", default=None)
    p.add_argument("--process-id", default=None)
    p.add_argument("--heartbeat-endpoint", default=None)
    p.add_argument("--process-data-endpoint", default=None)
    p.add_argument("--heartbeat-period-s", type=float, default=None)
    return p.parse_args(argv)


def _module_name_from_path(path: Path) -> tuple[str | None, Path | None]:
    parts: list[str] = []
    cur = path.parent
    while (cur / "__init__.py").exists():
        parts.append(cur.name)
        cur = cur.parent
    if not parts:
        return None, None
    pkg = list(reversed(parts))
    module_name = ".".join(pkg + [path.stem])
    return module_name, cur


def _import_class(file_path: str | Path, class_name: str) -> type[Any]:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Process file does not exist: {str(path)!r}")
    if path.suffix.lower() != ".py":
        raise ValueError(f"Process file must be a .py file: {str(path)!r}")
    if not class_name or not isinstance(class_name, str):
        raise ValueError("class_name must be a non-empty string")

    module_name, root = _module_name_from_path(path)
    if module_name and root is not None:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        module = importlib.import_module(module_name)
    else:
        module_name = f"_ec_process_{path.stem}_{abs(hash(str(path)))}"
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create import spec for {str(path)!r}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            sys.modules.pop(module_name, None)
            raise

    try:
        obj = getattr(module, class_name)
    except AttributeError as e:
        raise ImportError(
            f"Module loaded from {str(path)!r} has no attribute {class_name!r}"
        ) from e

    if not isinstance(obj, type):
        raise TypeError(
            f"{class_name!r} in {str(path)!r} did not resolve to a class (got {type(obj)!r})"
        )

    return obj


def _build_kwargs(
    cls: type[Any],
    init_kwargs: dict[str, Any],
    inject: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    sig = inspect.signature(cls.__init__)
    params = list(sig.parameters.values())
    accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in params)
    accepted = {p.name for p in params if p.name != "self"}

    kwargs = dict(init_kwargs)
    unused: dict[str, Any] = {}
    for key, value in inject.items():
        if value is None:
            continue
        if key in kwargs:
            continue
        if key in accepted or accepts_kwargs:
            kwargs[key] = value
        else:
            unused[key] = value
    return kwargs, unused


def _apply_injected_attrs(obj: Any, inject: dict[str, Any]) -> None:
    attr_map = {
        "process_id": "_process_id",
        "manager_rpc": "_manager_rpc",
        "manager_pub": "_manager_pub",
        "heartbeat_endpoint": "_heartbeat_endpoint",
        "process_data_endpoint": "_process_data_endpoint",
        "heartbeat_period_s": "_heartbeat_period_s",
    }
    for key, value in inject.items():
        if value is None:
            continue
        attr = attr_map.get(key)
        if attr is None or not hasattr(obj, attr):
            continue
        try:
            current = getattr(obj, attr)
        except Exception:
            current = None
        if attr == "_heartbeat_period_s" or current in {None, ""}:
            try:
                setattr(obj, attr, value)
            except Exception:
                pass


def main(argv: list[str] | None = None) -> None:
    try:
        ns = _parse_args(sys.argv[1:] if argv is None else argv)

        init_kwargs = json.loads(ns.process_init_json)
        if not isinstance(init_kwargs, dict):
            raise TypeError("--process-init-json must decode to a JSON object/dict")

        bad_keys = sorted(set(init_kwargs) & _RESERVED_INIT_KEYS)
        if bad_keys:
            raise TypeError(
                f"init_kwargs contains reserved keys: {', '.join(bad_keys)}"
            )

        cls = _import_class(ns.process_class_path, ns.process_class_name)
        inject = {
            "process_id": ns.process_id,
            "manager_rpc": ns.manager_rpc,
            "manager_pub": ns.manager_pub,
            "heartbeat_endpoint": ns.heartbeat_endpoint,
            "process_data_endpoint": ns.process_data_endpoint,
            "heartbeat_period_s": ns.heartbeat_period_s,
        }
        kwargs, unused = _build_kwargs(cls, init_kwargs, inject)

        obj = cls(**kwargs)
        _apply_injected_attrs(obj, unused | inject)

        if not hasattr(obj, "run"):
            raise TypeError(f"{cls.__name__!r} has no run() method")

        obj.run()
    except Exception as e:
        msg = str(e)
        sys.stderr.write(f"[start_process] error: {msg}\n")
        if os.environ.get("START_PROCESS_TRACEBACK") == "1":
            raise
        sys.exit(2)


if __name__ == "__main__":
    main()
