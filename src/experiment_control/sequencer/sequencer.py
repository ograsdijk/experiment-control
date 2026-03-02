from __future__ import annotations

import argparse
from collections import deque
import json
import sys
import time
from pathlib import Path
from typing import Any

import zmq

from ..capabilities import capabilities_payload, method, param
from ..utils.yaml_helpers import load_yaml_text
from ..utils.zmq_helpers import safe_json_loads
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from .ast import parse_sequence
from .runtime import SequencerRuntime
from ..processes.process_base import ManagedProcessBase

Json = dict[str, Any]
_EXTERNAL_FAULT_SEVERITIES = {"warning", "error", "critical"}


def _normalize_log_severity(raw: Any) -> str:
    severity = str(raw or "").strip().lower()
    if severity in {"warn", "warning"}:
        return "warning"
    return severity


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
        )
        self._context_columns: dict[str, str] | None = None
        self._loaded_sequence_source: str | None = None
        self._loaded_sequence_text: str | None = None
        self._autoload_error: str | None = None
        self._autoload_error_ts_wall: float | None = None
        self._autoload_error_source: str | None = None
        self._pending_log_payloads: deque[Json] = deque(maxlen=200)

        self._advertise_process_rpc()
        self._start_heartbeat_thread(state_provider=lambda: self._runtime.state)
        self._last_error_sent = False
        if autoload_path:
            self._try_autoload_path(str(autoload_path))

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
            self._runtime.load(spec)
            self._context_columns = spec.context_columns
            self._loaded_sequence_source = source
            self._loaded_sequence_text = seq_text
            self._clear_autoload_error()
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

        return True, spec, diagnostics

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

    def _set_stream_context(
        self, device_id: str, stream: str, context_id: int, fields: dict[str, Any]
    ) -> None:
        req = {
            "type": "command",
            "device_id": device_id,
            "action": "stream.context.set",
            "params": {
                "stream": stream,
                "context_id": int(context_id),
                "fields": fields,
            },
        }
        self._manager.call(req)

    def _get_telemetry(self, device_id: str, signal: str) -> dict[str, Any] | None:
        return self._manager.get_latest(device_id, signal)

    def _handle_rpc(self, req: Json) -> Json:
        rtype = str(req.get("type", ""))
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return {
                "request_id": req.get("request_id"),
                "ok": False,
                "error": {"code": "invalid_params"},
            }

        if rtype == "process.capabilities":
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
                    "sequencer.start",
                    params=[
                        param(
                            "adaptive",
                            required=False,
                            default=None,
                            annotation="dict",
                        )
                    ],
                    doc="Start the loaded sequence.",
                ),
                method("sequencer.pause", params=None, doc="Pause sequence execution."),
                method("sequencer.resume", params=None, doc="Resume sequence execution."),
                method("sequencer.stop", params=None, doc="Stop sequence execution."),
                method("sequencer.status", params=None, doc="Get sequencer status."),
                method(
                    "sequencer.adaptive.status",
                    params=None,
                    doc="Get saved adaptive study state.",
                ),
                method(
                    "sequencer.adaptive.clear",
                    params=[param("study_id", required=True, annotation="str")],
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
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": capabilities_payload(members),
            }

        if rtype == "sequencer.load":
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
                    return {
                        "request_id": req.get("request_id"),
                        "ok": False,
                        "error": {"code": "read_failed", "message": str(e)},
                    }
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
                return {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {"code": "missing_yaml"},
                }

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
                return {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {
                        "code": "invalid_sequence",
                        "message": message,
                        "diagnostics": diagnostics,
                    },
                }

            self._runtime.load(spec)
            self._context_columns = spec.context_columns
            self._loaded_sequence_source = source
            self._loaded_sequence_text = seq_text
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
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": {"status": "loaded"},
            }

        if rtype == "sequencer.validate":
            path = params.get("path")
            text = params.get("text")
            source = "sequence_yaml"
            if path:
                source = str(path)
                try:
                    seq_text = Path(str(path)).read_text(encoding="utf-8")
                except Exception as e:
                    return {
                        "request_id": req.get("request_id"),
                        "ok": False,
                        "error": {"code": "read_failed", "message": str(e)},
                    }
            elif text:
                seq_text = str(text)
            else:
                return {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {"code": "missing_yaml"},
                }
            ok, _, diagnostics = self._load_sequence_text(text=seq_text, source=source)
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": {"valid": bool(ok), "diagnostics": diagnostics},
            }

        if rtype == "sequencer.start":
            try:
                adaptive = params.get("adaptive")
                adaptive_overrides = adaptive if isinstance(adaptive, dict) else None
                if adaptive is not None and adaptive_overrides is None:
                    raise TypeError("sequencer.start params.adaptive must be a dict")
                self._runtime.start(adaptive=adaptive_overrides)
            except Exception as e:
                self._publish_lifecycle_event(
                    event="start",
                    ok=False,
                    source="rpc",
                    message=str(e),
                )
                return {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {"code": "start_failed", "message": str(e)},
                }
            self._publish_lifecycle_event(
                event="start",
                ok=True,
                source="rpc",
                message="sequencer started",
            )
            return {"request_id": req.get("request_id"), "ok": True, "result": {"status": "running"}}

        if rtype == "sequencer.pause":
            try:
                self._runtime.request_pause()
            except Exception as e:
                self._publish_lifecycle_event(
                    event="pause",
                    ok=False,
                    source="rpc",
                    message=str(e),
                )
                return {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {"code": "pause_failed", "message": str(e)},
                }
            self._publish_lifecycle_event(
                event="pause",
                ok=True,
                source="rpc",
                message="pause requested",
            )
            return {"request_id": req.get("request_id"), "ok": True, "result": {"status": "pause_requested"}}

        if rtype == "sequencer.resume":
            try:
                self._runtime.resume()
            except Exception as e:
                self._publish_lifecycle_event(
                    event="resume",
                    ok=False,
                    source="rpc",
                    message=str(e),
                )
                return {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {"code": "resume_failed", "message": str(e)},
                }
            self._publish_lifecycle_event(
                event="resume",
                ok=True,
                source="rpc",
                message="sequencer resumed",
            )
            return {"request_id": req.get("request_id"), "ok": True, "result": {"status": "running"}}

        if rtype == "sequencer.stop":
            try:
                self._runtime.request_stop()
            except Exception as e:
                self._publish_lifecycle_event(
                    event="stop",
                    ok=False,
                    source="rpc",
                    message=str(e),
                )
                return {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {"code": "stop_failed", "message": str(e)},
                }
            self._publish_lifecycle_event(
                event="stop",
                ok=True,
                source="rpc",
                message="stop requested",
            )
            return {"request_id": req.get("request_id"), "ok": True, "result": {"status": "stop_requested"}}

        if rtype == "sequencer.status":
            result = self._runtime.status()
            result["loaded"] = self._runtime.is_loaded
            result["context_columns"] = self._context_columns
            result["loaded_source"] = self._loaded_sequence_source
            result["autoload_error"] = self._autoload_error
            result["autoload_error_ts_wall"] = self._autoload_error_ts_wall
            result["autoload_error_source"] = self._autoload_error_source
            return {"request_id": req.get("request_id"), "ok": True, "result": result}

        if rtype == "sequencer.adaptive.status":
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": self._runtime.adaptive_status(),
            }

        if rtype == "sequencer.adaptive.clear":
            study_id = str(params.get("study_id", "")).strip()
            if not study_id:
                return {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {"code": "missing_study_id"},
                }
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": {
                    "cleared": int(self._runtime.clear_adaptive_studies(study_id=study_id)),
                    "study_id": study_id,
                },
            }

        if rtype == "sequencer.adaptive.clear_all":
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": {"cleared": int(self._runtime.clear_adaptive_studies())},
            }

        if rtype == "sequencer.loaded_yaml":
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": {
                    "loaded": self._runtime.is_loaded,
                    "source": self._loaded_sequence_source,
                    "text": self._loaded_sequence_text,
                },
            }

        return {
            "request_id": req.get("request_id"),
            "ok": False,
            "error": {"code": "unknown_request"},
        }

    def run(self) -> None:
        try:
            while True:
                events = self._poll_and_drain(50)
                self._drain_external_fault_logs(events)
                self._drain_analysis_outputs(events)
                self._flush_pending_logs(max_items=8)
                self._runtime.tick()
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
                {"type": "manager.log.publish", "payload": payload},
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
