from __future__ import annotations

import enum
import inspect
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np

from ..types import MemberParamSpec, MemberSpec


def _type_to_str(tp: object) -> str | None:
    if tp is inspect._empty:
        return None
    if isinstance(tp, type):
        return tp.__name__
    try:
        return str(tp)
    except Exception:
        return None


def _parse_simple_annotation(annotation: str | None) -> str | None:
    if not annotation:
        return None
    base_types = {"bool", "int", "float", "str"}
    norm = annotation.replace("typing.", "").replace(" ", "").lower()
    if norm in base_types:
        return norm
    if norm.startswith("optional[") and norm.endswith("]"):
        inner = norm[len("optional[") : -1]
        if inner in base_types:
            return inner
    if norm.startswith("union[") and norm.endswith("]"):
        inner = norm[len("union[") : -1]
        parts = {p for p in inner.split(",") if p}
        for base in base_types:
            if parts == {base, "none"}:
                return base
    if "|" in norm:
        parts = {p for p in norm.split("|") if p}
        for base in base_types:
            if parts == {base, "none"}:
                return base
    return None


def _has_simple_annotation(annotation: str | None) -> bool:
    return _parse_simple_annotation(annotation) is not None


def _runtime_value_annotation(value: object) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return None


def _property_getter_return_annotation(prop: property) -> str | None:
    if prop.fget is None:
        return None
    try:
        hints = typing.get_type_hints(prop.fget, include_extras=True)
    except Exception:
        hints = {}
    try:
        sig = inspect.signature(prop.fget)
    except Exception:
        sig = None
    ann = hints.get("return") if isinstance(hints, dict) else None
    if ann is None and sig is not None:
        ann = sig.return_annotation
    return _type_to_str(ann)


def _property_setter_value_annotation(prop: property) -> str | None:
    if prop.fset is None:
        return None
    ann = None
    try:
        hints = typing.get_type_hints(prop.fset, include_extras=True)
    except Exception:
        hints = {}
    try:
        sig = inspect.signature(prop.fset)
        params_list = list(sig.parameters.values())
        if len(params_list) >= 2:
            param_name = params_list[1].name
            ann = hints.get(param_name, params_list[1].annotation)
    except Exception:
        ann = None
    return _type_to_str(ann)


def _should_infer_property_runtime_annotation(
    *,
    settable: bool,
    getter_annotation: str | None,
    setter_annotation: str | None,
) -> bool:
    return (
        settable
        and not _has_simple_annotation(getter_annotation)
        and not _has_simple_annotation(setter_annotation)
    )


def _infer_property_runtime_annotation(device: object, prop: property) -> str | None:
    if prop.fget is None:
        return None
    try:
        return _runtime_value_annotation(prop.fget(device))
    except Exception:
        return None


def _coerce_simple_value(value: Any, kind: str) -> Any:
    if kind == "int":
        return int(cast(Any, value))
    if kind == "float":
        return float(cast(Any, value))
    if kind == "str":
        return str(value)
    if kind == "bool":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1"}:
                return True
            if lowered in {"false", "0"}:
                return False
            raise ValueError("Invalid boolean value")
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            if value not in {0, 1}:
                raise ValueError("Invalid boolean value")
            return bool(value)
        raise ValueError("Invalid boolean value")
    return value


def _jsonable_default(
    value: object,
    *,
    _depth: int = 0,
    _max_depth: int = 3,
    _max_len: int = 50,
) -> object | None:
    if isinstance(value, enum.Enum):
        enum_val = value.value
        if enum_val is None or isinstance(enum_val, (bool, int, float, str)):
            return {
                "__enum__": value.__class__.__name__,
                "name": value.name,
                "value": enum_val,
            }
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if _depth >= _max_depth:
        return repr(value)
    if isinstance(value, (list, tuple)):
        out: list[object | None] = []
        for item in list(value)[:_max_len]:
            out.append(
                _jsonable_default(
                    item,
                    _depth=_depth + 1,
                    _max_depth=_max_depth,
                    _max_len=_max_len,
                )
            )
        return out
    if isinstance(value, dict):
        out_dict: dict[str, object | None] = {}
        for key, item in list(value.items())[:_max_len]:
            if not isinstance(key, str):
                continue
            out_dict[key] = _jsonable_default(
                item,
                _depth=_depth + 1,
                _max_depth=_max_depth,
                _max_len=_max_len,
            )
        return out_dict
    return repr(value)


