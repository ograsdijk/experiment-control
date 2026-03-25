from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

import zmq

from ..capabilities import capabilities_payload, method, param
from ..processes.process_base import ManagedProcessBase
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.logging_levels import normalize_log_severity as normalize_log_level
from ..utils.rpc_dispatch import RpcDispatchRegistry
from ..utils.yaml_helpers import load_yaml_text
from ..utils.zmq_helpers import safe_json_loads
from .ast import (
    AdaptiveStep,
    AssignStep,
    AtomicStep,
    CallStep,
    ForStep,
    IfStep,
    ParallelStep,
    PauseStep,
    RepeatStep,
    SequenceSpec,
    SetContextStep,
    SetStep,
    SleepStep,
    Step,
    UseStep,
    WaitUntilStep,
    WhileStep,
    parse_sequence,
)
from .condition_validation import has_error_diagnostics, validate_sequence_conditions
from .eval import render_templates, to_attrdict
from .library import SequenceLibrary, SequenceLibraryEntry
from .ranges import generate_from_gen
from .runtime import SequencerRuntime

Json = dict[str, Any]
_EXTERNAL_FAULT_SEVERITIES = {"warning", "error", "critical"}
_DEFAULT_PROGRESS_EVENT_PERIOD_S = 0.3
_STREAM_CONTEXT_SET_RETRY_DEADLINE_S = 6.0
_STREAM_CONTEXT_SET_INITIAL_BACKOFF_S = 0.05
_STREAM_CONTEXT_SET_MAX_BACKOFF_S = 0.5
_STREAM_CONTEXT_SET_TRANSIENT_ERRORS = (
    "timeout",
    "temporarily unavailable",
    "resource temporarily unavailable",
    "would block",
    "again",
    "busy",
    "restarting",
    "not connected",
    "disconnected",
)
_DRIVER_BUILTIN_ACTIONS = {
    "capabilities",
    "refresh_capabilities",
    "get",
    "set",
    "status",
    "collect_run_metadata",
    "connect_device",
    "disconnect_device",
    "stream.context.set",
    "stream.context.clear",
}


def _normalize_log_severity(raw: Any) -> str:
    return normalize_log_level(raw, default="info")


def _should_trigger_external_sequencer_fault(entry: Json) -> tuple[bool, str | None]:
    if not isinstance(entry, dict):
        return False, None
    severity = _normalize_log_severity(entry.get("severity"))
    if severity not in _EXTERNAL_FAULT_SEVERITIES:
        return False, None

    source_kind = str(entry.get("source_kind", "") or "").strip().lower()
    source_id = str(entry.get("source_id", "") or "").strip()
    process_id = str(entry.get("process_id", "") or "").strip()
    topic = str(entry.get("topic", "") or "").strip()
    message = str(entry.get("message", "") or "").strip()

    if source_kind == "driver":
        target = source_id or str(entry.get("device_id", "") or "").strip() or "driver"
    elif source_kind == "process" and (
        source_id == "hdf_writer" or process_id == "hdf_writer"
    ):
        target = "hdf_writer"
    else:
        return False, None

    detail = message or topic or "external fault"
    return True, f"External fault from {source_kind}:{target}: {detail}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control sequencer")
    add_manager_args(p)
    add_process_id_arg(p, default="sequencer")
    add_rpc_timeout_arg(p, default_ms=2000)
    add_heartbeat_args(p, default_period_s=1.0)
    return p.parse_args(argv)


