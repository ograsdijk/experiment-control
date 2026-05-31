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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
import zmq

from .contracts.messages import RpcActionRequest
from .driver_stream_wrappers import build_stream_wrapper
from .shm.shm_ring import ShmRingWriter, now_mono_ns, now_wall_ns
from .types import (
    DeviceState,
    DriverState,
    ExtractorKind,
    MemberParamSpec,
    MemberSpec,
    RunMetaCall,
    StreamCall,
    StreamOut,
    TelemetryCall,
    TelemetryOut,
    TelemetryQuality,
    Timestamp,
)
from .utils.rpc_dispatch import RpcDispatchRegistry
from .utils.value_coercion import coerce_scalar


# Cap on inbound REP message size set on the driver's RPC socket via
# zmq.MAXMSGSIZE in `connect_ipc`. The manager's normal RPC envelope
# (action + params) is < 16 KiB; 1 MiB is well above any legitimate
# request and well below what a malicious / misbehaving client could
# use to wedge the driver loop on a giant recv allocation.
_DRIVER_RPC_MAX_MSG_BYTES = 1 * 1024 * 1024


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
    dtype: str
    extractor: Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class _TelemetryCallPlan:
    func: Callable[..., Any] | None
    attr_name: str | None
    kwargs: dict[str, Any]
    outputs: list[_TelemetryOutPlan]
    method: str  # Original call.method, used as key in telemetry call_errors.