def _jsonable_value(value: object) -> object:
    if isinstance(value, np.ndarray):
        size = int(value.size)
        if size > 10_000:
            return {
                "__error__": "array too large",
                "shape": list(value.shape),
                "dtype": str(value.dtype),
            }
        return value.tolist()
    return _jsonable_default(value)


def _member_to_json(m: MemberSpec) -> dict[str, object]:
    return {
        "name": m.name,
        "kind": m.kind,
        "readable": m.readable,
        "settable": m.settable,
        "value_annotation": m.value_annotation,
        "doc": m.doc,
        "params": [
            {
                "name": p.name,
                "kind": p.kind,
                "required": p.required,
                "default": p.default,
                "annotation": p.annotation,
            }
            for p in (m.params or [])
        ]
        if m.params is not None
        else None,
        "return_annotation": m.return_annotation,
        "source": m.source,
    }


def discover_device_members(device: object) -> list[MemberSpec]:
    members: list[MemberSpec] = []
    type_hints: dict[str, object] = {}
    try:
        type_hints = typing.get_type_hints(type(device), include_extras=True)
    except Exception:
        type_hints = {}

    for name in dir(device):
        if name.startswith("_") or name in {"connect", "disconnect"}:
            continue

        prop: property | None = None
        for cls in type(device).mro():
            if name in cls.__dict__ and isinstance(cls.__dict__[name], property):
                prop = cls.__dict__[name]
                break

        if prop is not None:
            readable = prop.fget is not None
            settable = prop.fset is not None
            doc_src = None
            if prop.fget is not None:
                doc_src = inspect.getdoc(prop.fget)
            if not doc_src:
                doc_src = inspect.getdoc(prop)
            doc = doc_src.splitlines()[0] if doc_src else None

            value_annotation = _property_getter_return_annotation(prop)
            setter_annotation = _property_setter_value_annotation(prop)
            if _should_infer_property_runtime_annotation(
                settable=settable,
                getter_annotation=value_annotation,
                setter_annotation=setter_annotation,
            ):
                # Some drivers create writable properties dynamically only after
                # connect. For example, pfeiffer_turbo setters are broad
                # Union[str, int, float] annotations even when the live parameter
                # is boolean. A single bounded read gives command coercion the
                # concrete scalar type, while still avoiding reads when static
                # annotations are already usable.
                runtime_annotation = _infer_property_runtime_annotation(device, prop)
                if runtime_annotation is not None:
                    value_annotation = runtime_annotation

            params: list[MemberParamSpec] | None = None
            if settable and prop.fset is not None:
                params = [
                    MemberParamSpec(
                        name="value",
                        kind=inspect.Parameter.POSITIONAL_OR_KEYWORD.name,
                        required=True,
                        default=None,
                        annotation=setter_annotation,
                    )
                ]

            members.append(
                MemberSpec(
                    name=name,
                    kind="property",
                    readable=readable,
                    settable=settable,
                    value_annotation=value_annotation,
                    doc=doc,
                    params=params,
                    return_annotation=None,
                    source="device",
                )
            )
            continue

        try:
            value = getattr(device, name)
        except Exception:
            members.append(
                MemberSpec(
                    name=name,
                    kind="attribute",
                    readable=False,
                    settable=False,
                    value_annotation=None,
                    doc=None,
                    params=None,
                    return_annotation=None,
                    source="device",
                )
            )
            continue

        if callable(value):
            try:
                sig = inspect.signature(value)
            except Exception:
                sig = None
            try:
                hints = typing.get_type_hints(value, include_extras=True)
            except Exception:
                hints = {}

            method_params: list[MemberParamSpec] = []
            if sig is not None:
                for param in sig.parameters.values():
                    if param.name == "self":
                        continue
                    required = param.default is inspect._empty
                    default = None if required else _jsonable_default(param.default)
                    ann = hints.get(param.name, param.annotation)
                    method_params.append(
                        MemberParamSpec(
                            name=param.name,
                            kind=param.kind.name,
                            required=required,
                            default=default,
                            annotation=_type_to_str(ann),
                        )
                    )
            params = method_params or None

            ret_ann = None
            if sig is not None:
                ret_ann = hints.get("return", sig.return_annotation)
            doc_src = inspect.getdoc(value)
            doc = doc_src.splitlines()[0] if doc_src else None

            members.append(
                MemberSpec(
                    name=name,
                    kind="method",
                    readable=True,
                    settable=False,
                    value_annotation=None,
                    doc=doc,
                    params=params,
                    return_annotation=_type_to_str(ret_ann),
                    source="device",
                )
            )
        else:
            value_ann = type_hints.get(name)
            if value_ann is None:
                value_ann = type(value)
            members.append(
                MemberSpec(
                    name=name,
                    kind="attribute",
                    readable=True,
                    settable=True,
                    value_annotation=_type_to_str(value_ann),
                    doc=None,
                    params=None,
                    return_annotation=None,
                    source="device",
                )
            )

    members.sort(key=lambda m: m.name)
    return members


