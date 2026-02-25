import enum
import importlib
import importlib.util
import inspect
import json
import operator
import os
import sys
import time
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, cast

import numpy as np
import zmq

from .shm.shm_ring import ShmRingWriter, now_mono_ns, now_wall_ns
from .types import (
    DeviceState,
    DriverState,
    ExtractorKind,
    MemberParamSpec,
    MemberSpec,
    RunMetaCall,
    RunMetaOut,
    StreamCall,
    StreamMeta,
    StreamOut,
    TelemetryCall,
    TelemetryOut,
    TelemetryQuality,
    Timestamp,
)
from .utils.value_coercion import coerce_scalar


class Device(Protocol):
    """
    Minimal interface expected from a device object.

    Subclasses of DeviceRunner can define their own device interfaces.
    """

    def connect(self, *args: Any, **kwargs: Any) -> None: ...

    def disconnect(self) -> None: ...


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

            value_annotation = None
            if prop.fget is not None:
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
                value_annotation = _type_to_str(ann)

            params: list[MemberParamSpec] | None = None
            if settable and prop.fset is not None:
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
                params = [
                    MemberParamSpec(
                        name="value",
                        kind=inspect.Parameter.POSITIONAL_OR_KEYWORD.name,
                        required=True,
                        default=None,
                        annotation=_type_to_str(ann),
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

            params: list[MemberParamSpec] | None = []
            if sig is not None:
                for param in sig.parameters.values():
                    if param.name == "self":
                        continue
                    required = param.default is inspect._empty
                    default = None if required else _jsonable_default(param.default)
                    ann = hints.get(param.name, param.annotation)
                    params.append(
                        MemberParamSpec(
                            name=param.name,
                            kind=param.kind.name,
                            required=required,
                            default=default,
                            annotation=_type_to_str(ann),
                        )
                    )
            if not params:
                params = None

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

        params: list[MemberParamSpec] | None = []
        if sig is not None:
            for param in sig.parameters.values():
                if param.name == "self":
                    continue
                required = param.default is inspect._empty
                default = None if required else _jsonable_default(param.default)
                ann = hints.get(param.name, param.annotation)
                params.append(
                    MemberParamSpec(
                        name=param.name,
                        kind=param.kind.name,
                        required=required,
                        default=default,
                        annotation=_type_to_str(ann),
                    )
                )
        if not params:
            params = None

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


def extract_value(
    value: object, *, kind: ExtractorKind, ref: int | str | None
) -> object:
    if kind == "scalar":
        return value
    if ref is None:
        raise ValueError(f"Extractor kind {kind!r} requires ref")
    if kind == "index":
        return value[ref]  # type: ignore[index]
    if kind == "key":
        return value[ref]  # type: ignore[index]
    if kind == "attr":
        if not isinstance(ref, str):
            raise TypeError("attr extractor requires str ref")
        return getattr(value, ref)
    raise ValueError(f"Unknown extractor kind {kind!r}")


def _identity(value: Any) -> Any:
    return value


def _raise_extractor(msg: str) -> Callable[[Any], Any]:
    def _raise(_: Any) -> Any:
        raise ValueError(msg)

    return _raise


@dataclass(frozen=True, slots=True)
class _TelemetryOutPlan:
    signal: str
    units: str | None
    extractor: Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class _TelemetryCallPlan:
    func: Callable[..., Any] | None
    attr_name: str | None
    kwargs: dict[str, Any]
    outputs: list[_TelemetryOutPlan]


def import_class(file_path: str | Path, class_name: str) -> type[Device]:
    """
    Import a class from a Python source file.

    Args:
        file_path: Path to a .py file (absolute or relative).
        class_name: Name of the class defined in that file.

    Returns:
        The class object.

    Notes:
    - This loads the file as a module via importlib, without requiring it to be on sys.path.
    - A minimal structural check is performed for the Device Protocol:
      the class must have attributes 'connect' and 'disconnect'.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Driver file does not exist: {str(path)!r}")
    if path.suffix.lower() != ".py":
        raise ValueError(f"Driver file must be a .py file: {str(path)!r}")
    if not class_name or not isinstance(class_name, str):
        raise ValueError("class_name must be a non-empty string")

    # Create a unique module name to avoid collisions if multiple files share a name.
    module_name = f"_centrex_driver_{path.stem}_{abs(hash(str(path)))}"

    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for {str(path)!r}")

    module = importlib.util.module_from_spec(spec)

    # Register before exec so relative imports inside the loaded module can work.
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception:
        # Avoid leaving a partially-imported module around
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

    if not hasattr(obj, "connect"):
        raise TypeError(f"{class_name!r} is missing required attribute 'connect'")
    if not hasattr(obj, "disconnect"):
        raise TypeError(f"{class_name!r} is missing required attribute 'disconnect'")

    return obj  # type: ignore[return-value]


class DeviceRunner:
    def __init__(
        self,
        device_id: str,
        device_class_path: str,
        device_class_name: str,
        device_init_kwargs: dict[str, Any],
        registry_endpoint: str,
        *,
        telemetry_calls: list[TelemetryCall] | None = None,
        stream_calls: list[StreamCall] | None = None,
        run_meta_calls: list[RunMetaCall] | None = None,
        telemetry_period_s: float = 1.0,
        heartbeat_period_s: float = 1.0,
        command_poll_period_s: float = 0.01,
    ) -> None:
        if telemetry_period_s <= 0:
            raise ValueError("telemetry_period_s must be > 0")
        if heartbeat_period_s <= 0:
            raise ValueError("heartbeat_period_s must be > 0")
        if command_poll_period_s <= 0:
            raise ValueError("command_poll_period_s must be > 0")

        self.device_id = device_id
        self._device = import_class(device_class_path, device_class_name)(
            **device_init_kwargs
        )

        self.registry_endpoint = registry_endpoint

        self._telemetry_calls = list(telemetry_calls or [])
        self._stream_calls = list(stream_calls or [])
        self._run_meta_calls = list(run_meta_calls or [])
        self.telemetry_period_s = telemetry_period_s
        self.heartbeat_period_s = heartbeat_period_s
        self.command_poll_period_s = command_poll_period_s

        self._telemetry_plan: list[_TelemetryCallPlan] = []
        self._init_telemetry_plan()

        self._stop = False

        self._telemetry_seq = 0
        self._heartbeat_seq = 0

        # Last known good hardware transaction time, set by subclasses when appropriate
        self._last_ok_ts: Timestamp | None = None
        self._last_error: str | None = None

        # Subclass-managed hardware status flags
        self._device_reachable: bool = False
        self._device_state: DeviceState = DeviceState.UNKNOWN
        self._connect_called: bool = False
        self._capabilities_cache: dict[str, object] | None = None
        self._members_cache: dict[str, MemberSpec] | None = None

        self.ctx = zmq.Context()

        self.rpc = self.ctx.socket(zmq.REP)
        self.pub = self.ctx.socket(zmq.PUB)

        self._stream_writers: dict[str, ShmRingWriter] = {}
        self._stream_outputs: dict[str, StreamOut] = {}
        self._stream_rpc: dict[str, Callable[..., Any]] = {}
        self._stream_shm_names: dict[str, str] = {}
        self._stream_context: dict[str, dict[str, Any]] = {}
        self._init_stream_schema()
        self._init_stream_wrappers()

    # ----------------------------
    # Public lifecycle API
    # ----------------------------

    def capabilities(self) -> dict[str, Any]:
        """
        Return a static description of what this driver supports.

        Keep this simple, JSON-serializable, and stable over time.
        """
        if self._capabilities_cache is not None:
            return self._capabilities_cache
        if not self._device_reachable or self._device_state == DeviceState.DISCONNECTED:
            return {"version": 1, "members": []}
        try:
            self._refresh_capabilities_cache()
        except Exception:
            return {"version": 1, "members": []}
        return self._capabilities_cache or {"version": 1, "members": []}

    def run(self) -> None:
        """
        Main loop for a driver process using:
        - REP socket for RPC from the manager
        - PUB socket for telemetry/heartbeat to subscribers

        Device connection is NOT attempted automatically.
        The manager must call the "connect_device" command.
        """
        self._stop = False
        self.connect_ipc()
        self.register_with_manager()

        # Start disconnected until manager requests a connection
        self._device_reachable = False
        self._device_state = DeviceState.DISCONNECTED
        self._last_ok_ts = None
        self._last_error = None

        poller = zmq.Poller()
        poller.register(self.rpc, zmq.POLLIN)

        next_hb = time.monotonic()
        next_tel = time.monotonic()
        intended_tick = time.monotonic()

        try:
            while not self._stop:
                now = time.monotonic()
                loop_lag_s = max(0.0, now - intended_tick)
                intended_tick = now + self.command_poll_period_s

                # Wait for an RPC request, but wake up for periodic heartbeat/telemetry.
                timeout_s = min(
                    max(0.0, next_hb - now),
                    max(0.0, next_tel - now),
                    self.command_poll_period_s,
                )
                timeout_ms = int(timeout_s * 1000)

                events = dict(poller.poll(timeout_ms))

                # RPC
                if self.rpc in events:
                    req_raw = self.rpc.recv_json()
                    if not isinstance(req_raw, dict):
                        resp = {
                            "id": None,
                            "status": "ERROR",
                            "error": "Malformed request",
                        }
                    else:
                        resp = self._handle_rpc_request(req_raw)
                    self.rpc.send_json(resp)

                # Heartbeat (PUB)
                now = time.monotonic()
                if now >= next_hb:
                    self._publish_heartbeat(loop_lag_s=loop_lag_s)
                    next_hb = now + self.heartbeat_period_s

                # Telemetry (PUB)
                if now >= next_tel:
                    self._publish_telemetry()
                    next_tel = now + self.telemetry_period_s

        finally:
            # Best-effort cleanup
            try:
                if self._connect_called and self._device_state != DeviceState.DISCONNECTED:
                    self.disconnect_device()
            except Exception:
                pass
            try:
                self._cleanup_stream_publishers()
            except Exception:
                pass
            try:
                self.disconnect_ipc()
            except Exception:
                pass

    # ----------------------------
    # Subclass hooks
    # ----------------------------

    def connect_ipc(self) -> None:
        rpc_port = self.rpc.bind_to_random_port("tcp://127.0.0.1")
        pub_port = self.pub.bind_to_random_port("tcp://127.0.0.1")
        self.rpc_endpoint = f"tcp://127.0.0.1:{rpc_port}"
        self.pub_endpoint = f"tcp://127.0.0.1:{pub_port}"

    def disconnect_ipc(self) -> None:
        try:
            self.rpc.close(0)
        except Exception:
            pass
        try:
            self.pub.close(0)
        except Exception:
            pass
        try:
            self.ctx.term()
        except Exception:
            pass

    def register_with_manager(self) -> None:
        reg = self.ctx.socket(zmq.REQ)
        reg.connect(self.registry_endpoint)

        msg = {
            "type": "register",
            "device_id": self.device_id,
            "rpc_endpoint": self.rpc_endpoint,
            "pub_endpoint": self.pub_endpoint,
            "capabilities": self.capabilities(),
        }

        reg.send_json(msg)
        reg.recv_json()  # simple ACK
        reg.close()

    def connect_device(self) -> None:
        assert self._device is not None, "Device not set"
        self._device.connect()

    def disconnect_device(self) -> None:
        assert self._device is not None, "Device not set"
        self._device.disconnect()

    def stream_names(self) -> list[str]:
        names: list[str] = []
        for call in self._stream_calls:
            for out in call.outputs or []:
                names.append(out.stream)
        return names

    def get_stream_schema(self) -> list[StreamCall]:
        return list(self._stream_calls)

    def get_run_meta_schema(self) -> list[RunMetaCall]:
        return list(self._run_meta_calls)

    def supported_commands(self) -> list[str]:
        """
        Subclass hook: list of command strings this driver supports.
        """
        return []

    def telemetry_signal_names(self) -> list[str]:
        names: list[str] = []
        for call in self._telemetry_calls:
            for out in call.outputs or []:
                names.append(out.signal)
        return names

    def _discover_device_members(self) -> list[MemberSpec]:
        if self._device is None:
            return []
        return discover_device_members(self._device)

    def _discover_stream_members(self) -> list[MemberSpec]:
        members: list[MemberSpec] = []
        for call in self._stream_calls:
            name = f"stream__{call.method}"
            params: list[MemberParamSpec] = []
            func = getattr(self._device, call.method, None)
            wants_n_batch = False
            n_batch_required = False
            if callable(func):
                try:
                    sig = inspect.signature(func)
                    if "n_batch" in sig.parameters:
                        wants_n_batch = True
                        n_param = sig.parameters["n_batch"]
                        n_batch_required = n_param.default is inspect._empty
                except Exception:
                    wants_n_batch = False
            if "n_batch" in (call.kwargs or {}):
                wants_n_batch = True
            if wants_n_batch:
                params.append(
                    MemberParamSpec(
                        name="n_batch",
                        kind=inspect.Parameter.POSITIONAL_OR_KEYWORD.name,
                        required=n_batch_required,
                        default=None,
                        annotation="int",
                    )
                )
            for key, value in (call.kwargs or {}).items():
                if key == "n_batch":
                    continue
                params.append(
                    MemberParamSpec(
                        name=str(key),
                        kind=inspect.Parameter.POSITIONAL_OR_KEYWORD.name,
                        required=False,
                        default=_jsonable_default(value),
                        annotation=None,
                    )
                )

            doc = None
            if callable(func):
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
                    params=params or None,
                    return_annotation=None,
                    source="stream",
                )
            )

        return members

    def _refresh_capabilities_cache(self) -> None:
        try:
            members = self._discover_device_members() + self._discover_stream_members()
            self._members_cache = {m.name: m for m in members}
            self._capabilities_cache = {
                "version": 1,
                "members": [_member_to_json(m) for m in members],
            }
        except Exception as e:
            self._capabilities_cache = {"version": 1, "members": []}
            self._members_cache = {}
            self._last_error = f"capabilities discovery failed: {e!r}"

    def read_telemetry(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}

        for plan in self._telemetry_plan:
            if plan.func is None and plan.attr_name is None:
                for o in plan.outputs:
                    out[o.signal] = {
                        "value": None,
                        "units": o.units,
                        "quality": TelemetryQuality.MISSING,
                        "ts": None,
                    }
                continue

            try:
                if plan.func is not None:
                    ret = plan.func(**plan.kwargs)
                else:
                    try:
                        member = getattr(self._device, cast(str, plan.attr_name))
                    except AttributeError:
                        for o in plan.outputs:
                            out[o.signal] = {
                                "value": None,
                                "units": o.units,
                                "quality": TelemetryQuality.MISSING,
                                "ts": None,
                            }
                        continue
                    if callable(member):
                        ret = member(**plan.kwargs)
                    else:
                        if plan.kwargs:
                            raise ValueError(
                                f"Telemetry property {plan.attr_name!r} does not accept kwargs"
                            )
                        ret = member
                for o in plan.outputs:
                    try:
                        val = o.extractor(ret)
                        out[o.signal] = {
                            "value": val,
                            "units": o.units,
                            "quality": TelemetryQuality.OK,
                            "ts": None,
                        }
                    except Exception:
                        out[o.signal] = {
                            "value": None,
                            "units": o.units,
                            "quality": TelemetryQuality.BAD,
                            "ts": None,
                        }
            except Exception:
                for o in plan.outputs:
                    out[o.signal] = {
                        "value": None,
                        "units": o.units,
                        "quality": TelemetryQuality.BAD,
                        "ts": None,
                    }

        return out

    def _extract_telemetry_value(self, ret: Any, o: TelemetryOut) -> Any:
        return extract_value(ret, kind=o.kind, ref=o.ref)

    def collect_run_metadata(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for call in self._run_meta_calls:
            func = getattr(self._device, call.method, None)
            if func is None or not callable(func):
                raise ValueError(f"Run metadata method not found: {call.method!r}")
            ret = func(**(call.kwargs or {}))
            for out in call.outputs or []:
                raw = extract_value(ret, kind=out.kind, ref=out.ref)
                value = self._normalize_run_meta_value(raw, out.dtype)
                if out.key in result:
                    raise ValueError(f"Duplicate run metadata key {out.key!r}")
                result[out.key] = value
        return result

    def handle_command(self, action: str, params: dict[str, Any]) -> Any:
        if not isinstance(params, dict):
            raise TypeError("params must be a dict")

        if action.startswith("_"):
            raise NotImplementedError(f"Internal class methods not allowed {action!r}")

        if action in {"connect", "disconnect"}:
            raise NotImplementedError(f"Command {action!r} is not allowed via RPC")

        if action in self._stream_rpc:
            return self._stream_rpc[action](**params)

        func = getattr(self._device, action, None)
        if func is None or not callable(func):
            raise NotImplementedError(f"Unknown command {action!r}")

        try:
            return func(**params)
        except TypeError as e:
            raise TypeError(f"Bad parameters for command {action!r}: {e}") from e

    def _handle_rpc_request(self, req: dict[str, Any]) -> dict[str, Any]:
        """
        RPC request format (simple):
        {"id": "<str|int>", "action": "<str>", "params": {...}}

        Response:
        {"id": ..., "status": "OK|ERROR", "result": ..., "error": ...}
        """
        req_id = req.get("id")
        action = req.get("action")
        params = req.get("params", {})

        if not isinstance(action, str) or not isinstance(params, dict):
            return {"id": req_id, "status": "ERROR", "error": "Malformed request"}

        try:
            if action == "shutdown":
                self._stop = True
                return {"id": req_id, "status": "OK", "result": None}

            if action == "capabilities":
                return {"id": req_id, "status": "OK", "result": self.capabilities()}

            if action == "refresh_capabilities":
                if self._device_state == DeviceState.DISCONNECTED:
                    return {
                        "id": req_id,
                        "status": "OK",
                        "result": {"version": 1, "members": []},
                    }
                try:
                    self._refresh_capabilities_cache()
                except Exception as e:
                    self._last_error = f"capabilities discovery failed: {e!r}"
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": self._last_error,
                    }
                return {"id": req_id, "status": "OK", "result": self.capabilities()}

            if action == "get":
                if self._device_state == DeviceState.DISCONNECTED:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "Device is disconnected",
                    }
                name = params.get("name")
                if (
                    not isinstance(name, str)
                    or name.startswith("_")
                    or name in {"connect", "disconnect"}
                ):
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "Invalid member name",
                    }
                value = getattr(self._device, name)
                return {
                    "id": req_id,
                    "status": "OK",
                    "result": _jsonable_value(value),
                }

            if action == "set":
                if self._device_state == DeviceState.DISCONNECTED:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "Device is disconnected",
                    }
                name = params.get("name")
                if (
                    not isinstance(name, str)
                    or name.startswith("_")
                    or name in {"connect", "disconnect"}
                ):
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "Invalid member name",
                    }
                if self._members_cache is None:
                    self._refresh_capabilities_cache()
                spec = (self._members_cache or {}).get(name)
                if spec is None:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "Unknown member",
                    }
                if not spec.settable:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "Member is not settable",
                    }
                value = params.get("value")
                kind = _parse_simple_annotation(spec.value_annotation)
                if kind is not None:
                    try:
                        value = _coerce_simple_value(value, kind)
                    except Exception:
                        return {
                            "id": req_id,
                            "status": "ERROR",
                            "error": "Failed to coerce value",
                        }
                setattr(self._device, name, value)
                return {"id": req_id, "status": "OK", "result": None}

            if action == "status":
                return {
                    "id": req_id,
                    "status": "OK",
                    "result": {
                        "driver_state": self._driver_state().value,
                        "device_reachable": bool(self._device_reachable),
                        "device_state": self._device_state.value,
                        "last_ok_wall": self._last_ok_ts.t_wall
                        if self._last_ok_ts
                        else None,
                        "last_ok_mono": self._last_ok_ts.t_mono
                        if self._last_ok_ts
                        else None,
                        "last_error": self._last_error,
                    },
                }

            if action == "collect_run_metadata":
                if self._device_state == DeviceState.DISCONNECTED:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "Device is disconnected",
                    }
                result = self.collect_run_metadata()
                return {"id": req_id, "status": "OK", "result": result}

            if action == "connect_device":
                if self._device_state != DeviceState.DISCONNECTED:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": f"Device is already connected ({self._device_state.value})",
                    }
                self._connect_called = True
                self.connect_device()
                self._device_reachable = True
                self._device_state = DeviceState.OK
                self._last_ok_ts = self._now()
                self._last_error = None
                try:
                    self._refresh_capabilities_cache()
                except Exception:
                    pass
                return {"id": req_id, "status": "OK", "result": None}

            if action == "disconnect_device":
                if self._device_state == DeviceState.DISCONNECTED:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "Device is already disconnected",
                    }
                self.disconnect_device()
                self._device_reachable = False
                self._device_state = DeviceState.DISCONNECTED
                self._capabilities_cache = None
                self._members_cache = None
                return {"id": req_id, "status": "OK", "result": None}

            if action == "stream.context.set":
                stream = params.get("stream")
                context_id = params.get("context_id")
                fields = params.get("fields", {})
                if not isinstance(stream, str) or not stream:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "stream must be a non-empty string",
                    }
                if stream not in self._stream_outputs:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": f"Unknown stream {stream!r}",
                    }
                if context_id is None:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "context_id required",
                    }
                try:
                    ctx_id_int = int(context_id)
                except Exception:
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "context_id must be int",
                    }
                if fields is None:
                    fields = {}
                if not isinstance(fields, dict):
                    return {
                        "id": req_id,
                        "status": "ERROR",
                        "error": "fields must be a dict",
                    }
                self._stream_context[stream] = {
                    "context_id": ctx_id_int,
                    "context_fields": fields,
                }
                return {"id": req_id, "status": "OK", "result": None}

            if action == "stream.context.clear":
                stream = params.get("stream")
                if stream is None:
                    self._stream_context.clear()
                else:
                    if isinstance(stream, str):
                        self._stream_context.pop(stream, None)
                    else:
                        return {
                            "id": req_id,
                            "status": "ERROR",
                            "error": "stream must be a string",
                        }
                return {"id": req_id, "status": "OK", "result": None}

            # Guard: if disconnected, do not forward arbitrary commands
            if self._device_state == DeviceState.DISCONNECTED:
                return {
                    "id": req_id,
                    "status": "ERROR",
                    "error": "Device is disconnected",
                }

            if action in self._stream_rpc:
                result = self._stream_rpc[action](**params)
                self._device_reachable = True
                self._last_ok_ts = self._now()
                self._last_error = None
                return {"id": req_id, "status": "OK", "result": result}

            # Forward to subclass command handler (with optional coercion)
            if self._members_cache is None:
                self._refresh_capabilities_cache()
            spec = (self._members_cache or {}).get(action)
            if spec is not None and spec.kind == "method" and spec.params:
                coerced = dict(params)
                for param in spec.params:
                    if param.name in coerced:
                        kind = _parse_simple_annotation(param.annotation)
                        if kind is None:
                            continue
                        try:
                            coerced[param.name] = _coerce_simple_value(
                                coerced[param.name], kind
                            )
                        except Exception as e:
                            raise TypeError(
                                f"Bad parameters for command {action!r}: {param.name} ({e})"
                            ) from e
                params = coerced

            result = self.handle_command(action, params)

            self._device_reachable = True
            self._last_ok_ts = self._now()
            self._last_error = None

            return {"id": req_id, "status": "OK", "result": result}

        except Exception as e:
            self._last_error = f"command {action} failed: {e!r}"
            return {"id": req_id, "status": "ERROR", "error": str(e)}

    # ----------------------------
    # Internal helpers
    # ----------------------------

    def _now(self) -> Timestamp:
        return Timestamp(t_wall=time.time(), t_mono=time.monotonic())

    def _publish_heartbeat(self, *, loop_lag_s: float | None) -> None:
        self._heartbeat_seq += 1
        ts = self._now()

        payload = {
            "version": 1,
            "device_id": self.device_id,
            "driver_pid": os.getpid(),
            "seq": self._heartbeat_seq,
            "ts": self._ts_dict(ts),
            "driver_state": self._driver_state().value,
            "device_reachable": bool(self._device_reachable),
            "device_state": self._device_state.value,
            "last_ok_wall": self._last_ok_ts.t_wall if self._last_ok_ts else None,
            "last_ok_mono": self._last_ok_ts.t_mono if self._last_ok_ts else None,
            "last_error": self._last_error,
            "loop_lag_s": loop_lag_s,
        }

        topic = f"{self.device_id}/heartbeat".encode()
        self.pub.send_multipart([topic, json.dumps(payload).encode()])

    def _publish_telemetry(self) -> None:
        if self._device_state == DeviceState.DISCONNECTED:
            return
        self._telemetry_seq += 1
        bundle_ts = self._now()

        signals: dict[str, dict[str, Any]] = {}
        try:
            signals = self.read_telemetry()
            self._device_reachable = True
            if self._device_state == DeviceState.DISCONNECTED:
                self._device_state = DeviceState.OK
            self._last_ok_ts = bundle_ts
            self._last_error = None
        except Exception as e:
            self._device_reachable = False
            # Keep DISCONNECTED if it was disconnected, otherwise mark degraded
            if self._device_state != DeviceState.DISCONNECTED:
                self._device_state = DeviceState.DEGRADED
            self._last_error = f"telemetry read failed: {e!r}"
            signals = {}

        payload = {
            "version": 1,
            "device_id": self.device_id,
            "seq": self._telemetry_seq,
            "ts": self._ts_dict(bundle_ts),
            "signals": self._serialize_signals(signals, bundle_ts=bundle_ts),
        }

        topic = f"{self.device_id}/telemetry".encode()
        self.pub.send_multipart([topic, json.dumps(payload).encode()])

    def _init_stream_schema(self) -> None:
        for call in self._stream_calls:
            for out in call.outputs or []:
                if out.stream in self._stream_outputs:
                    raise ValueError(f"Duplicate stream {out.stream!r}")
                self._stream_outputs[out.stream] = out

    def _init_stream_wrappers(self) -> None:
        for call in self._stream_calls:
            method = call.method
            action_name = f"stream__{method}"

            def _make_wrapper(stream_call: StreamCall) -> Callable[..., Any]:
                def _as_shot_list(
                    value: Any,
                    out: StreamOut,
                    *,
                    n_batch: int,
                    allow_batch: bool,
                ) -> list[np.ndarray]:
                    if isinstance(value, np.ndarray):
                        if tuple(value.shape) == tuple(out.shape):
                            arr = value
                            if not arr.flags["C_CONTIGUOUS"]:
                                arr = np.ascontiguousarray(arr)
                            return [arr]
                        if allow_batch and (
                            value.ndim >= 1
                            and value.shape[0] == n_batch
                            and tuple(value.shape[1:]) == tuple(out.shape)
                        ):
                            shots = [value[i] for i in range(n_batch)]
                            out_list: list[np.ndarray] = []
                            for shot in shots:
                                arr = np.asarray(shot)
                                if tuple(arr.shape) != tuple(out.shape):
                                    raise ValueError(
                                        f"Stream {out.stream!r} shot shape mismatch: got {arr.shape}, expected {out.shape}"
                                    )
                                if not arr.flags["C_CONTIGUOUS"]:
                                    arr = np.ascontiguousarray(arr)
                                out_list.append(arr)
                            return out_list
                        if allow_batch:
                            raise ValueError(
                                f"Stream {out.stream!r} batched shape mismatch: got {value.shape}, expected ({n_batch}, {out.shape})"
                            )
                        raise ValueError(
                            f"Stream {out.stream!r} shot shape mismatch: got {value.shape}, expected {out.shape}"
                        )
                    if isinstance(value, (list, tuple)):
                        expected_len = n_batch if allow_batch else 1
                        if len(value) != expected_len:
                            raise ValueError(
                                f"Stream {out.stream!r} list length {len(value)} != {expected_len}"
                            )
                        out_list = []
                        for item in value:
                            arr = np.asarray(item)
                            if tuple(arr.shape) != tuple(out.shape):
                                raise ValueError(
                                    f"Stream {out.stream!r} shot shape mismatch: got {arr.shape}, expected {out.shape}"
                                )
                                if not arr.flags["C_CONTIGUOUS"]:
                                    arr = np.ascontiguousarray(arr)
                                out_list.append(arr)
                        return out_list
                    if n_batch == 1:
                        arr = np.asarray(value)
                        if tuple(arr.shape) != tuple(out.shape):
                            raise ValueError(
                                f"Stream {out.stream!r} shot shape mismatch: got {arr.shape}, expected {out.shape}"
                            )
                        if not arr.flags["C_CONTIGUOUS"]:
                            arr = np.ascontiguousarray(arr)
                        return [arr]
                    raise TypeError(
                        f"Stream {out.stream!r} expected ndarray or list/tuple for n_batch={n_batch}"
                    )

                def _wrapper(*args: Any, **kwargs: Any) -> Any:
                    func = getattr(self._device, stream_call.method, None)
                    if func is None or not callable(func):
                        raise NotImplementedError(
                            f"Stream method {stream_call.method!r} not found"
                        )

                    call_kwargs = dict(stream_call.kwargs or {})
                    call_kwargs.update(kwargs)

                    n_batch_provided = "n_batch" in call_kwargs
                    n_batch = int(call_kwargs.pop("n_batch", 1))
                    if n_batch < 1:
                        raise ValueError("n_batch must be >= 1")

                    if n_batch_provided:
                        try:
                            ret = func(*args, n_batch=n_batch, **call_kwargs)
                        except TypeError as e:
                            if "n_batch" in str(e) or "unexpected keyword" in str(e):
                                raise TypeError(
                                    f"Stream method {stream_call.method!r} does not support n_batch"
                                ) from e
                            raise
                    else:
                        ret = func(*args, **call_kwargs)

                    outputs = stream_call.outputs or []
                    if len(outputs) == 1:
                        out = outputs[0]
                        shots = _as_shot_list(
                            ret, out, n_batch=n_batch, allow_batch=n_batch_provided
                        )
                        return [self.publish_stream(out.stream, shot) for shot in shots]

                    if not isinstance(ret, dict):
                        raise TypeError(
                            "Stream call with multiple outputs must return dict[str, ndarray|list]"
                        )

                    shot_lists: dict[str, list[np.ndarray]] = {}
                    for out in outputs:
                        if out.stream not in ret:
                            raise KeyError(
                                f"Missing stream output {out.stream!r} in return dict"
                            )
                        shot_lists[out.stream] = _as_shot_list(
                            ret[out.stream],
                            out,
                            n_batch=n_batch,
                            allow_batch=n_batch_provided,
                        )

                    results: list[dict[str, Any]] = []
                    for i in range(n_batch):
                        descs: dict[str, Any] = {}
                        for out in outputs:
                            descs[out.stream] = self.publish_stream(
                                out.stream, shot_lists[out.stream][i]
                            )
                        results.append(descs)
                    return results

                return _wrapper

            wrapper = _make_wrapper(call)
            self._stream_rpc[action_name] = wrapper
            setattr(self, action_name, wrapper)

    def _make_telemetry_extractor(
        self, kind: ExtractorKind, ref: int | str | None
    ) -> Callable[[Any], Any]:
        if kind == "scalar":
            return _identity
        if ref is None:
            return _raise_extractor(f"Extractor kind {kind!r} requires ref")
        if kind in {"index", "key"}:
            return operator.itemgetter(ref)
        if kind == "attr":
            if not isinstance(ref, str):
                return _raise_extractor("attr extractor requires str ref")
            return operator.attrgetter(ref)
        return _raise_extractor(f"Unknown extractor kind {kind!r}")

    def _init_telemetry_plan(self) -> None:
        plan: list[_TelemetryCallPlan] = []
        missing_member = object()
        for call in self._telemetry_calls:
            member_static = inspect.getattr_static(
                self._device, call.method, missing_member
            )
            func: Callable[..., Any] | None = None
            attr_name: str | None = None
            kwargs = dict(call.kwargs or {})
            if member_static is missing_member:
                # May still resolve via dynamic __getattr__ on the driver.
                attr_name = call.method
            elif callable(member_static):
                bound = getattr(self._device, call.method, None)
                if bound is not None and callable(bound):
                    func = bound
                else:
                    attr_name = call.method
            else:
                if kwargs:
                    raise ValueError(
                        f"Telemetry property {call.method!r} does not accept kwargs"
                    )
                attr_name = call.method
            if func is None and attr_name is None:
                attr_name = call.method
            outs: list[_TelemetryOutPlan] = []
            for out in call.outputs or []:
                extractor = self._make_telemetry_extractor(out.kind, out.ref)
                outs.append(
                    _TelemetryOutPlan(
                        signal=out.signal,
                        units=out.units,
                        extractor=extractor,
                    )
                )
            plan.append(
                _TelemetryCallPlan(
                    func=func,
                    attr_name=attr_name,
                    kwargs=kwargs,
                    outputs=outs,
                )
            )
        self._telemetry_plan = plan

    def _ensure_stream_publishers(self) -> None:
        if self._stream_writers:
            return
        pid = os.getpid()
        for stream, out in self._stream_outputs.items():
            shm_name = f"cntx_{self.device_id}_{stream}_{pid}"
            writer = ShmRingWriter.create(
                shm_name,
                dtype=out.dtype,
                shape=tuple(out.shape),
                slot_count=out.ring_slots,
                layout_version=1,
            )
            self._stream_writers[stream] = writer
            self._stream_shm_names[stream] = shm_name

    def _cleanup_stream_publishers(self) -> None:
        for writer in self._stream_writers.values():
            try:
                writer.close()
                writer.unlink()
            except Exception:
                pass
        self._stream_writers.clear()

    def publish_stream(
        self,
        stream: str,
        arr: np.ndarray,
        *,
        t0_mono_ns: int | None = None,
        t0_wall_ns: int | None = None,
    ) -> dict[str, Any]:
        if stream not in self._stream_outputs:
            raise ValueError(f"Unknown stream {stream!r}")
        out = self._stream_outputs[stream]
        arr = np.asarray(arr)
        if arr.dtype != np.dtype(out.dtype):
            raise ValueError(
                f"Stream {stream!r} dtype mismatch: got {arr.dtype}, expected {out.dtype}"
            )
        if tuple(arr.shape) != tuple(out.shape):
            raise ValueError(
                f"Stream {stream!r} shape mismatch: got {arr.shape}, expected {out.shape}"
            )

        self._ensure_stream_publishers()
        writer = self._stream_writers[stream]
        t0_mono = int(t0_mono_ns or now_mono_ns())
        t0_wall = int(t0_wall_ns or now_wall_ns())
        seq = writer.write(arr, t0_mono_ns=t0_mono, t0_wall_ns=t0_wall)
        desc: dict[str, Any] = {
            "device_id": self.device_id,
            "stream": stream,
            "shm_name": self._stream_shm_names[stream],
            "layout_version": writer.layout.layout_version,
            "seq": int(seq),
            "t0_mono_ns": int(t0_mono),
            "t0_wall_ns": int(t0_wall),
        }
        context = self._stream_context.get(stream)
        if context:
            if "context_id" in context:
                desc["context_id"] = int(context["context_id"])
            fields = context.get("context_fields")
            if isinstance(fields, dict):
                desc["context_fields"] = fields

        topic = f"{self.device_id}/chunk_ready".encode("utf-8")
        payload = {
            "version": 1,
            "device_id": self.device_id,
            "stream": stream,
            "descriptor": desc,
        }
        self.pub.send_multipart([topic, json.dumps(payload).encode()])
        return desc

    @staticmethod
    def _normalize_run_meta_value(value: object, dtype_str: str) -> object:
        return coerce_scalar(value, dtype_str)

    def _driver_state(self) -> DriverState:
        # This is intentionally conservative. Subclasses can override via _device_state
        # and reported device reachability.
        if self._device_reachable:
            if self._device_state in {
                DeviceState.OK,
                DeviceState.DEGRADED,
                DeviceState.FAULT,
            }:
                return (
                    DriverState.OK
                    if self._device_state == DeviceState.OK
                    else DriverState.DEGRADED
                )
            return DriverState.OK
        return DriverState.DEGRADED if self._last_error else DriverState.INIT

    def _serialize_signals(
        self, signals: dict[str, dict[str, Any]], *, bundle_ts: Timestamp
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, s in signals.items():
            if not isinstance(name, str) or not isinstance(s, dict):
                continue

            value = s.get("value")
            units = s.get("units")
            quality = s.get("quality", "OK")
            ts_obj = s.get("ts")

            if units is not None and not isinstance(units, str):
                units = str(units)

            if quality not in {"OK", "BAD", "STALE", "MISSING"}:
                quality = "BAD"

            if isinstance(ts_obj, Timestamp):
                ts_dict = self._ts_dict(ts_obj)
            elif ts_obj is None:
                ts_dict = None
            else:
                # Allow passing {"t_wall": ..., "t_mono": ...}
                if (
                    isinstance(ts_obj, dict)
                    and "t_wall" in ts_obj
                    and "t_mono" in ts_obj
                ):
                    ts_dict = {
                        "t_wall": float(ts_obj["t_wall"]),
                        "t_mono": float(ts_obj["t_mono"]),
                    }
                else:
                    ts_dict = None

            out[name] = {
                "value": value,
                "units": units,
                "quality": quality,
                "ts": ts_dict,  # None means use TelemetryUpdate.ts
            }

        return out

    def _ts_dict(self, ts: Timestamp) -> dict[str, float]:
        return {"t_wall": float(ts.t_wall), "t_mono": float(ts.t_mono)}