@dataclass(slots=True)
class _ScheduledStreamCallPlan:
    action_name: str
    period_s: float
    next_due_s: float


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
        register_timeout_ms: int = 2000,
        register_retries: int = 3,
        register_retry_delay_s: float = 0.2,
    ) -> None:
        if telemetry_period_s <= 0:
            raise ValueError("telemetry_period_s must be > 0")
        if heartbeat_period_s <= 0:
            raise ValueError("heartbeat_period_s must be > 0")
        if command_poll_period_s <= 0:
            raise ValueError("command_poll_period_s must be > 0")
        if register_timeout_ms <= 0:
            raise ValueError("register_timeout_ms must be > 0")
        if register_retries <= 0:
            raise ValueError("register_retries must be > 0")
        if register_retry_delay_s < 0:
            raise ValueError("register_retry_delay_s must be >= 0")

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
        self._register_timeout_ms = int(register_timeout_ms)
        self._register_retries = int(register_retries)
        self._register_retry_delay_s = float(register_retry_delay_s)

        self._telemetry_plan: list[_TelemetryCallPlan] = []
        self._init_telemetry_plan()

        self._stop = False

        self._telemetry_seq = 0
        self._heartbeat_seq = 0

        # Last known good hardware transaction time, set by subclasses when appropriate
        self._last_ok_ts: Timestamp | None = None
        self._last_error: str | None = None

        # Per-call telemetry error capture populated by read_telemetry on every
        # tick. Surfaced in the published telemetry bundle (bundle-level
        # `call_errors`) so the UI can show why a signal went BAD without
        # operators having to read driver stderr. Keys are the original
        # call.method names. (Per-signal errors are exposed via each signal's
        # own `error` field in the same bundle; no separate state needed.)
        self._telemetry_last_call_errors: dict[str, str] = {}
        # Rate-limit table for the stderr log of telemetry-call exceptions:
        # (call_method, exception_class_qualname) -> last_logged_monotonic.
        self._telemetry_log_last_mono: dict[tuple[str, str], float] = {}
        self._telemetry_log_period_s: float = 30.0

        # Subclass-managed hardware status flags
        self._device_reachable: bool = False
        self._device_state: DeviceState = DeviceState.UNKNOWN
        # Latch set by `_mark_device_unreachable` after a failed
        # get/set/command, cleared by the next successful action call.
        # Read by `_apply_telemetry_quality_state` and the no-telemetry-
        # signals branch of `_publish_telemetry` to refuse promoting
        # back to OK while a real device operation is still failing —
        # without this, a single failed set_property would be silently
        # papered over by the very next telemetry tick (telemetry uses
        # a different code path from get/set/command and can succeed
        # while every other op fails). Once the operator retries and
        # the action succeeds, the flag clears and telemetry can
        # promote normally.
        self._action_failed_since_last_ok: bool = False
        self._connect_called: bool = False
        self._capabilities_cache: dict[str, object] | None = None
        self._members_cache: dict[str, MemberSpec] | None = None

        self.ctx = zmq.Context()

        self.rpc = self.ctx.socket(zmq.REP)
        self.pub = self.ctx.socket(zmq.PUB)

        self._stream_writers: dict[str, ShmRingWriter] = {}
        self._stream_outputs: dict[str, StreamOut] = {}
        self._stream_rpc: dict[str, Callable[..., Any]] = {}
        self._scheduled_stream_calls: list[_ScheduledStreamCallPlan] = []
        self._stream_shm_names: dict[str, str] = {}
        self._stream_context: dict[str, dict[str, Any]] = {}
        self._init_stream_schema()
        self._init_stream_wrappers()
        self._init_scheduled_stream_calls()
        self._rpc_registry = self._build_rpc_registry()

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
        stream_start = time.monotonic()
        for plan in self._scheduled_stream_calls:
            plan.next_due_s = stream_start + plan.period_s
        intended_tick = time.monotonic()

        try:
            while not self._stop:
                now = time.monotonic()
                loop_lag_s = max(0.0, now - intended_tick)
                intended_tick = now + self.command_poll_period_s
                next_stream = self._next_scheduled_stream_due()

                # Wait for an RPC request, but wake up for periodic heartbeat/telemetry.
                timeout_s = min(
                    max(0.0, next_hb - now),
                    max(0.0, next_tel - now),
                    max(0.0, next_stream - now),
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

                self._publish_scheduled_streams(now=time.monotonic())

        finally:
            # Best-effort cleanup. Log the exception to stderr before swallowing
            # so the process supervisor (which pipes stderr to manager.log) sees
            # cleanup failures instead of devices silently sitting in UNKNOWN.
            try:
                if self._connect_called and self._device_state != DeviceState.DISCONNECTED:
                    self.disconnect_device()
            except Exception as exc:
                sys.stderr.write(
                    f"[driver][{self.device_id}] disconnect_device failed during cleanup: {exc!r}\n"
                )
                sys.stderr.flush()
            try:
                self._cleanup_stream_publishers()
            except Exception as exc:
                sys.stderr.write(
                    f"[driver][{self.device_id}] _cleanup_stream_publishers failed: {exc!r}\n"
                )
                sys.stderr.flush()
            try:
                self.disconnect_ipc()
            except Exception as exc:
                sys.stderr.write(
                    f"[driver][{self.device_id}] disconnect_ipc failed: {exc!r}\n"
                )
                sys.stderr.flush()

    # ----------------------------
    # Subclass hooks
    # ----------------------------

    def connect_ipc(self) -> None:
        # Cap inbound REP message size at 1 MiB. The manager's
        # call_device_rpc envelope is normally < 16 KiB (action +
        # params), so 1 MiB is well above any legitimate request.
        # Without the cap, a misbehaving (or malicious) client sending
        # an oversize JSON payload would have the recv_json call
        # below allocate up to MAXMSGSIZE (default unlimited),
        # potentially wedging the driver loop on a large allocation.
        # libzmq enforces MAXMSGSIZE at the recv layer; oversize
        # messages are dropped at the source socket and the client
        # gets a disconnect.
        self.rpc.setsockopt(zmq.MAXMSGSIZE, _DRIVER_RPC_MAX_MSG_BYTES)
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
        msg = {
            "type": "register",
            "device_id": self.device_id,
            "rpc_endpoint": self.rpc_endpoint,
            "pub_endpoint": self.pub_endpoint,
            "capabilities": self.capabilities(),
        }
        last_exc: Exception | None = None
        for attempt in range(1, self._register_retries + 1):
            reg = self.ctx.socket(zmq.REQ)
            reg.setsockopt(zmq.LINGER, 0)
            reg.setsockopt(zmq.SNDTIMEO, self._register_timeout_ms)
            reg.setsockopt(zmq.RCVTIMEO, self._register_timeout_ms)
            reg.connect(self.registry_endpoint)
            try:
                reg.send_json(msg)
                _ack = reg.recv_json()  # simple ACK
                return
            except Exception as exc:
                last_exc = exc
            finally:
                try:
                    reg.close(0)
                except Exception:
                    pass
            if attempt < self._register_retries and self._register_retry_delay_s > 0:
                time.sleep(self._register_retry_delay_s)
        raise RuntimeError(
            f"Manager registration failed after {self._register_retries} attempts"
        ) from last_exc

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
        # Reset per-tick call-error capture; populated below when a telemetry
        # call raises. Surfaced as bundle-level `call_errors` by
        # _publish_telemetry. Per-signal extractor failures are not tracked
        # here because they already flow out via each signal's own `error`
        # field in `out`.
        self._telemetry_last_call_errors = {}

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
                        if val is not None:
                            val = coerce_scalar(val, o.dtype)
                        out[o.signal] = {
                            "value": val,
                            "units": o.units,
                            "quality": TelemetryQuality.OK,
                            "ts": None,
                        }
                    except Exception as e:
                        err_text = self._telemetry_format_error(e)
                        out[o.signal] = {
                            "value": None,
                            "units": o.units,
                            "quality": TelemetryQuality.BAD,
                            "ts": None,
                            "error": err_text,
                        }
            except Exception as e:
                err_text = self._telemetry_format_error(e)
                self._telemetry_last_call_errors[plan.method] = err_text
                self._telemetry_log_call_exception(plan.method, e)
                for o in plan.outputs:
                    out[o.signal] = {
                        "value": None,
                        "units": o.units,
                        "quality": TelemetryQuality.BAD,
                        "ts": None,
                        "error": err_text,
                    }

        return out

    @staticmethod
    def _telemetry_format_error(exc: BaseException, *, max_len: int = 200) -> str:
        """Render a telemetry exception as a single-line, length-bounded string."""
        text = repr(exc)
        if len(text) > max_len:
            text = text[: max_len - 3] + "..."
        return text

    def _telemetry_log_call_exception(self, method: str, exc: BaseException) -> None:
        """Write a telemetry-call exception to stderr, rate-limited per (method, exc-type).

        Without this, the supervisor's per-device manager.log shows a device
        sitting in DEGRADED with no diagnostic, because the only previous
        record of the exception was in `_last_error` (overwritten on the next
        tick). One emission per (method, exception class) per
        `_telemetry_log_period_s` seconds is enough to surface the failure to
        operators without flooding the log on every tick.
        """
        try:
            key = (method, type(exc).__qualname__)
            now = time.monotonic()
            last = self._telemetry_log_last_mono.get(key)
            if last is not None and (now - last) < self._telemetry_log_period_s:
                return
            self._telemetry_log_last_mono[key] = now
            sys.stderr.write(
                f"[driver][{self.device_id}] telemetry call {method!r} raised "
                f"{type(exc).__name__}: {exc!r}\n"
            )
            sys.stderr.flush()
        except Exception:
            # Never let the log path itself break telemetry.
            pass

    @staticmethod
    def _telemetry_quality_counts(signals: dict[str, dict[str, Any]]) -> dict[str, int]:
        counts = {"OK": 0, "BAD": 0, "MISSING": 0, "STALE": 0, "OTHER": 0}
        for payload in signals.values():
            if not isinstance(payload, dict):
                counts["OTHER"] += 1
                continue
            quality = payload.get("quality")
            if isinstance(quality, TelemetryQuality):
                key = quality.value
            else:
                key = str(quality or "OTHER").upper()
            if key not in counts:
                key = "OTHER"
            counts[key] += 1
        return counts

    def _apply_telemetry_quality_state(self, signals: dict[str, dict[str, Any]]) -> None:
        counts = self._telemetry_quality_counts(signals)
        total = sum(counts.values())
        ok_count = counts.get("OK", 0)
        bad_count = total - ok_count
        if total == 0 or ok_count == 0:
            self._device_reachable = False
            if self._device_state != DeviceState.DISCONNECTED:
                self._device_state = DeviceState.DEGRADED
            self._last_error = f"telemetry returned no OK signals ({counts})"
            return
        # Even when telemetry looks fine, refuse to promote back to OK
        # while an action call (get/set/command) is still failing —
        # telemetry runs on a different code path and can succeed
        # while every set_property to the hardware raises. The latch
        # is cleared by the action-success helpers
        # (_mark_action_succeeded), so once the operator retries and
        # the action succeeds, the next telemetry tick can promote
        # normally.
        if self._action_failed_since_last_ok:
            self._device_reachable = False
            if self._device_state != DeviceState.DISCONNECTED:
                self._device_state = DeviceState.DEGRADED
            # Keep the existing _last_error from _mark_device_unreachable
            # in place — it identifies the failing action, which is the
            # diagnostic operators need.
            return
        self._device_reachable = True
        if bad_count > 0:
            if self._device_state != DeviceState.DISCONNECTED:
                self._device_state = DeviceState.DEGRADED
            self._last_error = f"telemetry partially degraded ({counts})"
            return
        if self._device_state == DeviceState.DISCONNECTED:
            self._device_state = DeviceState.OK
        elif self._device_state == DeviceState.DEGRADED:
            self._device_state = DeviceState.OK
        self._last_error = None

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

    @staticmethod
    def _rpc_ok(req_id: Any, result: Any) -> dict[str, Any]:
        return {"id": req_id, "status": "OK", "result": result}

    @staticmethod
    def _rpc_error(
        req_id: Any,
        error: str,
        *,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        resp: dict[str, Any] = {"id": req_id, "status": "ERROR", "error": str(error)}
        if error_code is not None:
            resp["error_code"] = error_code
        return resp

    def _ensure_rpc_registry(self) -> RpcDispatchRegistry:
        registry = getattr(self, "_rpc_registry", None)
        if isinstance(registry, RpcDispatchRegistry):
            return registry
        registry = self._build_rpc_registry()
        self._rpc_registry = registry
        return registry

    def _build_rpc_registry(self) -> RpcDispatchRegistry:
        return RpcDispatchRegistry(
            handlers={
                "shutdown": self._rpc_route_shutdown,
                "capabilities": self._rpc_route_capabilities,
                "refresh_capabilities": self._rpc_route_refresh_capabilities,
                "get": self._rpc_route_get,
                "set": self._rpc_route_set,
                "status": self._rpc_route_status,
                "collect_run_metadata": self._rpc_route_collect_run_metadata,
                "identity": self._rpc_route_identity,
                "connect_device": self._rpc_route_connect_device,
                "disconnect_device": self._rpc_route_disconnect_device,
                "stream.context.set": self._rpc_route_stream_context_set,
                "stream.context.clear": self._rpc_route_stream_context_clear,
            }
        )

    def _rpc_route_shutdown(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        self._stop = True
        return self._rpc_ok(req_id, None)

    def _rpc_route_capabilities(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        return self._rpc_ok(req_id, self.capabilities())

    def _rpc_route_refresh_capabilities(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        if self._device_state == DeviceState.DISCONNECTED:
            return self._rpc_ok(req_id, {"version": 1, "members": []})
        try:
            self._refresh_capabilities_cache()
        except Exception as e:
            self._last_error = f"capabilities discovery failed: {e!r}"
            return self._rpc_error(req_id, self._last_error)
        return self._rpc_ok(req_id, self.capabilities())

    def _rpc_route_get(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        params = req.get("params", {})
        if self._device_state == DeviceState.DISCONNECTED:
            return self._rpc_error(req_id, "Device is disconnected")
        name = params.get("name")
        if (
            not isinstance(name, str)
            or name.startswith("_")
            or name in {"connect", "disconnect"}
        ):
            return self._rpc_error(req_id, "Invalid member name")
        try:
            value = getattr(self._device, name)
        except Exception as exc:
            # The device's getattr raised (e.g. VISA read error, hardware
            # disconnect mid-call). Demote health so the manager sees the
            # device as DEGRADED on its next heartbeat / telemetry tick
            # instead of continuing to report "OK" while every read fails.
            self._mark_device_unreachable(f"get {name!r} failed: {exc!r}")
            return self._rpc_error(req_id, f"get failed: {exc}")
        # Successful device read clears the action-failure latch so the
        # next telemetry tick is free to promote back to OK (see
        # _mark_device_unreachable for why the latch exists).
        self._mark_action_succeeded()
        return self._rpc_ok(req_id, _jsonable_value(value))

    def _rpc_route_set(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        params = req.get("params", {})
        if self._device_state == DeviceState.DISCONNECTED:
            return self._rpc_error(req_id, "Device is disconnected")
        name = params.get("name")
        if (
            not isinstance(name, str)
            or name.startswith("_")
            or name in {"connect", "disconnect"}
        ):
            return self._rpc_error(req_id, "Invalid member name")
        if self._members_cache is None:
            self._refresh_capabilities_cache()
        spec = (self._members_cache or {}).get(name)
        if spec is None:
            return self._rpc_error(req_id, "Unknown member")
        if not spec.settable:
            return self._rpc_error(req_id, "Member is not settable")
        value = params.get("value")
        kind: str | None = None
        if spec.params:
            kind = _parse_simple_annotation(spec.params[0].annotation)
        if kind is None:
            kind = _parse_simple_annotation(spec.value_annotation)
        if kind is not None:
            try:
                value = _coerce_simple_value(value, kind)
            except Exception:
                return self._rpc_error(req_id, "Failed to coerce value")
        try:
            setattr(self._device, name, value)
        except Exception as exc:
            # See _rpc_route_get for rationale: a failed setattr on the
            # underlying device (VISA write error, hardware disconnect,
            # validation rejection inside a property setter, etc.) must
            # not leave the device looking OK in the next telemetry
            # bundle while every subsequent get/set silently fails.
            self._mark_device_unreachable(f"set {name!r} failed: {exc!r}")
            return self._rpc_error(req_id, f"set failed: {exc}")
        # Successful device write clears the action-failure latch.
        self._mark_action_succeeded()
        return self._rpc_ok(req_id, None)

    def _mark_action_succeeded(self) -> None:
        """Counterpart to `_mark_device_unreachable`: a get/set/command
        completed successfully, so the failure-latch can be cleared
        and the next telemetry tick is free to promote the device
        back to OK (when telemetry quality also looks good).

        Callers must already have set `_last_ok_ts = self._now()`
        themselves (this helper does not touch the timestamp, only the
        latch — keeping the call sites' existing semantics intact).
        """
        self._action_failed_since_last_ok = False

    def _mark_device_unreachable(self, reason: str) -> None:
        """Demote device health after an unexpected device-side failure.

        Sets `_device_reachable = False` and transitions
        `_device_state` to DEGRADED (unless already DISCONNECTED). Also
        sets `_action_failed_since_last_ok = True` — a latch that
        prevents `_apply_telemetry_quality_state` (and the no-
        telemetry-signals branch of `_publish_telemetry`) from
        silently promoting the device back to OK on the next
        telemetry tick. Telemetry runs on a different code path from
        get/set/command and can succeed even while every action call
        fails (e.g. driver caches the last-known telemetry values
        but every set_property to the hardware raises VISA timeout).

        The latch is cleared by the action-success paths that already
        set `_last_ok_ts` — so once an operator retries the failing
        operation and it succeeds, telemetry can promote normally on
        the next tick.

        Records `reason` into `_last_error` (capped at ~200 chars).
        """
        self._device_reachable = False
        if self._device_state != DeviceState.DISCONNECTED:
            self._device_state = DeviceState.DEGRADED
        self._action_failed_since_last_ok = True
        if len(reason) > 200:
            reason = reason[:197] + "..."
        self._last_error = reason

    def _rpc_route_status(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        return self._rpc_ok(
            req_id,
            {
                "driver_state": self._driver_state().value,
                "device_reachable": bool(self._device_reachable),
                "device_state": self._device_state.value,
                "last_ok_wall": self._last_ok_ts.t_wall if self._last_ok_ts else None,
                "last_ok_mono": self._last_ok_ts.t_mono if self._last_ok_ts else None,
                "last_error": self._last_error,
            },
        )

    def _rpc_route_collect_run_metadata(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        if self._device_state == DeviceState.DISCONNECTED:
            return self._rpc_error(req_id, "Device is disconnected")
        return self._rpc_ok(req_id, self.collect_run_metadata())

    def _rpc_route_identity(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        func = getattr(self._device, "identity", None)
        if func is None or not callable(func):
            return self._rpc_error(
                req_id,
                "identity not supported",
                error_code="identity_not_supported",
            )
        result = func()
        return self._rpc_ok(req_id, _jsonable_value(result))

    def _rpc_route_connect_device(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        if self._device_state != DeviceState.DISCONNECTED:
            return self._rpc_error(
                req_id,
                f"Device is already connected ({self._device_state.value})",
                error_code="already_connected",
            )
        self._connect_called = True
        self.connect_device()
        self._device_reachable = True
        self._device_state = DeviceState.OK
        self._last_ok_ts = self._now()
        self._last_error = None
        # Fresh successful connect clears the action-failure latch from
        # any prior session.
        self._mark_action_succeeded()
        try:
            self._refresh_capabilities_cache()
        except Exception:
            pass
        return self._rpc_ok(req_id, None)

    def _rpc_route_disconnect_device(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        if self._device_state == DeviceState.DISCONNECTED:
            return self._rpc_error(
                req_id,
                "Device is already disconnected",
                error_code="already_disconnected",
            )
        self.disconnect_device()
        self._device_reachable = False
        self._device_state = DeviceState.DISCONNECTED
        self._capabilities_cache = None
        self._members_cache = None
        return self._rpc_ok(req_id, None)

    def _rpc_route_stream_context_set(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        params = req.get("params", {})
        stream = params.get("stream")
        context_id = params.get("context_id")
        fields = params.get("fields", {})
        if not isinstance(stream, str) or not stream:
            return self._rpc_error(req_id, "stream must be a non-empty string")
        if stream not in self._stream_outputs:
            return self._rpc_error(req_id, f"Unknown stream {stream!r}")
        if context_id is None:
            return self._rpc_error(req_id, "context_id required")
        try:
            ctx_id_int = int(context_id)
        except Exception:
            return self._rpc_error(req_id, "context_id must be int")
        if fields is None:
            fields = {}
        if not isinstance(fields, dict):
            return self._rpc_error(req_id, "fields must be a dict")
        self._stream_context[stream] = {
            "context_id": ctx_id_int,
            "context_fields": fields,
        }
        return self._rpc_ok(req_id, None)

    def _rpc_route_stream_context_clear(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        params = req.get("params", {})
        stream = params.get("stream")
        if stream is None:
            self._stream_context.clear()
            return self._rpc_ok(req_id, None)
        if isinstance(stream, str):
            self._stream_context.pop(stream, None)
            return self._rpc_ok(req_id, None)
        return self._rpc_error(req_id, "stream must be a string")

    def _rpc_dispatch_device_command(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        action = str(req.get("action", ""))
        params = req.get("params", {})

        # Guard: if disconnected, do not forward arbitrary commands.
        if self._device_state == DeviceState.DISCONNECTED:
            return self._rpc_error(req_id, "Device is disconnected")

        if action in self._stream_rpc:
            try:
                result = self._stream_rpc[action](**params)
            except Exception as exc:
                self._mark_device_unreachable(
                    f"stream rpc {action!r} failed: {exc!r}"
                )
                raise
            self._device_reachable = True
            self._last_ok_ts = self._now()
            self._last_error = None
            self._mark_action_succeeded()
            return self._rpc_ok(req_id, result)

        # Forward to subclass command handler (with optional coercion).
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

        try:
            result = self.handle_command(action, params)
        except Exception as exc:
            self._mark_device_unreachable(
                f"command {action!r} failed: {exc!r}"
            )
            raise
        self._device_reachable = True
        self._last_ok_ts = self._now()
        self._last_error = None
        self._mark_action_succeeded()
        return self._rpc_ok(req_id, result)

    def _handle_rpc_request(self, req: dict[str, Any]) -> dict[str, Any]:
        """
        RPC request format (simple):
        {"id": "<str|int>", "action": "<str>", "params": {...}}

        Response:
        {"id": ..., "status": "OK|ERROR", "result": ..., "error": ...}
        """
        rpc = RpcActionRequest.parse(
            req,
            action_field="action",
            request_id_field="id",
            fallback_action_field="type",
        )
        if rpc is None:
            req_id = req.get("id")
            return {"id": req_id, "status": "ERROR", "error": "Malformed request"}
        rpc_req = rpc.as_dispatch_payload(request_id_field="id")
        try:
            routed = self._ensure_rpc_registry().dispatch(rpc_req)
            if routed is not None:
                return routed
            return self._rpc_dispatch_device_command(rpc_req)
        except Exception as e:
            self._last_error = f"command {rpc.action} failed: {e!r}"
            return {"id": rpc.request_id, "status": "ERROR", "error": str(e)}

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
            if self.telemetry_signal_names():
                self._apply_telemetry_quality_state(signals)
            else:
                # Devices without telemetry signals had read_telemetry
                # return cleanly (no exception). Previously this
                # unconditionally promoted to OK every tick — silently
                # erasing any `_mark_device_unreachable` from a failed
                # action call. Gate the promote on the action-failure
                # latch so the demotion survives until an action
                # actually succeeds.
                if not self._action_failed_since_last_ok:
                    self._device_reachable = True
                    if self._device_state == DeviceState.DISCONNECTED:
                        self._device_state = DeviceState.OK
                    self._last_error = None
                else:
                    self._device_reachable = False
                    if self._device_state != DeviceState.DISCONNECTED:
                        self._device_state = DeviceState.DEGRADED
                    # Keep the action-failure _last_error as set by
                    # _mark_device_unreachable.
            if self._device_reachable:
                self._last_ok_ts = bundle_ts
        except Exception as e:
            self._device_reachable = False
            # Keep DISCONNECTED if it was disconnected, otherwise mark degraded
            if self._device_state != DeviceState.DISCONNECTED:
                self._device_state = DeviceState.DEGRADED
            self._last_error = f"telemetry read failed: {e!r}"
            signals = {}
            # read_telemetry didn't get a chance to populate per-call errors
            # for this exceptional path, but we still want operators to see
            # the failure. Record under a synthetic key.
            err_text = self._telemetry_format_error(e)
            self._telemetry_last_call_errors = {"<read_telemetry>": err_text}
            self._telemetry_log_call_exception("<read_telemetry>", e)

        payload: dict[str, Any] = {
            "version": 1,
            "device_id": self.device_id,
            "seq": self._telemetry_seq,
            "ts": self._ts_dict(bundle_ts),
            "signals": self._serialize_signals(signals, bundle_ts=bundle_ts),
        }
        # Surface per-call errors at the bundle level so the UI can show why
        # the device went DEGRADED without operators having to read stderr.
        if self._telemetry_last_call_errors:
            payload["call_errors"] = dict(self._telemetry_last_call_errors)

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
            action_name = f"stream__{call.method}"
            wrapper = build_stream_wrapper(runner=self, stream_call=call)
            self._stream_rpc[action_name] = wrapper
            setattr(self, action_name, wrapper)

    def _init_scheduled_stream_calls(self) -> None:
        self._scheduled_stream_calls.clear()
        now = time.monotonic()
        for call in self._stream_calls:
            if call.period_s is None:
                continue
            action_name = f"stream__{call.method}"
            if action_name not in self._stream_rpc:
                raise ValueError(f"Scheduled stream action {action_name!r} was not registered")
            self._scheduled_stream_calls.append(
                _ScheduledStreamCallPlan(
                    action_name=action_name,
                    period_s=float(call.period_s),
                    next_due_s=now + float(call.period_s),
                )
            )

    def _next_scheduled_stream_due(self) -> float:
        if not self._scheduled_stream_calls:
            return float("inf")
        return min(plan.next_due_s for plan in self._scheduled_stream_calls)

    def _publish_scheduled_streams(self, *, now: float) -> None:
        if self._device_state == DeviceState.DISCONNECTED:
            return
        for plan in self._scheduled_stream_calls:
            if now < plan.next_due_s:
                continue
            missed = max(0, int((now - plan.next_due_s) // plan.period_s))
            plan.next_due_s += (missed + 1) * plan.period_s
            try:
                self._stream_rpc[plan.action_name]()
                ts = self._now()
                self._device_reachable = True
                if self._device_state == DeviceState.DISCONNECTED:
                    self._device_state = DeviceState.OK
                self._last_ok_ts = ts
                self._last_error = None
            except Exception as e:
                if self._device_state != DeviceState.DISCONNECTED:
                    self._device_state = DeviceState.DEGRADED
                self._last_error = f"scheduled stream {plan.action_name} failed: {e!r}"

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
                        dtype=out.dtype,
                        extractor=extractor,
                    )
                )
            plan.append(
                _TelemetryCallPlan(
                    func=func,
                    attr_name=attr_name,
                    kwargs=kwargs,
                    outputs=outs,
                    method=call.method,
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
                dtype=out.numpy_dtype(),
                shape=tuple(out.shape),
                slot_count=out.ring_slots,
                layout_version=3 if out.kind == "records" else 1,
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
        expected_dtype = out.numpy_dtype()
        arr = (
            np.asarray(arr, dtype=expected_dtype)
            if out.kind == "records"
            else np.asarray(arr)
        )
        if arr.dtype != expected_dtype:
            raise ValueError(
                f"Stream {stream!r} dtype mismatch: got {arr.dtype}, expected {expected_dtype}"
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
            "stream_kind": out.kind,
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

        topic = f"{self.device_id}/chunk_ready".encode()
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
            error = s.get("error")

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

            serialized: dict[str, Any] = {
                "value": value,
                "units": units,
                "quality": quality,
                "ts": ts_dict,  # None means use TelemetryUpdate.ts
            }
            if error is not None:
                # Coerce to str defensively in case a driver puts a non-str
                # error value through; truncate to keep the payload bounded.
                error_text = str(error)
                if len(error_text) > 200:
                    error_text = error_text[:197] + "..."
                serialized["error"] = error_text
            out[name] = serialized

        return out

    def _ts_dict(self, ts: Timestamp) -> dict[str, float]:
        return {"t_wall": float(ts.t_wall), "t_mono": float(ts.t_mono)}