class SequencerProcess(ManagedProcessBase):
    def __init__(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        process_id: str,
        rpc_timeout_ms: int = 2000,
        autoload_path: str | None = None,
        sequence_library_path: str | None = None,
        autoload_sequence_id: str | None = None,
        library_description_policy: str = "warn",
        progress_event_period_s: float = _DEFAULT_PROGRESS_EVENT_PERIOD_S,
        heartbeat_endpoint: str | None = None,
        heartbeat_period_s: float = 1.0,
    ) -> None:
        super().__init__(
            process_id=process_id,
            heartbeat_endpoint=heartbeat_endpoint,
            heartbeat_period_s=heartbeat_period_s,
        )
        self._manager_rpc = manager_rpc
        self._manager_pub = manager_pub
        self._rpc_timeout_ms = int(rpc_timeout_ms)

        # Control plane (ROUTER)
        self._init_rpc_router()

        self._manager = self._init_manager_client(
            manager_rpc=self._manager_rpc,
            manager_pub=self._manager_pub,
            rpc_timeout_ms=self._rpc_timeout_ms,
            process_id=self._process_id,
            subscribe_telemetry=True,
        )
        self._log_sub = self._ctx.socket(zmq.SUB)
        self._log_sub.setsockopt(zmq.SUBSCRIBE, b"manager.log")
        self._log_sub.setsockopt(zmq.RCVTIMEO, 100)
        self._log_sub.setsockopt(zmq.LINGER, 0)
        self._log_sub.connect(self._manager_pub)
        self._analysis_sub = self._ctx.socket(zmq.SUB)
        self._analysis_sub.setsockopt(zmq.SUBSCRIBE, b"manager.stream_analysis.output")
        self._analysis_sub.setsockopt(zmq.RCVTIMEO, 100)
        self._analysis_sub.setsockopt(zmq.LINGER, 0)
        self._analysis_sub.connect(self._manager_pub)
        self._init_poller(
            extra=[
                (self._log_sub, zmq.POLLIN),
                (self._analysis_sub, zmq.POLLIN),
            ]
        )

        self._runtime = SequencerRuntime(
            call_device=self._call_device,
            get_telemetry=self._get_telemetry,
            set_stream_context=self._set_stream_context,
            resolve_use=self._resolve_use_sequence_spec,
        )
        self._context_columns: dict[str, str] | None = None
        self._loaded_sequence_source: str | None = None
        self._loaded_sequence_source_kind: str | None = None
        self._loaded_sequence_text: str | None = None
        self._active_sequence_id: str | None = None
        self._sequence_library_path = (
            str(sequence_library_path).strip() if sequence_library_path else None
        )
        self._autoload_sequence_id = (
            str(autoload_sequence_id).strip() if autoload_sequence_id else None
        )
        self._library_description_policy = str(library_description_policy or "warn").strip()
        self._sequence_library: SequenceLibrary | None = None
        self._sequence_library_error: str | None = None
        self._sequence_library_warnings: list[str] = []
        try:
            period = float(progress_event_period_s)
        except Exception:
            period = _DEFAULT_PROGRESS_EVENT_PERIOD_S
        if not (period > 0.0):
            period = _DEFAULT_PROGRESS_EVENT_PERIOD_S
        self._progress_event_period_s = period
        self._last_progress_event_mono = 0.0
        self._last_progress_event_signature: tuple[Any, ...] | None = None
        self._autoload_error: str | None = None
        self._autoload_error_ts_wall: float | None = None
        self._autoload_error_source: str | None = None
        self._pending_log_payloads: deque[Json] = deque(maxlen=200)
        self._rpc_registry = self._build_rpc_registry()

        self._advertise_process_rpc()
        self._start_heartbeat_thread(state_provider=lambda: self._runtime.state)
        self._last_error_sent = False
        if self._sequence_library_path:
            self._load_sequence_library(initial=True)
            if self._autoload_sequence_id:
                self._try_autoload_sequence_id(self._autoload_sequence_id)
        if autoload_path and self._loaded_sequence_text is None:
            self._try_autoload_path(str(autoload_path))

    def _resolve_use_sequence_spec(self, sequence_id: str):
        if self._sequence_library is None:
            raise RuntimeError(
                f"use step {sequence_id!r} requires sequence_library_path to be configured"
            )
        return self._sequence_library.get_spec(sequence_id)

    def _set_loaded_sequence(
        self,
        *,
        spec: Any,
        text: str,
        source: str,
        source_kind: str,
        active_sequence_id: str | None,
    ) -> None:
        self._runtime.load(spec)
        self._context_columns = spec.context_columns
        self._loaded_sequence_source = source
        self._loaded_sequence_source_kind = source_kind
        self._loaded_sequence_text = text
        self._active_sequence_id = active_sequence_id
        self._clear_autoload_error()
        self._last_progress_event_signature = None
        self._last_progress_event_mono = 0.0

    def _set_loaded_sequence_from_library_entry(
        self, entry: SequenceLibraryEntry
    ) -> None:
        self._set_loaded_sequence(
            spec=entry.spec,
            text=entry.text,
            source=entry.path,
            source_kind="library",
            active_sequence_id=entry.sequence_id,
        )

    def _load_sequence_library(self, *, initial: bool = False) -> bool:
        if not self._sequence_library_path:
            self._sequence_library = None
            self._sequence_library_error = None
            self._sequence_library_warnings = []
            return False
        try:
            library = SequenceLibrary(
                manifest_path=self._sequence_library_path,
                description_policy=self._library_description_policy,
            )
            library.reload()
        except Exception as e:
            self._sequence_library_error = str(e)
            if initial:
                self._publish_log(
                    severity="error",
                    message=f"sequencer sequence library load failed: {e}",
                )
            return False
        self._sequence_library = library
        self._sequence_library_error = None
        self._sequence_library_warnings = list(library.warnings)
        for warning in self._sequence_library_warnings:
            self._publish_log(
                severity="warning",
                message=f"sequencer sequence library warning: {warning}",
            )
        return True

    def _try_autoload_sequence_id(self, sequence_id: str) -> None:
        seq_id = str(sequence_id or "").strip()
        if not seq_id:
            return
        if self._sequence_library is None:
            self._set_autoload_error(
                "sequencer autoload_sequence_id requires a loaded sequence library",
                source=self._sequence_library_path,
            )
            return
        try:
            entry = self._sequence_library.get_entry(seq_id)
            self._set_loaded_sequence_from_library_entry(entry)
            self._publish_lifecycle_event(
                event="load_ok",
                ok=True,
                source="autoload",
                message="sequencer autoloaded library sequence",
                payload={
                    "loaded_source": entry.path,
                    "active_sequence_id": seq_id,
                    "context_columns": self._context_columns,
                },
            )
            self._publish_log(
                severity="info",
                message=f"sequencer autoloaded library sequence: {seq_id}",
            )
        except Exception as e:
            self._set_autoload_error(str(e), source=seq_id)
            self._publish_lifecycle_event(
                event="load_failed",
                ok=False,
                source="autoload",
                message=str(e),
                payload={"active_sequence_id": seq_id},
            )
            self._publish_log(
                severity="error",
                message=f"sequencer autoload sequence_id failed for {seq_id!r}: {e}",
            )

    def _try_autoload_path(self, path: str) -> None:
        try:
            source = str(path)
            seq_text = Path(path).read_text(encoding="utf-8")
            ok, spec, diagnostics = self._load_sequence_text(text=seq_text, source=source)
            if not ok or spec is None:
                first = diagnostics[0] if diagnostics else {}
                message = str(
                    first.get("message", "sequence validation failed during autoload")
                )
                self._set_autoload_error(message, source=source)
                self._publish_lifecycle_event(
                    event="load_failed",
                    ok=False,
                    source="autoload",
                    message=message,
                    payload={"diagnostics": diagnostics, "loaded_source": source},
                )
                self._publish_log(severity="error", message=f"sequencer autoload failed: {message}")
                return
            self._set_loaded_sequence(
                spec=spec,
                text=seq_text,
                source=source,
                source_kind="autoload_path",
                active_sequence_id=None,
            )
            self._publish_lifecycle_event(
                event="load_ok",
                ok=True,
                source="autoload",
                message="sequencer autoloaded sequence",
                payload={
                    "loaded_source": source,
                    "context_columns": self._context_columns,
                },
            )
            self._publish_log(severity="info", message=f"sequencer autoloaded sequence: {source}")
        except Exception as e:
            self._set_autoload_error(str(e), source=str(path))
            self._publish_lifecycle_event(
                event="load_failed",
                ok=False,
                source="autoload",
                message=str(e),
                payload={"loaded_source": str(path)},
            )
            self._publish_log(
                severity="error",
                message=f"sequencer autoload read failed for {path!r}: {e}",
            )

    def _set_autoload_error(self, message: str, *, source: str | None) -> None:
        self._autoload_error = str(message)
        self._autoload_error_ts_wall = time.time()
        self._autoload_error_source = str(source) if source else None

    def _clear_autoload_error(self) -> None:
        self._autoload_error = None
        self._autoload_error_ts_wall = None
        self._autoload_error_source = None

    def _library_list_payload(self) -> Json:
        entries = self._sequence_library.list_entries() if self._sequence_library else []
        return {
            "configured": bool(self._sequence_library_path),
            "manifest_path": self._sequence_library_path,
            "description_policy": self._library_description_policy,
            "active_sequence_id": self._active_sequence_id,
            "autoload_sequence_id": self._autoload_sequence_id,
            "entry_count": len(entries),
            "warnings": list(self._sequence_library_warnings),
            "last_error": self._sequence_library_error,
            "entries": entries,
        }

    @staticmethod
    def _progress_event_signature(status: Json) -> tuple[Any, ...]:
        progress = status.get("progress")
        if not isinstance(progress, dict):
            progress = {}
        return (
            status.get("run_id"),
            status.get("state"),
            status.get("current_step"),
            progress.get("completed_steps"),
            progress.get("total_steps"),
            progress.get("percent"),
            progress.get("eta_s"),
            progress.get("loop_mode"),
            progress.get("loops_completed"),
            progress.get("loops_target"),
        )

    def _maybe_publish_progress_event(self) -> None:
        if self._manager is None:
            return
        status = self._runtime.status()
        signature = self._progress_event_signature(status)
        now = time.monotonic()
        state = str(status.get("state") or "")
        force = state in {"STOPPED", "ERROR"} and signature != self._last_progress_event_signature
        if not force:
            if signature == self._last_progress_event_signature:
                return
            if (now - self._last_progress_event_mono) < self._progress_event_period_s:
                return
        payload = {
            "version": 1,
            "process_id": self._process_id,
            "run_id": status.get("run_id"),
            "state": status.get("state"),
            "current_step": status.get("current_step"),
            "loop_mode": status.get("loop_mode"),
            "loops_completed": status.get("loops_completed"),
            "loops_target": status.get("loops_target"),
            "progress": status.get("progress"),
        }
        try:
            self._manager.publish_event(
                topic="sequencer.progress",
                payload=payload,
                include_process_id=False,
                include_ts=True,
            )
        except Exception:
            return
        self._last_progress_event_signature = signature
        self._last_progress_event_mono = now

    def _load_sequence_text(
        self, *, text: str, source: str
    ) -> tuple[bool, Any | None, list[Json]]:
        diagnostics: list[Json] = []
        try:
            raw = load_yaml_text(text, source=source)
        except Exception as e:
            line = getattr(e, "line", None)
            column = getattr(e, "column", None)
            diagnostics.append(
                {
                    "severity": "error",
                    "message": str(e),
                    "line": int(line) if isinstance(line, int) else None,
                    "column": int(column) if isinstance(column, int) else None,
                    "source": "yaml",
                }
            )
            return False, None, diagnostics

        try:
            spec = parse_sequence(raw)
        except Exception as e:
            diagnostics.append(
                {
                    "severity": "error",
                    "message": str(e),
                    "line": None,
                    "column": None,
                    "source": "sequencer",
                }
            )
            return False, None, diagnostics

        condition_diagnostics = validate_sequence_conditions(spec)
        diagnostics.extend(condition_diagnostics)
        if has_error_diagnostics(condition_diagnostics):
            return False, None, diagnostics

        return True, spec, diagnostics

    @staticmethod
    def _preflight_diag(
        *,
        severity: str,
        path: str,
        message: str,
        code: str | None = None,
        details: Json | None = None,
    ) -> Json:
        item: Json = {
            "severity": str(severity or "warning").lower(),
            "path": path,
            "message": message,
            "source": "sequencer.preflight",
            "line": None,
            "column": None,
        }
        if code:
            item["code"] = code
        if isinstance(details, dict) and details:
            item["details"] = details
        return item

    @staticmethod
    def _preflight_has_errors(diagnostics: list[Json]) -> bool:
        return any(str(item.get("severity", "")).lower() == "error" for item in diagnostics)

    @staticmethod
    def _preflight_summary(diagnostics: list[Json]) -> Json:
        summary: Json = {"errors": 0, "warnings": 0, "infos": 0}
        for item in diagnostics:
            sev = str(item.get("severity", "")).strip().lower()
            if sev == "error":
                summary["errors"] = int(summary["errors"]) + 1
            elif sev == "warning":
                summary["warnings"] = int(summary["warnings"]) + 1
            else:
                summary["infos"] = int(summary["infos"]) + 1
        return summary

    @staticmethod
    def _preflight_is_template_text(value: Any) -> bool:
        return isinstance(value, str) and "${" in value

    @staticmethod
    def _preflight_member_name_from_params(raw_params: Any) -> str | None:
        if not isinstance(raw_params, dict):
            return None
        raw_name = raw_params.get("name")
        if SequencerProcess._preflight_is_template_text(raw_name):
            return None
        if not isinstance(raw_name, str):
            return None
        name = raw_name.strip()
        return name if name else None

    @staticmethod
    def _preflight_parse_capabilities(result: Any) -> dict[str, Json] | None:
        if not isinstance(result, dict):
            return None
        members_raw = result.get("members")
        if not isinstance(members_raw, list):
            return None
        members: dict[str, Json] = {}
        for item in members_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if name:
                members[name] = item
        return members

    @staticmethod
    def _preflight_render(
        *,
        value: Any,
        env: dict[str, Any],
        path: str,
        diagnostics: list[Json],
    ) -> Any:
        try:
            return render_templates(value, env)
        except Exception as e:
            diagnostics.append(
                SequencerProcess._preflight_diag(
                    severity="error",
                    path=path,
                    code="template_unresolved",
                    message=f"template render failed during preflight: {e}",
                )
            )
            return value

    @staticmethod
    def _preflight_load_devices(resp: Any) -> set[str]:
        out: set[str] = set()
        if not isinstance(resp, dict):
            return out
        devices = resp.get("devices")
        if not isinstance(devices, list):
            return out
        for item in devices:
            if not isinstance(item, dict):
                continue
            device_id = str(item.get("device_id", "")).strip()
            if device_id:
                out.add(device_id)
        return out

    @staticmethod
    def _preflight_load_telemetry_signals(resp: Any) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        if not isinstance(resp, dict):
            return out
        result = resp.get("result")
        if not isinstance(result, dict):
            return out
        devices = result.get("devices")
        if not isinstance(devices, list):
            return out
        for item in devices:
            if not isinstance(item, dict):
                continue
            device_id = str(item.get("device_id", "")).strip()
            if not device_id:
                continue
            signals: set[str] = set()
            raw_signals = item.get("signals")
            if isinstance(raw_signals, list):
                for raw_signal in raw_signals:
                    signal = str(raw_signal).strip()
                    if signal:
                        signals.add(signal)
            out[device_id] = signals
        return out

    @staticmethod
    def _preflight_load_stream_names(resp: Any) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        if not isinstance(resp, dict) or not bool(resp.get("ok", False)):
            return out
        result = resp.get("result")
        if not isinstance(result, list):
            return out
        for item in result:
            if not isinstance(item, dict):
                continue
            device_id = str(item.get("device_id", "")).strip()
            if not device_id:
                continue
            streams = SequencerProcess._preflight_collect_stream_names(
                item.get("stream_calls")
            )
            out[device_id] = streams
        return out

    @staticmethod
    def _preflight_collect_stream_names(stream_calls: Any) -> set[str]:
        streams: set[str] = set()
        if not isinstance(stream_calls, list):
            return streams
        for call in stream_calls:
            if not isinstance(call, dict):
                continue
            outputs = call.get("outputs")
            if not isinstance(outputs, list):
                continue
            for output in outputs:
                if not isinstance(output, dict):
                    continue
                stream = str(output.get("stream", "")).strip()
                if stream:
                    streams.add(stream)
        return streams

    def _preflight_check_member_access(
        self,
        *,
        device_id: str,
        member_name: str,
        path: str,
        mode: str,
        diagnostics: list[Json],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        members = capabilities_by_device.get(device_id)
        if members is None:
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path=path,
                    code="capabilities_unavailable",
                    message=f"could not verify member {member_name!r} on {device_id!r}",
                    details={"device_id": device_id, "member": member_name},
                )
            )
            return
        spec = members.get(member_name)
        if spec is None:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=path,
                    code="unknown_member",
                    message=f"unknown member {member_name!r} on {device_id!r}",
                    details={"device_id": device_id, "member": member_name},
                )
            )
            return
        readable = bool(spec.get("readable"))
        settable = bool(spec.get("settable"))
        if mode == "read" and not readable:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=path,
                    code="member_not_readable",
                    message=f"member {member_name!r} is not readable on {device_id!r}",
                    details={"device_id": device_id, "member": member_name},
                )
            )
        if mode == "write" and not settable:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=path,
                    code="member_not_settable",
                    message=f"member {member_name!r} is not settable on {device_id!r}",
                    details={"device_id": device_id, "member": member_name},
                )
            )

    def _preflight_check_stream_name(
        self,
        *,
        device_id: str,
        stream_name: str,
        path: str,
        diagnostics: list[Json],
        stream_names_by_device: dict[str, set[str]],
    ) -> None:
        streams = stream_names_by_device.get(device_id)
        if streams is None:
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path=path,
                    code="stream_schema_unavailable",
                    message=f"could not verify stream {stream_name!r} on {device_id!r}",
                    details={"device_id": device_id, "stream": stream_name},
                )
            )
            return
        if stream_name not in streams:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=path,
                    code="unknown_stream",
                    message=f"unknown stream {stream_name!r} on {device_id!r}",
                    details={"device_id": device_id, "stream": stream_name},
                )
            )

    def _preflight_check_call_action(
        self,
        *,
        device_id: str,
        action: str,
        path: str,
        diagnostics: list[Json],
        device_ids: set[str],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        if device_id not in device_ids:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=path,
                    code="unknown_device",
                    message=f"unknown device {device_id!r}",
                    details={"device_id": device_id},
                )
            )
            return
        if action in _DRIVER_BUILTIN_ACTIONS:
            return
        members = capabilities_by_device.get(device_id)
        if members is None:
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path=path,
                    code="capabilities_unavailable",
                    message=f"could not verify action {action!r} on {device_id!r}",
                    details={"device_id": device_id, "action": action},
                )
            )
            return
        if action not in members:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=path,
                    code="unknown_action",
                    message=f"unknown action {action!r} on {device_id!r}",
                    details={"device_id": device_id, "action": action},
                )
            )

    def _preflight_scan_value_sources(
        self,
        *,
        value: Any,
        path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        rendered = self._preflight_render(
            value=value,
            env=env,
            path=path,
            diagnostics=diagnostics,
        )
        if isinstance(rendered, list):
            self._preflight_scan_value_sources_list(
                rendered=rendered,
                path=path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
            return
        if not isinstance(rendered, dict):
            return
        self._preflight_scan_value_sources_mapping(
            rendered=rendered,
            path=path,
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )

    def _preflight_scan_value_sources_list(
        self,
        *,
        rendered: list[Any],
        path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        for index, item in enumerate(rendered):
            self._preflight_scan_value_sources(
                value=item,
                path=f"{path}[{index}]",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )

    def _preflight_scan_value_sources_mapping(
        self,
        *,
        rendered: dict[str, Any],
        path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        telemetry_spec = rendered.get("telemetry")
        if telemetry_spec is not None:
            self._preflight_scan_telemetry_source(
                telemetry_spec=telemetry_spec,
                path=path,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
            )

        call_spec = rendered.get("call")
        if call_spec is not None:
            self._preflight_scan_call_source(
                call_spec=call_spec,
                path=path,
                diagnostics=diagnostics,
                device_ids=device_ids,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
        self._preflight_scan_rendered_nested_sources(
            rendered=rendered,
            path=path,
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )

    def _preflight_scan_rendered_nested_sources(
        self,
        *,
        rendered: dict[str, Any],
        path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        for key, nested in rendered.items():
            self._preflight_scan_value_sources(
                value=nested,
                path=f"{path}.{key}",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )

    def _preflight_scan_telemetry_source(
        self,
        *,
        telemetry_spec: Any,
        path: str,
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
    ) -> None:
        if not isinstance(telemetry_spec, dict):
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{path}.telemetry",
                    code="invalid_telemetry_source",
                    message="telemetry source must be a dict",
                )
            )
            return
        device = telemetry_spec.get("device")
        signal = telemetry_spec.get("signal")
        if self._preflight_is_template_text(device) or self._preflight_is_template_text(
            signal
        ):
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path=f"{path}.telemetry",
                    code="dynamic_telemetry_ref_unchecked",
                    message="telemetry device/signal is dynamic and was not checked",
                )
            )
            return
        device_id = str(device or "").strip()
        signal_name = str(signal or "").strip()
        if not device_id or not signal_name:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{path}.telemetry",
                    code="invalid_telemetry_source",
                    message="telemetry source requires non-empty device and signal",
                )
            )
            return
        if device_id not in device_ids:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{path}.telemetry.device",
                    code="unknown_device",
                    message=f"unknown device {device_id!r}",
                    details={"device_id": device_id},
                )
            )
        known_signals = telemetry_signals_by_device.get(device_id)
        if known_signals is None:
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path=f"{path}.telemetry.signal",
                    code="telemetry_schema_unavailable",
                    message=f"could not verify signal {signal_name!r} on {device_id!r}",
                    details={"device_id": device_id, "signal": signal_name},
                )
            )
            return
        if signal_name not in known_signals:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{path}.telemetry.signal",
                    code="unknown_signal",
                    message=f"unknown signal {signal_name!r} on {device_id!r}",
                    details={"device_id": device_id, "signal": signal_name},
                )
            )

    def _preflight_scan_call_source(
        self,
        *,
        call_spec: Any,
        path: str,
        diagnostics: list[Json],
        device_ids: set[str],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        if not isinstance(call_spec, dict):
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{path}.call",
                    code="invalid_call_source",
                    message="call source must be a dict",
                )
            )
            return
        device = call_spec.get("device")
        action = call_spec.get("action")
        if self._preflight_is_template_text(device) or self._preflight_is_template_text(
            action
        ):
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path=f"{path}.call",
                    code="dynamic_action_unchecked",
                    message="call device/action is dynamic and was not checked",
                )
            )
            return
        device_id = str(device or "").strip()
        action_name = str(action or "").strip()
        if not device_id or not action_name:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{path}.call",
                    code="invalid_call_source",
                    message="call source requires non-empty device and action",
                )
            )
            return
        self._preflight_check_call_action(
            device_id=device_id,
            action=action_name,
            path=f"{path}.call.action",
            diagnostics=diagnostics,
            device_ids=device_ids,
            capabilities_by_device=capabilities_by_device,
        )
        member_name = self._preflight_member_name_from_params(call_spec.get("params"))
        if action_name in {"get", "set"}:
            if member_name:
                self._preflight_check_member_access(
                    device_id=device_id,
                    member_name=member_name,
                    path=f"{path}.call.params.name",
                    mode="read" if action_name == "get" else "write",
                    diagnostics=diagnostics,
                    capabilities_by_device=capabilities_by_device,
                )
            else:
                diagnostics.append(
                    self._preflight_diag(
                        severity="warning",
                        path=f"{path}.call.params.name",
                        code="dynamic_member_name_unchecked",
                        message=(
                            "member name for get/set is dynamic or missing and "
                            "was not checked"
                        ),
                    )
                )
        if action_name == "stream.context.set":
            params = call_spec.get("params")
            if isinstance(params, dict):
                stream = params.get("stream")
                if self._preflight_is_template_text(stream):
                    diagnostics.append(
                        self._preflight_diag(
                            severity="warning",
                            path=f"{path}.call.params.stream",
                            code="dynamic_stream_name_unchecked",
                            message="stream name is dynamic and was not checked",
                        )
                    )
                elif isinstance(stream, str) and stream.strip():
                    self._preflight_check_stream_name(
                        device_id=device_id,
                        stream_name=stream.strip(),
                        path=f"{path}.call.params.stream",
                        diagnostics=diagnostics,
                        stream_names_by_device=stream_names_by_device,
                    )

    def _preflight_scan_sources(
        self,
        *,
        value: Any,
        path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        self._preflight_scan_value_sources(
            value=value,
            path=path,
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )

    @staticmethod
    def _preflight_refresh_vars_binding(env: dict[str, Any]) -> None:
        env["vars"] = to_attrdict(
            {
                key: value
                for key, value in env.items()
                if isinstance(key, str) and key != "vars"
            }
        )

    def _preflight_validate_call_step_action(
        self,
        *,
        step: CallStep,
        step_path: str,
        diagnostics: list[Json],
        device_ids: set[str],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        device_id = str(step.device).strip()
        action = str(step.action).strip()
        self._preflight_check_call_action(
            device_id=device_id,
            action=action,
            path=f"{step_path}.call.action",
            diagnostics=diagnostics,
            device_ids=device_ids,
            capabilities_by_device=capabilities_by_device,
        )
        if action in {"get", "set"}:
            member_name = self._preflight_member_name_from_params(step.params)
            if member_name:
                self._preflight_check_member_access(
                    device_id=device_id,
                    member_name=member_name,
                    path=f"{step_path}.call.params.name",
                    mode="read" if action == "get" else "write",
                    diagnostics=diagnostics,
                    capabilities_by_device=capabilities_by_device,
                )
            else:
                diagnostics.append(
                    self._preflight_diag(
                        severity="warning",
                        path=f"{step_path}.call.params.name",
                        code="dynamic_member_name_unchecked",
                        message=(
                            "member name for get/set is dynamic or missing and was not checked"
                        ),
                    )
                )
        if action == "stream.context.set":
            stream = step.params.get("stream")
            if self._preflight_is_template_text(stream):
                diagnostics.append(
                    self._preflight_diag(
                        severity="warning",
                        path=f"{step_path}.call.params.stream",
                        code="dynamic_stream_name_unchecked",
                        message="stream name is dynamic and was not checked",
                    )
                )
            elif isinstance(stream, str) and stream.strip():
                self._preflight_check_stream_name(
                    device_id=device_id,
                    stream_name=stream.strip(),
                    path=f"{step_path}.call.params.stream",
                    diagnostics=diagnostics,
                    stream_names_by_device=stream_names_by_device,
                )

    def _preflight_handle_call_step(
        self,
        *,
        step: CallStep,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        if self._preflight_is_template_text(step.device) or self._preflight_is_template_text(
            step.action
        ):
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path=f"{step_path}.call",
                    code="dynamic_action_unchecked",
                    message="call device/action is dynamic and was not checked",
                )
            )
        else:
            self._preflight_validate_call_step_action(
                step=step,
                step_path=step_path,
                diagnostics=diagnostics,
                device_ids=device_ids,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
        self._preflight_scan_sources(
            value=step.params,
            path=f"{step_path}.call.params",
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )
        if step.save_as:
            env[str(step.save_as)] = {}
        if step.extract:
            env[str(step.save_as) if step.save_as else "value"] = 0
        if isinstance(step.assign, dict):
            for key in step.assign:
                key_name = str(key).strip()
                if key_name:
                    env[key_name] = 0
        self._preflight_refresh_vars_binding(env)

    def _preflight_handle_set_step(
        self,
        *,
        step: SetStep,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        device_id = str(step.device).strip()
        member_name = str(step.name).strip()
        if self._preflight_is_template_text(step.device):
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path=f"{step_path}.set.device",
                    code="dynamic_action_unchecked",
                    message="set device is dynamic and was not checked",
                )
            )
        else:
            if device_id not in device_ids:
                diagnostics.append(
                    self._preflight_diag(
                        severity="error",
                        path=f"{step_path}.set.device",
                        code="unknown_device",
                        message=f"unknown device {device_id!r}",
                        details={"device_id": device_id},
                    )
                )
            else:
                self._preflight_check_member_access(
                    device_id=device_id,
                    member_name=member_name,
                    path=f"{step_path}.set.name",
                    mode="write",
                    diagnostics=diagnostics,
                    capabilities_by_device=capabilities_by_device,
                )
        self._preflight_scan_sources(
            value=step.value,
            path=f"{step_path}.set.value",
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )

    def _preflight_handle_wait_until_step(
        self,
        *,
        step: WaitUntilStep,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        wait_env = dict(env)
        wait_env.setdefault("sample", 0)
        wait_env.setdefault("sample_reduced", 0)
        wait_env.setdefault("samples", [])
        self._preflight_refresh_vars_binding(wait_env)
        self._preflight_scan_sources(
            value=step.raw,
            path=f"{step_path}.wait_until",
            env=wait_env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )
        env.setdefault("sample", 0)
        env.setdefault("sample_reduced", 0)
        env.setdefault("samples", [])
        self._preflight_refresh_vars_binding(env)

    def _preflight_parse_stream_target(
        self,
        *,
        item: Any,
        path: str,
        diagnostics: list[Json],
    ) -> tuple[str, str] | None:
        device_id = ""
        stream_name = ""
        if isinstance(item, dict):
            device_raw = item.get("device")
            stream_raw = item.get("stream")
            if self._preflight_is_template_text(device_raw) or self._preflight_is_template_text(
                stream_raw
            ):
                diagnostics.append(
                    self._preflight_diag(
                        severity="warning",
                        path=path,
                        code="dynamic_stream_name_unchecked",
                        message="stream target is dynamic and was not checked",
                    )
                )
                return None
            device_id = str(device_raw or "").strip()
            stream_name = str(stream_raw or "").strip()
        elif isinstance(item, str):
            if self._preflight_is_template_text(item):
                diagnostics.append(
                    self._preflight_diag(
                        severity="warning",
                        path=path,
                        code="dynamic_stream_name_unchecked",
                        message="stream target is dynamic and was not checked",
                    )
                )
                return None
            raw = item.strip()
            if "." in raw:
                device_id, stream_name = raw.split(".", 1)
            elif "/" in raw:
                device_id, stream_name = raw.split("/", 1)
        if not device_id or not stream_name:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=path,
                    code="invalid_stream_target",
                    message="stream target must include non-empty device and stream",
                )
            )
            return None
        return device_id, stream_name

    def _preflight_handle_set_context_step(
        self,
        *,
        step: SetContextStep,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        streams_rendered = self._preflight_render(
            value=step.streams,
            env=env,
            path=f"{step_path}.set_context.streams",
            diagnostics=diagnostics,
        )
        if isinstance(streams_rendered, list):
            for stream_index, item in enumerate(streams_rendered):
                item_path = f"{step_path}.set_context.streams[{stream_index}]"
                resolved = self._preflight_parse_stream_target(
                    item=item,
                    path=item_path,
                    diagnostics=diagnostics,
                )
                if resolved is None:
                    continue
                device_id, stream_name = resolved
                if device_id not in device_ids:
                    diagnostics.append(
                        self._preflight_diag(
                            severity="error",
                            path=item_path,
                            code="unknown_device",
                            message=f"unknown device {device_id!r}",
                            details={"device_id": device_id},
                        )
                    )
                    continue
                self._preflight_check_stream_name(
                    device_id=device_id,
                    stream_name=stream_name,
                    path=item_path,
                    diagnostics=diagnostics,
                    stream_names_by_device=stream_names_by_device,
                )
        self._preflight_scan_sources(
            value=step.fields,
            path=f"{step_path}.set_context.fields",
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )

    def _preflight_handle_for_step(
        self,
        *,
        step: ForStep,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
        use_stack: tuple[str, ...],
    ) -> None:
        rendered_in = self._preflight_render(
            value=step.in_expr,
            env=env,
            path=f"{step_path}.for.in",
            diagnostics=diagnostics,
        )
        if isinstance(rendered_in, dict):
            raw_gen = rendered_in.get("gen")
            if isinstance(raw_gen, dict):
                try:
                    _ = generate_from_gen(raw_gen, env=env, serpentine_index=None)
                except Exception as e:
                    diagnostics.append(
                        self._preflight_diag(
                            severity="error",
                            path=f"{step_path}.for.in.gen",
                            code="invalid_generator",
                            message=f"invalid generator config: {e}",
                        )
                    )
        nested_env = dict(env)
        for target in step.bind.values():
            target_name = str(target).strip()
            if target_name:
                nested_env[target_name] = 0
        self._preflight_refresh_vars_binding(nested_env)
        self._preflight_steps(
            steps=step.body,
            path=f"{step_path}.for.do",
            env=nested_env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
            use_stack=use_stack,
        )

    def _preflight_handle_assign_step(
        self,
        *,
        step: AssignStep,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
    ) -> None:
        self._preflight_scan_sources(
            value=step.values,
            path=f"{step_path}.assign",
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )
        for key in step.values:
            key_name = str(key).strip()
            if key_name:
                env[key_name] = 0
        self._preflight_refresh_vars_binding(env)

    def _preflight_handle_use_step(
        self,
        *,
        step: UseStep,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
        use_stack: tuple[str, ...],
    ) -> None:
        sequence_id = str(step.sequence_id).strip()
        self._preflight_scan_sources(
            value=step.args or {},
            path=f"{step_path}.use.args",
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )
        if not self._sequence_library_path:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{step_path}.use.id",
                    code="library_not_configured",
                    message="use step requires sequence_library_path",
                )
            )
            return
        if sequence_id in use_stack:
            cycle = " -> ".join([*use_stack, sequence_id])
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{step_path}.use.id",
                    code="use_cycle",
                    message=f"recursive use sequence detected: {cycle}",
                )
            )
            return
        try:
            nested_spec = self._resolve_use_sequence_spec(sequence_id)
        except Exception as e:
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{step_path}.use.id",
                    code="unknown_use_sequence",
                    message=f"cannot resolve use.id {sequence_id!r}: {e}",
                )
            )
            return
        nested_env = dict(env)
        for key, value in dict(nested_spec.vars).items():
            nested_env[str(key)] = value
        self._preflight_refresh_vars_binding(nested_env)
        self._preflight_steps(
            steps=nested_spec.steps,
            path=f"{step_path}.use.do",
            env=nested_env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
            use_stack=(*use_stack, sequence_id),
        )

    def _preflight_handle_adaptive_step(
        self,
        *,
        step: AdaptiveStep,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
        use_stack: tuple[str, ...],
    ) -> None:
        adaptive_env = dict(env)
        for target in step.bind.values():
            target_name = str(target).strip()
            if target_name:
                adaptive_env[target_name] = 0
        self._preflight_refresh_vars_binding(adaptive_env)
        rendered_controller = self._preflight_render(
            value=step.controller,
            env=adaptive_env,
            path=f"{step_path}.adaptive.controller",
            diagnostics=diagnostics,
        )
        rendered_space = self._preflight_render(
            value=step.space,
            env=adaptive_env,
            path=f"{step_path}.adaptive.space",
            diagnostics=diagnostics,
        )
        if not isinstance(rendered_controller, dict):
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{step_path}.adaptive.controller",
                    code="invalid_controller",
                    message="adaptive.controller must render to a dict",
                )
            )
        if not isinstance(rendered_space, dict):
            diagnostics.append(
                self._preflight_diag(
                    severity="error",
                    path=f"{step_path}.adaptive.space",
                    code="invalid_space",
                    message="adaptive.space must render to a dict",
                )
            )
        if isinstance(rendered_controller, dict) and isinstance(rendered_space, dict):
            repeats_raw = step.observe.get("repeats", 1)
            try:
                repeats = int(repeats_raw)
                if repeats <= 0:
                    repeats = 1
            except Exception:
                repeats = 1
            try:
                from ..adaptive import create_adaptive_controller

                _ = create_adaptive_controller(
                    controller_spec=rendered_controller,
                    space=rendered_space,
                    repeats=repeats,
                )
            except Exception as e:
                diagnostics.append(
                    self._preflight_diag(
                        severity="error",
                        path=f"{step_path}.adaptive",
                        code="invalid_adaptive_controller",
                        message=f"adaptive controller setup failed: {e}",
                    )
                )
        self._preflight_scan_sources(
            value=step.observe,
            path=f"{step_path}.adaptive.observe",
            env=adaptive_env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
        )
        self._preflight_steps(
            steps=step.body,
            path=f"{step_path}.adaptive.do",
            env=adaptive_env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
            use_stack=use_stack,
        )

    def _preflight_dispatch_primary_step(
        self,
        *,
        step: Step,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
        use_stack: tuple[str, ...],
    ) -> bool:
        if isinstance(step, CallStep):
            self._preflight_handle_call_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
            return True
        if isinstance(step, SetStep):
            self._preflight_handle_set_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
            return True
        if isinstance(step, WaitUntilStep):
            self._preflight_handle_wait_until_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
            return True
        if isinstance(step, SetContextStep):
            self._preflight_handle_set_context_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
            return True
        if isinstance(step, ForStep):
            self._preflight_handle_for_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            )
            return True
        if isinstance(step, AssignStep):
            self._preflight_handle_assign_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
            return True
        if isinstance(step, UseStep):
            self._preflight_handle_use_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            )
            return True
        if isinstance(step, AdaptiveStep):
            self._preflight_handle_adaptive_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            )
            return True
        return False

    def _preflight_recurse_steps(
        self,
        *,
        steps: list[Step],
        path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
        use_stack: tuple[str, ...],
    ) -> None:
        self._preflight_steps(
            steps=steps,
            path=path,
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
            use_stack=use_stack,
        )

    def _preflight_dispatch_structural_step(
        self,
        *,
        step: Step,
        step_path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
        use_stack: tuple[str, ...],
    ) -> bool:
        if isinstance(step, RepeatStep):
            self._preflight_recurse_steps(
                steps=step.body,
                path=f"{step_path}.repeat.do",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            )
            return True

        if isinstance(step, IfStep):
            self._preflight_scan_sources(
                value=step.condition,
                path=f"{step_path}.if.condition",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
            self._preflight_recurse_steps(
                steps=step.then_steps,
                path=f"{step_path}.if.then",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            )
            self._preflight_recurse_steps(
                steps=step.else_steps or [],
                path=f"{step_path}.if.else",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            )
            return True

        if isinstance(step, WhileStep):
            self._preflight_scan_sources(
                value=step.condition,
                path=f"{step_path}.while.condition",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
            )
            self._preflight_recurse_steps(
                steps=step.body,
                path=f"{step_path}.while.do",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            )
            return True

        if isinstance(step, AtomicStep):
            self._preflight_recurse_steps(
                steps=step.body,
                path=f"{step_path}.atomic.do",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            )
            return True

        if isinstance(step, ParallelStep):
            self._preflight_recurse_steps(
                steps=step.body,
                path=f"{step_path}.parallel.do",
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            )
            return True

        if isinstance(step, (SleepStep, PauseStep)):
            return True
        return False

    def _preflight_steps(
        self,
        *,
        steps: list[Step],
        path: str,
        env: dict[str, Any],
        diagnostics: list[Json],
        device_ids: set[str],
        telemetry_signals_by_device: dict[str, set[str]],
        stream_names_by_device: dict[str, set[str]],
        capabilities_by_device: dict[str, dict[str, Json] | None],
        use_stack: tuple[str, ...],
    ) -> None:
        for index, step in enumerate(steps):
            step_path = f"{path}[{index}]"
            if self._preflight_dispatch_primary_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            ):
                continue
            if self._preflight_dispatch_structural_step(
                step=step,
                step_path=step_path,
                env=env,
                diagnostics=diagnostics,
                device_ids=device_ids,
                telemetry_signals_by_device=telemetry_signals_by_device,
                stream_names_by_device=stream_names_by_device,
                capabilities_by_device=capabilities_by_device,
                use_stack=use_stack,
            ):
                continue

    def _preflight_sequence_spec(self, spec: SequenceSpec) -> list[Json]:
        diagnostics: list[Json] = []

        list_devices_resp = self._manager.call({"type": "manager.devices.list"})
        device_ids = self._preflight_load_devices(list_devices_resp)
        if not device_ids:
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path="sequence",
                    code="device_inventory_unavailable",
                    message="device inventory unavailable; checks may be incomplete",
                )
            )

        telemetry_schema_resp = self._manager.call({"action": "manager.telemetry.schema.list"})
        telemetry_signals_by_device = self._preflight_load_telemetry_signals(
            telemetry_schema_resp
        )
        if not telemetry_signals_by_device:
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path="sequence",
                    code="telemetry_schema_unavailable",
                    message="telemetry schema unavailable; signal checks may be incomplete",
                )
            )

        device_config_resp = self._manager.call({"type": "device.config.list"})
        stream_names_by_device = self._preflight_load_stream_names(device_config_resp)
        if not stream_names_by_device:
            diagnostics.append(
                self._preflight_diag(
                    severity="warning",
                    path="sequence",
                    code="stream_schema_unavailable",
                    message="stream schema unavailable; stream checks may be incomplete",
                )
            )

        capabilities_by_device: dict[str, dict[str, Json] | None] = {}
        for device_id in sorted(device_ids):
            cap_resp = self._call_device(device_id, "capabilities", {})
            if not bool(cap_resp.get("ok", False)):
                capabilities_by_device[device_id] = None
                continue
            capabilities_by_device[device_id] = self._preflight_parse_capabilities(
                cap_resp.get("result")
            )

        env = dict(spec.vars)
        env["vars"] = to_attrdict(dict(spec.vars))
        self._preflight_steps(
            steps=spec.steps,
            path="steps",
            env=env,
            diagnostics=diagnostics,
            device_ids=device_ids,
            telemetry_signals_by_device=telemetry_signals_by_device,
            stream_names_by_device=stream_names_by_device,
            capabilities_by_device=capabilities_by_device,
            use_stack=(),
        )
        return diagnostics

    def _call_device(self, device_id: str, action: str, params: dict[str, Any]) -> Json:
        req = {
            "type": "command",
            "device_id": device_id,
            "action": action,
            "params": params,
        }
        resp = self._manager.call(req)
        if resp is None:
            return {"ok": False, "error": "timeout"}
        if not isinstance(resp, dict):
            return {"ok": False, "error": "bad response"}
        if "ok" in resp:
            return resp
        status = resp.get("status")
        if status == "OK":
            return {"ok": True, "result": resp.get("result")}
        if status == "ERROR":
            return {"ok": False, "error": resp.get("error", "unknown")}
        return resp

    @staticmethod
    def _device_error_text(resp: Json) -> str:
        err = resp.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or "").strip()
            message = str(err.get("message") or "").strip()
            if code and message:
                return f"{code}: {message}"
            if message:
                return message
            if code:
                return code
            return "unknown"
        if err is None:
            return "unknown"
        return str(err)

    @staticmethod
    def _is_transient_context_set_error(error_text: str) -> bool:
        text = str(error_text or "").strip().lower()
        if not text:
            return False
        return any(token in text for token in _STREAM_CONTEXT_SET_TRANSIENT_ERRORS)

    def _set_stream_context(
        self, device_id: str, stream: str, context_id: int, fields: dict[str, Any]
    ) -> None:
        params = {
            "stream": stream,
            "context_id": int(context_id),
            "fields": fields,
        }
        deadline = time.monotonic() + _STREAM_CONTEXT_SET_RETRY_DEADLINE_S
        backoff_s = _STREAM_CONTEXT_SET_INITIAL_BACKOFF_S
        attempts = 0
        while True:
            attempts += 1
            resp = self._call_device(device_id, "stream.context.set", params)
            if bool(resp.get("ok", False)):
                return
            error_text = self._device_error_text(resp)
            if not self._is_transient_context_set_error(error_text):
                raise RuntimeError(
                    f"stream.context.set failed for {device_id}/{stream}: {error_text}"
                )
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "stream.context.set failed for "
                    f"{device_id}/{stream} after {attempts} attempts: {error_text}"
                )
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2.0, _STREAM_CONTEXT_SET_MAX_BACKOFF_S)

    def _get_telemetry(self, device_id: str, signal: str) -> dict[str, Any] | None:
        return self._manager.get_latest(device_id, signal)

    def _sequencer_capability_members(self) -> list[Any]:
        members = [
            method(
                "sequencer.load",
                params=[
                    param("path", required=False, default=None, annotation="str"),
                    param("text", required=False, default=None, annotation="str"),
                ],
                doc="Load sequence YAML (path or text).",
            ),
            method(
                "sequencer.validate",
                params=[
                    param("path", required=False, default=None, annotation="str"),
                    param("text", required=False, default=None, annotation="str"),
                ],
                doc="Validate sequence YAML without loading it.",
            ),
            method(
                "sequencer.preflight",
                params=[
                    param("path", required=False, default=None, annotation="str"),
                    param("text", required=False, default=None, annotation="str"),
                ],
                doc="Run runtime preflight checks on sequence YAML (without loading).",
            ),
            method(
                "sequencer.start",
                params=[
                    param(
                        "sequence_id",
                        required=False,
                        default=None,
                        annotation="str",
                    ),
                    param(
                        "repeat_count",
                        required=False,
                        default=None,
                        annotation="int",
                    ),
                    param(
                        "continuous",
                        required=False,
                        default=False,
                        annotation="bool",
                    ),
                    param(
                        "vars_override",
                        required=False,
                        default=None,
                        annotation="dict",
                    ),
                    param(
                        "adaptive",
                        required=False,
                        default=None,
                        annotation="dict",
                    ),
                ],
                doc="Start the loaded sequence.",
            ),
            method("sequencer.pause", params=None, doc="Pause sequence execution."),
            method("sequencer.resume", params=None, doc="Resume sequence execution."),
            method("sequencer.stop", params=None, doc="Stop sequence execution."),
            method("sequencer.status", params=None, doc="Get sequencer status."),
            method(
                "sequencer.library.list",
                params=None,
                doc="List configured sequence library entries.",
            ),
            method(
                "sequencer.library.reload",
                params=None,
                doc="Reload sequence library manifest and entries.",
            ),
            method(
                "sequencer.library.load",
                params=[
                    param(
                        "sequence_id",
                        required=True,
                        default=None,
                        annotation="str",
                    )
                ],
                doc="Load a sequence from the configured library by id.",
            ),
            method(
                "sequencer.adaptive.status",
                params=None,
                doc="Get saved adaptive study state.",
            ),
            method(
                "sequencer.adaptive.clear",
                params=[
                    param(
                        "study_id",
                        required=True,
                        default=None,
                        annotation="str",
                    )
                ],
                doc="Clear saved adaptive study state for one study id.",
            ),
            method(
                "sequencer.adaptive.clear_all",
                params=None,
                doc="Clear all saved adaptive study state.",
            ),
            method(
                "sequencer.loaded_yaml",
                params=None,
                doc="Get currently loaded sequence YAML text and source.",
            ),
        ]
        return self._with_common_capabilities(members)

    def _rpc_sequencer_capabilities(self, req: Json) -> Json:
        return self._rpc_ok(req, result=capabilities_payload(self._sequencer_capability_members()))

    def _rpc_sequencer_status(self, req: Json) -> Json:
        result = self._runtime.status()
        result["loaded"] = self._runtime.is_loaded
        result["context_columns"] = self._context_columns
        result["loaded_source"] = self._loaded_sequence_source
        result["loaded_source_kind"] = self._loaded_sequence_source_kind
        result["active_sequence_id"] = self._active_sequence_id
        result["sequence_library_configured"] = bool(self._sequence_library_path)
        result["sequence_library_path"] = self._sequence_library_path
        result["sequence_library_error"] = self._sequence_library_error
        result["sequence_library_warnings"] = list(self._sequence_library_warnings)
        result["autoload_error"] = self._autoload_error
        result["autoload_error_ts_wall"] = self._autoload_error_ts_wall
        result["autoload_error_source"] = self._autoload_error_source
        return self._rpc_ok(req, result=result)

    def _rpc_sequencer_adaptive_status(self, req: Json) -> Json:
        return self._rpc_ok(req, result=self._runtime.adaptive_status())

    def _rpc_sequencer_adaptive_clear(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self._rpc_invalid_params(req)
        study_id = str(params.get("study_id", "")).strip()
        if not study_id:
            return self._rpc_err(req, code="missing_study_id")
        return self._rpc_ok(
            req,
            result={
                "cleared": int(self._runtime.clear_adaptive_studies(study_id=study_id)),
                "study_id": study_id,
            },
        )

    def _rpc_sequencer_adaptive_clear_all(self, req: Json) -> Json:
        return self._rpc_ok(
            req, result={"cleared": int(self._runtime.clear_adaptive_studies())}
        )

    def _rpc_sequencer_loaded_yaml(self, req: Json) -> Json:
        return self._rpc_ok(
            req,
            result={
                "loaded": self._runtime.is_loaded,
                "source": self._loaded_sequence_source,
                "source_kind": self._loaded_sequence_source_kind,
                "active_sequence_id": self._active_sequence_id,
                "text": self._loaded_sequence_text,
            },
        )

    def _rpc_sequencer_load(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self._rpc_invalid_params(req)
        path = params.get("path")
        text = params.get("text")
        source = "sequence_yaml"
        if path:
            source = str(path)
            try:
                seq_text = Path(str(path)).read_text(encoding="utf-8")
            except Exception as e:
                self._publish_lifecycle_event(
                    event="load_failed",
                    ok=False,
                    source="rpc",
                    message=str(e),
                    payload={"loaded_source": source},
                )
                return self._rpc_err(req, code="read_failed", message=str(e))
        elif text:
            seq_text = str(text)
        else:
            self._publish_lifecycle_event(
                event="load_failed",
                ok=False,
                source="rpc",
                message="missing_yaml",
                payload={"loaded_source": source},
            )
            return self._rpc_err(req, code="missing_yaml")

        ok, spec, diagnostics = self._load_sequence_text(text=seq_text, source=source)
        if not ok or spec is None:
            first = diagnostics[0] if diagnostics else {}
            message = str(first.get("message", "sequence validation failed"))
            self._publish_lifecycle_event(
                event="load_failed",
                ok=False,
                source="rpc",
                message=message,
                payload={"diagnostics": diagnostics, "loaded_source": source},
            )
            return self._rpc_err(
                req,
                code="invalid_sequence",
                message=message,
                extra={"diagnostics": diagnostics},
            )

        self._set_loaded_sequence(
            spec=spec,
            text=seq_text,
            source=source,
            source_kind="rpc",
            active_sequence_id=None,
        )
        self._publish_lifecycle_event(
            event="load_ok",
            ok=True,
            source="rpc",
            message="sequence loaded",
            payload={
                "loaded_source": source,
                "context_columns": self._context_columns,
            },
        )
        return self._rpc_ok(req, result={"status": "loaded"})

    def _rpc_sequencer_validate(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self._rpc_invalid_params(req)
        path = params.get("path")
        text = params.get("text")
        source = "sequence_yaml"
        if path:
            source = str(path)
            try:
                seq_text = Path(str(path)).read_text(encoding="utf-8")
            except Exception as e:
                return self._rpc_err(req, code="read_failed", message=str(e))
        elif text:
            seq_text = str(text)
        else:
            return self._rpc_err(req, code="missing_yaml")
        ok, _spec, diagnostics = self._load_sequence_text(text=seq_text, source=source)
        return self._rpc_ok(
            req, result={"valid": bool(ok), "diagnostics": diagnostics}
        )

    def _rpc_sequencer_preflight(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self._rpc_invalid_params(req)
        path = params.get("path")
        text = params.get("text")
        source = "sequence_yaml"
        if path:
            source = str(path)
            try:
                seq_text = Path(str(path)).read_text(encoding="utf-8")
            except Exception as e:
                return self._rpc_err(req, code="read_failed", message=str(e))
        elif text:
            seq_text = str(text)
        else:
            return self._rpc_err(req, code="missing_yaml")
        ok, spec, diagnostics = self._load_sequence_text(text=seq_text, source=source)
        all_diagnostics = list(diagnostics)
        if ok and isinstance(spec, SequenceSpec):
            all_diagnostics.extend(self._preflight_sequence_spec(spec))
        valid = bool(ok) and not self._preflight_has_errors(all_diagnostics)
        return self._rpc_ok(
            req,
            result={
                "valid": valid,
                "diagnostics": all_diagnostics,
                "summary": self._preflight_summary(all_diagnostics),
            },
        )

    def _rpc_sequencer_library_list(self, req: Json) -> Json:
        return self._rpc_ok(req, result=self._library_list_payload())

    def _rpc_sequencer_library_reload(self, req: Json) -> Json:
        if not self._sequence_library_path:
            return self._rpc_err(
                req,
                code="library_not_configured",
                message="sequencer sequence_library_path is not configured",
            )
        if not self._load_sequence_library(initial=False):
            return self._rpc_err(
                req,
                code="library_reload_failed",
                message=self._sequence_library_error or "sequence library reload failed",
            )
        return self._rpc_ok(req, result=self._library_list_payload())

    def _rpc_sequencer_library_load(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self._rpc_invalid_params(req)
        sequence_id = str(params.get("sequence_id", "")).strip()
        if not sequence_id:
            return self._rpc_err(req, code="missing_sequence_id")
        if self._sequence_library is None:
            return self._rpc_err(
                req,
                code="library_not_configured",
                message="sequencer sequence library is not configured",
            )
        try:
            entry = self._sequence_library.get_entry(sequence_id)
            self._set_loaded_sequence_from_library_entry(entry)
        except KeyError as e:
            return self._rpc_err(req, code="unknown_sequence_id", message=str(e))
        except Exception as e:
            self._publish_lifecycle_event(
                event="load_failed",
                ok=False,
                source="rpc",
                message=str(e),
                payload={"active_sequence_id": sequence_id},
            )
            return self._rpc_err(req, code="load_failed", message=str(e))
        self._publish_lifecycle_event(
            event="load_ok",
            ok=True,
            source="rpc",
            message="sequence loaded from library",
            payload={
                "active_sequence_id": sequence_id,
                "loaded_source": entry.path,
                "context_columns": self._context_columns,
            },
        )
        return self._rpc_ok(
            req, result={"status": "loaded", "active_sequence_id": sequence_id}
        )

    def _rpc_sequencer_start(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self._rpc_invalid_params(req)
        try:
            adaptive = params.get("adaptive")
            adaptive_overrides = adaptive if isinstance(adaptive, dict) else None
            if adaptive is not None and adaptive_overrides is None:
                raise TypeError("sequencer.start params.adaptive must be a dict")
            sequence_id = params.get("sequence_id")
            sequence_id_text = str(sequence_id).strip() if sequence_id is not None else ""
            if sequence_id_text:
                if self._sequence_library is None:
                    raise RuntimeError(
                        "sequencer.start sequence_id requires configured sequence library"
                    )
                entry = self._sequence_library.get_entry(sequence_id_text)
                self._set_loaded_sequence_from_library_entry(entry)
            repeat_count = params.get("repeat_count")
            continuous_raw = params.get("continuous", False)
            if isinstance(continuous_raw, bool):
                continuous = continuous_raw
            elif continuous_raw is None:
                continuous = False
            else:
                raise TypeError("sequencer.start params.continuous must be a bool")
            vars_override_raw = params.get("vars_override")
            vars_override = vars_override_raw if isinstance(vars_override_raw, dict) else None
            if vars_override_raw is not None and vars_override is None:
                raise TypeError("sequencer.start params.vars_override must be a dict")
            self._runtime.start(
                adaptive=adaptive_overrides,
                repeat_count=repeat_count,
                continuous=continuous,
                vars_override=vars_override,
            )
            self._last_progress_event_signature = None
            self._last_progress_event_mono = 0.0
        except Exception as e:
            self._publish_lifecycle_event(
                event="start",
                ok=False,
                source="rpc",
                message=str(e),
            )
            return self._rpc_err(req, code="start_failed", message=str(e))
        self._publish_lifecycle_event(
            event="start",
            ok=True,
            source="rpc",
            message="sequencer started",
            payload={
                "run_id": self._runtime.status().get("run_id"),
                "active_sequence_id": self._active_sequence_id,
                "loaded_source": self._loaded_sequence_source,
            },
        )
        return self._rpc_ok(req, result={"status": "running"})

    def _rpc_sequencer_pause(self, req: Json) -> Json:
        try:
            self._runtime.request_pause()
        except Exception as e:
            self._publish_lifecycle_event(
                event="pause",
                ok=False,
                source="rpc",
                message=str(e),
            )
            return self._rpc_err(req, code="pause_failed", message=str(e))
        self._publish_lifecycle_event(
            event="pause",
            ok=True,
            source="rpc",
            message="pause requested",
        )
        return self._rpc_ok(req, result={"status": "pause_requested"})

    def _rpc_sequencer_resume(self, req: Json) -> Json:
        try:
            self._runtime.resume()
        except Exception as e:
            self._publish_lifecycle_event(
                event="resume",
                ok=False,
                source="rpc",
                message=str(e),
            )
            return self._rpc_err(req, code="resume_failed", message=str(e))
        self._publish_lifecycle_event(
            event="resume",
            ok=True,
            source="rpc",
            message="sequencer resumed",
        )
        return self._rpc_ok(req, result={"status": "running"})

    def _rpc_sequencer_stop(self, req: Json) -> Json:
        try:
            self._runtime.request_stop()
        except Exception as e:
            self._publish_lifecycle_event(
                event="stop",
                ok=False,
                source="rpc",
                message=str(e),
            )
            return self._rpc_err(req, code="stop_failed", message=str(e))
        self._publish_lifecycle_event(
            event="stop",
            ok=True,
            source="rpc",
            message="stop requested",
        )
        return self._rpc_ok(req, result={"status": "stop_requested"})

    def _build_rpc_registry(self) -> RpcDispatchRegistry:
        handlers = {
            "process.capabilities": self._rpc_sequencer_capabilities,
            "sequencer.status": self._rpc_sequencer_status,
            "sequencer.adaptive.status": self._rpc_sequencer_adaptive_status,
            "sequencer.adaptive.clear": self._rpc_sequencer_adaptive_clear,
            "sequencer.adaptive.clear_all": self._rpc_sequencer_adaptive_clear_all,
            "sequencer.loaded_yaml": self._rpc_sequencer_loaded_yaml,
            "sequencer.load": self._rpc_sequencer_load,
            "sequencer.validate": self._rpc_sequencer_validate,
            "sequencer.preflight": self._rpc_sequencer_preflight,
            "sequencer.library.list": self._rpc_sequencer_library_list,
            "sequencer.library.reload": self._rpc_sequencer_library_reload,
            "sequencer.library.load": self._rpc_sequencer_library_load,
            "sequencer.start": self._rpc_sequencer_start,
            "sequencer.pause": self._rpc_sequencer_pause,
            "sequencer.resume": self._rpc_sequencer_resume,
            "sequencer.stop": self._rpc_sequencer_stop,
        }
        return RpcDispatchRegistry(
            handlers=handlers,
            aliases={
                "sequencer.run": "sequencer.start",
                "sequencer.get_status": "sequencer.status",
                "sequencer.get_loaded_yaml": "sequencer.loaded_yaml",
                "sequencer.library.get": "sequencer.library.load",
                "sequencer.clear_adaptive": "sequencer.adaptive.clear",
                "sequencer.clear_all_adaptive": "sequencer.adaptive.clear_all",
            },
        )

    def _handle_rpc(self, req: Json) -> Json:
        common = self._handle_common_rpc(req)
        if common is not None:
            return common
        if not hasattr(self, "_rpc_registry"):
            self._rpc_registry = self._build_rpc_registry()
        canonical = self._rpc_registry.canonical_action(req.get("type"))
        if canonical:
            req_type = str(req.get("type", ""))
            if canonical != req_type:
                req = dict(req)
                req["type"] = canonical
            dispatched = self._rpc_registry.dispatch(req)
            if dispatched is not None:
                return dispatched
        return self._rpc_unknown(req)

    def _handle_rpc_legacy(self, req: Json) -> Json:
        return self._rpc_unknown(req)

    def run(self) -> None:
        try:
            while True:
                events = self._poll_and_drain(50)
                self._drain_external_fault_logs(events)
                self._drain_analysis_outputs(events)
                self._flush_pending_logs(max_items=8)
                self._runtime.tick()
                self._maybe_publish_progress_event()
                if self._runtime.state == "ERROR" and not self._last_error_sent:
                    err_message = str(
                        self._runtime.status().get("error") or "sequencer error"
                    )
                    self._publish_lifecycle_event(
                        event="error",
                        ok=False,
                        source="runtime",
                        message=err_message,
                    )
                    self._publish_log(
                        severity="error",
                        message=err_message,
                    )
                    self._last_error_sent = True
                if self._runtime.state in {"IDLE", "RUNNING", "PAUSED", "STOPPED"}:
                    self._last_error_sent = False
        finally:
            try:
                self._analysis_sub.close(0)
            except Exception:
                pass
            try:
                self._log_sub.close(0)
            except Exception:
                pass
            self.close()

    def _drain_external_fault_logs(self, events: dict[Any, int]) -> None:
        if not (int(events.get(self._log_sub, 0)) & zmq.POLLIN):
            return
        while True:
            try:
                _topic_b, payload_b = self._log_sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception:
                break
            payload = safe_json_loads(payload_b)
            if not isinstance(payload, dict):
                continue
            if self._runtime.state != "RUNNING":
                continue
            should_fail, reason = _should_trigger_external_sequencer_fault(payload)
            if not should_fail or not reason:
                continue
            self._runtime.fail(reason)
            self._publish_lifecycle_event(
                event="error",
                ok=False,
                source="external_trigger",
                message=reason,
                payload={
                    "topic": payload.get("topic"),
                    "source_kind": payload.get("source_kind"),
                    "source_id": payload.get("source_id"),
                    "process_id": payload.get("process_id"),
                    "device_id": payload.get("device_id"),
                    "severity": payload.get("severity"),
                },
            )
            self._publish_log(
                severity="error",
                message=reason,
            )
            self._last_error_sent = True
            break

    def _drain_analysis_outputs(self, events: dict[Any, int]) -> None:
        if not (int(events.get(self._analysis_sub, 0)) & zmq.POLLIN):
            return
        while True:
            try:
                _topic_b, payload_b = self._analysis_sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception:
                break
            payload = safe_json_loads(payload_b)
            if not isinstance(payload, dict):
                continue
            self._runtime.record_analysis_output(payload)

    def _publish_log(self, *, severity: str, message: str) -> None:
        payload = {
            "version": 1,
            "severity": severity,
            "topic": "sequencer",
            "source_kind": "process",
            "source_id": self._process_id,
            "device_id": None,
            "process_id": self._process_id,
            "message": message,
            "payload_json": json.dumps({"process_id": self._process_id}),
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        if self._try_publish_log_payload(payload):
            return
        normalized_severity = str(severity).strip().lower()
        if normalized_severity in {"error", "critical"}:
            self._emit_stderr_fallback(severity=severity, message=message)
            return
        self._queue_log_payload(payload)

    def _publish_lifecycle_event(
        self,
        *,
        event: str,
        ok: bool,
        source: str,
        message: str | None = None,
        payload: Json | None = None,
    ) -> None:
        if self._manager is None:
            return
        body: Json = {
            "version": 1,
            "process_id": self._process_id,
            "event": str(event),
            "ok": bool(ok),
            "source": str(source),
            "message": str(message or ""),
        }
        if payload is not None:
            body["payload"] = payload
        try:
            self._manager.publish_event(
                topic="sequencer.lifecycle",
                payload=body,
                include_process_id=True,
                include_ts=True,
            )
        except Exception:
            pass

    def _try_publish_log_payload(self, payload: Json, *, timeout_ms: int = 120) -> bool:
        try:
            resp = self._manager.call(
                {"type": "manager.logs.publish", "payload": payload},
                timeout_ms=timeout_ms,
            )
        except Exception:
            return False
        return isinstance(resp, dict) and resp.get("ok") is True

    def _queue_log_payload(self, payload: Json) -> None:
        self._pending_log_payloads.append(payload)

    def _flush_pending_logs(self, *, max_items: int = 8) -> None:
        for _ in range(max(0, int(max_items))):
            if not self._pending_log_payloads:
                return
            payload = self._pending_log_payloads[0]
            if not self._try_publish_log_payload(payload, timeout_ms=80):
                return
            self._pending_log_payloads.popleft()

    @staticmethod
    def _emit_stderr_fallback(*, severity: str, message: str) -> None:
        try:
            sys.stderr.write(f"[sequencer][{severity}] {message}\n")
            sys.stderr.flush()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    sequencer = SequencerProcess(
        manager_rpc=ns.manager_rpc,
        manager_pub=ns.manager_pub,
        process_id=ns.process_id,
        rpc_timeout_ms=ns.rpc_timeout_ms,
        heartbeat_endpoint=ns.heartbeat_endpoint,
        heartbeat_period_s=ns.heartbeat_period_s,
    )
    sequencer.run()


if __name__ == "__main__":
    main()

