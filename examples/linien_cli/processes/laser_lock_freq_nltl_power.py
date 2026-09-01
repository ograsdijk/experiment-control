from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import zmq

from experiment_control.capabilities import capabilities_payload, method, param
from experiment_control.processes.manager_client_helper import ManagerClientHelper
from experiment_control.processes.process_base import ManagedProcessBase
from experiment_control.utils.zmq_helpers import safe_json_loads

Json = dict[str, Any]


@dataclass(frozen=True)
class PowerEffect:
    device_id: str
    action: str
    param: str
    channel: int | None


@dataclass(frozen=True)
class PowerRule:
    rule_id: str
    name: str
    device_id: str
    trigger_action: str
    trigger_param: str
    channel: int | None
    csv_path: Path
    freq_col: int
    power_col: int
    effects: list[PowerEffect]
    freqs_hz: list[float]
    powers_dbm: list[float]
    min_freq_hz: float
    max_freq_hz: float
    max_step_hz: float | None
    current_freq_signal: str | None
    telemetry_max_age_s: float


class LaserLockFreqNltlPowerFollower(ManagedProcessBase):
    """Example channel-aware SynthHD frequency guard and NLTL power follower."""

    def __init__(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        process_id: str | None = "laser_lock_freq_nltl_power",
        heartbeat_endpoint: str | None = None,
        heartbeat_period_s: float = 1.0,
        rpc_timeout_ms: int = 2000,
        rules: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            process_id=process_id,
            heartbeat_endpoint=heartbeat_endpoint,
            heartbeat_period_s=heartbeat_period_s,
        )
        self._manager_helper = ManagerClientHelper(
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            rpc_timeout_ms=int(rpc_timeout_ms),
        )
        self._rules_raw = list(rules or [])
        self._rules: list[PowerRule] = []
        self._rule_enabled: dict[str, bool] = {}
        self._command_sub: zmq.Socket | None = None

    def _parse_rules(self) -> list[PowerRule]:
        base_dir = Path(__file__).resolve().parent
        parsed: list[PowerRule] = []
        for idx, raw in enumerate(self._rules_raw):
            if not isinstance(raw, dict):
                raise ValueError(f"rules[{idx}] must be a dict")

            name = str(raw.get("name") or f"rule_{idx}")
            device_id = _require_str(raw.get("device_id"), "device_id", idx)
            trigger_action = _require_str(
                raw.get("trigger_action"), "trigger_action", idx
            )
            trigger_param = str(raw.get("trigger_param") or "freq_hz")
            channel = _coerce_optional_channel(raw.get("channel"), idx, "channel")
            max_step_hz = _coerce_optional_positive_float(
                raw.get("max_step_hz"), idx, "max_step_hz"
            )
            current_freq_signal = _coerce_optional_nonempty_str(
                raw.get("current_freq_signal"), idx, "current_freq_signal"
            )
            if max_step_hz is not None and current_freq_signal is None:
                raise ValueError(
                    f"rules[{idx}].current_freq_signal is required when max_step_hz is set"
                )
            telemetry_max_age_s = _coerce_positive_float(
                raw.get("telemetry_max_age_s", 2.0), idx, "telemetry_max_age_s"
            )

            csv_path = Path(_require_str(raw.get("csv_path"), "csv_path", idx))
            if not csv_path.is_absolute():
                csv_path = (base_dir / csv_path).resolve()
            freq_col = _coerce_col(raw.get("freq_col", 0), idx, "freq_col")
            power_col = _coerce_col(raw.get("power_col", 1), idx, "power_col")

            effects_raw = raw.get("effects", [])
            if not isinstance(effects_raw, list):
                raise ValueError(f"rules[{idx}].effects must be a list")
            effects: list[PowerEffect] = []
            for j, eff_raw in enumerate(effects_raw):
                if not isinstance(eff_raw, dict):
                    raise ValueError(f"rules[{idx}].effects[{j}] must be a dict")
                effects.append(
                    PowerEffect(
                        device_id=str(eff_raw.get("device_id") or device_id),
                        action=_require_str(
                            eff_raw.get("action"),
                            "action",
                            idx,
                            subpath=f"effects[{j}]",
                        ),
                        param=str(eff_raw.get("param") or "power_dbm"),
                        channel=_coerce_optional_channel(
                            eff_raw.get("channel"), idx, f"effects[{j}].channel"
                        ),
                    )
                )

            freqs, powers = _load_table(
                csv_path, freq_col=freq_col, power_col=power_col
            )
            if not freqs:
                raise ValueError(f"rules[{idx}] CSV has no finite data: {csv_path}")

            parsed.append(
                PowerRule(
                    rule_id=f"r{idx}",
                    name=name,
                    device_id=device_id,
                    trigger_action=trigger_action,
                    trigger_param=trigger_param,
                    channel=channel,
                    csv_path=csv_path,
                    freq_col=freq_col,
                    power_col=power_col,
                    effects=effects,
                    freqs_hz=freqs,
                    powers_dbm=powers,
                    min_freq_hz=float(freqs[0]),
                    max_freq_hz=float(freqs[-1]),
                    max_step_hz=max_step_hz,
                    current_freq_signal=current_freq_signal,
                    telemetry_max_age_s=telemetry_max_age_s,
                )
            )
        return parsed

    def _rule_enabled_state(self, rule_id: str) -> bool:
        return bool(self._rule_enabled.get(rule_id, True))

    def _find_rules(
        self, device_id: str, action: str, *, enabled_only: bool = False
    ) -> list[PowerRule]:
        return [
            rule
            for rule in self._rules
            if rule.device_id == device_id
            and rule.trigger_action == action
            and (not enabled_only or self._rule_enabled_state(rule.rule_id))
        ]

    @staticmethod
    def _filter_channel_rules(
        rules: list[PowerRule], params: dict[str, Any]
    ) -> tuple[list[PowerRule], str | None]:
        if not any(rule.channel is not None for rule in rules):
            return rules, None
        raw_channel = params.get("channel")
        if isinstance(raw_channel, bool) or not isinstance(raw_channel, int):
            return [], "channel must be integer 0 (CH1/A) or 1 (CH2/B)"
        if raw_channel not in (0, 1):
            return [], "channel must be integer 0 (CH1/A) or 1 (CH2/B)"
        return [
            rule for rule in rules if rule.channel is None or rule.channel == raw_channel
        ], None

    def _rule_status_payload(self, rule: PowerRule) -> Json:
        return {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "enabled": self._rule_enabled_state(rule.rule_id),
            "device_id": rule.device_id,
            "trigger_action": rule.trigger_action,
            "trigger_param": rule.trigger_param,
            "channel": rule.channel,
            "min_freq_hz": rule.min_freq_hz,
            "max_freq_hz": rule.max_freq_hz,
            "max_step_hz": rule.max_step_hz,
            "current_freq_signal": rule.current_freq_signal,
            "telemetry_max_age_s": rule.telemetry_max_age_s,
            "csv_path": str(rule.csv_path),
            "effects": [
                {
                    "device_id": effect.device_id,
                    "action": effect.action,
                    "param": effect.param,
                    "channel": effect.channel,
                }
                for effect in rule.effects
            ],
        }

    def _routes_snapshot(self) -> list[Json]:
        routes: list[Json] = []
        seen: set[tuple[str, str]] = set()
        for rule in self._rules:
            if not self._rule_enabled_state(rule.rule_id):
                continue
            key = (rule.device_id, rule.trigger_action)
            if key in seen:
                continue
            seen.add(key)
            routes.append({"device_id": rule.device_id, "action": rule.trigger_action})
        return routes

    def _register_routes(self, manager: Any) -> None:
        response = manager.call(
            {
                "type": "manager.interceptors.register",
                "process_id": self._process_id,
                "routes": self._routes_snapshot(),
                "replace": True,
            },
            timeout_ms=self._manager_helper.rpc_timeout_ms,
        )
        if not isinstance(response, dict) or not response.get("ok", False):
            raise RuntimeError(f"failed to register routes: {response}")

    def _graceful_stop(self) -> None:
        try:
            self._unregister_command_interceptor_routes()
        finally:
            super()._graceful_stop()

    def run(self) -> None:
        try:
            self._rules = self._parse_rules()
            self._rule_enabled = {rule.rule_id: True for rule in self._rules}
        except Exception as exc:
            raise RuntimeError(f"invalid rules config: {exc}") from exc

        self._init_rpc_router()
        self._start_heartbeat_thread(state_provider=lambda: "RUNNING")
        manager = self._manager_helper.init_client(
            ctx=self._ctx,
            process_id=self._process_id,
            subscribe_telemetry=True,
        )
        self._manager = manager
        self._advertise_process_rpc()
        self._register_routes(manager)

        sub = self._manager_helper.open_sub(
            ctx=self._ctx,
            topics=["manager.command"],
            rcvtimeo_ms=200,
        )
        self._command_sub = sub
        self._init_poller(include_rpc=True, include_sub=True, extra=[(sub, zmq.POLLIN)])

        try:
            while not self._stop_evt.is_set():
                events = self._poll_and_drain(200)
                if events.get(sub) != zmq.POLLIN:
                    continue
                try:
                    _topic_b, payload_b = sub.recv_multipart(flags=zmq.NOBLOCK)
                except Exception:
                    continue
                payload = safe_json_loads(payload_b)
                if isinstance(payload, dict):
                    self._handle_command(payload, manager)
        finally:
            try:
                sub.close(0)
            except Exception:
                pass
            self.close()

    def _reject(
        self,
        req: Json,
        *,
        rule: PowerRule | None,
        code: str,
        message: str,
        **details: Any,
    ) -> Json:
        payload: Json = {
            "request_id": self._rpc_request_id(req),
            "ok": True,
            "allow": False,
            "interceptor_id": self._process_id,
            "error": {"code": code, "message": message, "details": details},
        }
        if rule is not None:
            payload["rule"] = rule.name
            payload["error"]["details"].update(
                {
                    "rule": rule.name,
                    "device_id": rule.device_id,
                    "trigger_action": rule.trigger_action,
                    "trigger_param": rule.trigger_param,
                    "channel": rule.channel,
                }
            )
        return payload

    def _validate_max_step(
        self, req: Json, *, rule: PowerRule, requested_hz: float
    ) -> Json | None:
        if rule.max_step_hz is None:
            return None
        if self._manager is None or not rule.current_freq_signal:
            return self._reject(
                req,
                rule=rule,
                code="TELEMETRY_UNAVAILABLE",
                message="Telemetry cache unavailable for step guard",
            )
        sample = self._manager.get_latest(rule.device_id, rule.current_freq_signal)
        if not isinstance(sample, dict):
            return self._reject(
                req,
                rule=rule,
                code="TELEMETRY_MISSING",
                message=f"Telemetry missing for {rule.device_id}.{rule.current_freq_signal}",
            )
        if str(sample.get("quality", "MISSING")) != "OK":
            return self._reject(
                req,
                rule=rule,
                code="TELEMETRY_NOT_OK",
                message=f"Telemetry not OK for {rule.device_id}.{rule.current_freq_signal}",
                quality=sample.get("quality"),
            )
        try:
            raw_age = sample.get("age_s")
            age_s = float(raw_age) if raw_age is not None else None
        except Exception:
            age_s = None
        if age_s is None:
            try:
                age_s = time.monotonic() - float(sample.get("t_mono"))
            except Exception:
                age_s = None
        if age_s is None or not math.isfinite(age_s) or age_s > rule.telemetry_max_age_s:
            return self._reject(
                req,
                rule=rule,
                code="TELEMETRY_STALE",
                message=f"Telemetry stale for {rule.device_id}.{rule.current_freq_signal}",
                age_s=age_s,
            )
        try:
            current_hz = float(sample.get("value"))
        except Exception:
            current_hz = math.nan
        if not math.isfinite(current_hz):
            return self._reject(
                req,
                rule=rule,
                code="TELEMETRY_BAD_VALUE",
                message=(
                    f"Telemetry value is not finite for "
                    f"{rule.device_id}.{rule.current_freq_signal}"
                ),
                value=sample.get("value"),
            )
        step_hz = abs(requested_hz - current_hz)
        if step_hz > rule.max_step_hz:
            return self._reject(
                req,
                rule=rule,
                code="FREQ_STEP_TOO_LARGE",
                message=f"Frequency step {step_hz:g} Hz exceeds max {rule.max_step_hz:g} Hz",
                requested_hz=requested_hz,
                current_hz=current_hz,
                step_hz=step_hz,
                max_step_hz=rule.max_step_hz,
                current_freq_signal=rule.current_freq_signal,
            )
        return None

    def _handle_rpc(self, req: Json) -> Json:
        common = self._handle_common_rpc(req)
        if common is not None:
            return common

        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be an object")

        rtype = str(req.get("type", ""))
        if rtype == "process.capabilities":
            members = [
                method(
                    "follower.rules",
                    params=None,
                    doc="List configured frequency-to-power rules and ranges.",
                ),
                method(
                    "follower.enable_rule",
                    params=[param("rule_id", required=True, default=None, annotation="str")],
                    doc="Enable a follower rule by rule_id.",
                ),
                method(
                    "follower.disable_rule",
                    params=[param("rule_id", required=True, default=None, annotation="str")],
                    doc="Disable a follower rule by rule_id.",
                ),
            ]
            return self.rpc_ok(
                req, result=capabilities_payload(self._with_common_capabilities(members))
            )

        if rtype == "follower.rules":
            return self.rpc_ok(
                req, result={"rules": [self._rule_status_payload(rule) for rule in self._rules]}
            )

        if rtype in {"follower.enable_rule", "follower.disable_rule"}:
            rule_id = str(params.get("rule_id", "")).strip()
            if not rule_id:
                return self.rpc_invalid_params(req, message="rule_id is required")
            if not any(rule.rule_id == rule_id for rule in self._rules):
                return self.rpc_err(req, code="unknown_rule")
            if self._manager is None:
                return self.rpc_err(
                    req, code="manager_unavailable", message="Manager not initialized"
                )
            enabled = rtype == "follower.enable_rule"
            previous = self._rule_enabled_state(rule_id)
            self._rule_enabled[rule_id] = enabled
            try:
                self._register_routes(self._manager)
            except Exception as exc:
                self._rule_enabled[rule_id] = previous
                return self.rpc_err(req, code="route_update_failed", message=str(exc))
            return self.rpc_ok(req, result={"rule_id": rule_id, "enabled": enabled})

        if rtype != "command_interceptor.check":
            return self.rpc_unknown(req)

        command = req.get("command")
        if not isinstance(command, dict):
            return self.rpc_err(req, code="invalid_command")
        device_id = str(command.get("device_id", ""))
        action = str(command.get("action", ""))
        cmd_params = command.get("params", {})
        if not device_id or not action or not isinstance(cmd_params, dict):
            return self.rpc_err(req, code="invalid_command")

        rules = self._find_rules(device_id, action, enabled_only=True)
        if not rules:
            return {"request_id": self._rpc_request_id(req), "ok": True, "allow": True}
        rules, channel_error = self._filter_channel_rules(rules, cmd_params)
        if channel_error is not None:
            return self._reject(
                req,
                rule=None,
                code="INVALID_CHANNEL",
                message=channel_error,
                device_id=device_id,
                action=action,
                channel=cmd_params.get("channel"),
            )

        for rule in rules:
            raw_freq = cmd_params.get(rule.trigger_param)
            if raw_freq is None:
                continue
            try:
                freq_hz = float(raw_freq)
            except Exception:
                freq_hz = math.nan
            if not math.isfinite(freq_hz):
                return self._reject(
                    req,
                    rule=rule,
                    code="INVALID_FREQUENCY",
                    message="requested frequency must be finite",
                    requested=raw_freq,
                )
            if freq_hz < rule.min_freq_hz or freq_hz > rule.max_freq_hz:
                return self._reject(
                    req,
                    rule=rule,
                    code="FREQ_OUT_OF_RANGE",
                    message=(
                        f"Frequency {freq_hz:g} Hz out of range for rule {rule.name!r}: "
                        f"[{rule.min_freq_hz:g}, {rule.max_freq_hz:g}] Hz"
                    ),
                    requested_hz=freq_hz,
                    min_hz=rule.min_freq_hz,
                    max_hz=rule.max_freq_hz,
                    csv_path=str(rule.csv_path),
                )
            step_error = self._validate_max_step(req, rule=rule, requested_hz=freq_hz)
            if step_error is not None:
                return step_error

        return {"request_id": self._rpc_request_id(req), "ok": True, "allow": True}

    def _handle_command(self, payload: Json, manager: Any) -> None:
        if not payload.get("ok", False):
            return
        device_id = payload.get("device_id")
        action = payload.get("action")
        if not isinstance(device_id, str) or not isinstance(action, str):
            return

        params = _parse_params(payload.get("params_json"))
        rules = self._find_rules(device_id, action, enabled_only=True)
        rules, channel_error = self._filter_channel_rules(rules, params)
        if channel_error is not None:
            return

        for rule in rules:
            if rule.trigger_param not in params or not rule.effects:
                continue
            try:
                freq_hz = float(params[rule.trigger_param])
            except Exception:
                continue
            if not math.isfinite(freq_hz):
                continue
            power_dbm = _interp_linear(rule.freqs_hz, rule.powers_dbm, freq_hz)
            if power_dbm is None:
                continue
            for effect in rule.effects:
                effect_params: Json = {effect.param: power_dbm}
                if effect.channel is not None:
                    effect_params["channel"] = effect.channel
                command: Json = {
                    "type": "command",
                    "device_id": effect.device_id,
                    "action": effect.action,
                    "params": effect_params,
                }
                if self._process_id:
                    command["caller_process_id"] = self._process_id
                manager.call(command, timeout_ms=self._manager_helper.rpc_timeout_ms)