def discover_stream_members(
    stream_rpc: dict[str, Callable[..., Any]]
) -> list[MemberSpec]:
    members: list[MemberSpec] = []
    for name, func in sorted(stream_rpc.items(), key=lambda item: item[0]):
        try:
            sig = inspect.signature(func)
        except Exception:
            sig = None
        try:
            hints = typing.get_type_hints(func, include_extras=True)
        except Exception:
            hints = {}

        stream_params: list[MemberParamSpec] = []
        if sig is not None:
            for param in sig.parameters.values():
                if param.name == "self":
                    continue
                required = param.default is inspect._empty
                default = None if required else _jsonable_default(param.default)
                ann = hints.get(param.name, param.annotation)
                stream_params.append(
                    MemberParamSpec(
                        name=param.name,
                        kind=param.kind.name,
                        required=required,
                        default=default,
                        annotation=_type_to_str(ann),
                    )
                )
        params = stream_params or None

        ret_ann = None
        if sig is not None:
            ret_ann = hints.get("return", sig.return_annotation)
        doc_src = inspect.getdoc(func)
        doc = doc_src.splitlines()[0] if doc_src else None

        members.append(
            MemberSpec(
                name=name,
                kind="method",
                readable=True,
                settable=False,
                value_annotation=None,
                doc=doc,
                params=params,
                return_annotation=_type_to_str(ret_ann),
                source="stream",
            )
        )

    return members


def discover_capabilities(
    device: object,
    *,
    stream_rpc: dict[str, Callable[..., Any]] | None = None,
) -> dict[str, object]:
    members = discover_device_members(device)
    if stream_rpc:
        members += discover_stream_members(stream_rpc)
    return {"version": 1, "members": [_member_to_json(m) for m in members]}


def discover_capabilities_for_class(
    device_or_class: object,
    *,
    init_kwargs: dict[str, Any] | None = None,
    connect: bool = False,
    disconnect: bool = False,
    stream_rpc: dict[str, Callable[..., Any]] | None = None,
) -> dict[str, object]:
    if isinstance(device_or_class, type):
        kwargs = init_kwargs or {}
        device = device_or_class(**kwargs)
    else:
        device = device_or_class
    if connect and hasattr(device, "connect"):
        device.connect()
    try:
        return discover_capabilities(device, stream_rpc=stream_rpc)
    finally:
        if connect and disconnect and hasattr(device, "disconnect"):
            try:
                device.disconnect()
            except Exception:
                pass