def _parse_params(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_table(
    path: Path, *, freq_col: int, power_col: int
) -> tuple[list[float], list[float]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    data = np.genfromtxt(path, delimiter=",", dtype=float)
    if data.size == 0:
        return [], []
    if data.ndim == 1:
        data = np.atleast_2d(data)
    if data.shape[1] <= max(freq_col, power_col):
        raise ValueError("CSV missing required columns")
    freqs = data[:, freq_col]
    powers = data[:, power_col]
    mask = np.isfinite(freqs) & np.isfinite(powers)
    freqs = freqs[mask]
    powers = powers[mask]
    if freqs.size == 0:
        return [], []
    order = np.argsort(freqs)
    return freqs[order].tolist(), powers[order].tolist()


def _interp_linear(xs: list[float], ys: list[float], x: float) -> float | None:
    if not xs or not math.isfinite(x) or x < xs[0] or x > xs[-1]:
        return None
    value = float(np.interp(x, np.asarray(xs), np.asarray(ys)))
    return value if math.isfinite(value) else None


def _require_str(
    raw: Any, field: str, rule_idx: int, *, subpath: str | None = None
) -> str:
    if not isinstance(raw, str) or not raw:
        path = f"rules[{rule_idx}]"
        if subpath:
            path += f".{subpath}"
        raise ValueError(f"{path}.{field} must be a non-empty string")
    return raw


def _coerce_col(raw: Any, rule_idx: int, field: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"rules[{rule_idx}].{field} must be an int")
    return raw


def _coerce_optional_channel(raw: Any, rule_idx: int, field: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw not in (0, 1):
        raise ValueError(
            f"rules[{rule_idx}].{field} must be integer 0 (CH1/A) or 1 (CH2/B)"
        )
    return raw


def _coerce_positive_float(raw: Any, rule_idx: int, field: str) -> float:
    try:
        value = float(raw)
    except Exception as exc:
        raise ValueError(f"rules[{rule_idx}].{field} must be a float") from exc
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"rules[{rule_idx}].{field} must be finite and > 0")
    return value


def _coerce_optional_positive_float(
    raw: Any, rule_idx: int, field: str
) -> float | None:
    if raw is None:
        return None
    return _coerce_positive_float(raw, rule_idx, field)


def _coerce_optional_nonempty_str(raw: Any, rule_idx: int, field: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"rules[{rule_idx}].{field} must be a non-empty string")
    return raw
