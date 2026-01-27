from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..capabilities import capabilities_payload, method, param
from ..sequencer.eval import eval_condition, render_templates, to_attrdict
from ..rules.rules_common import TelemetryBinding, parse_telemetry_bindings, parse_version
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.value_coercion import coerce_float
from ..utils.config_parsing import (
    ConfigError,
    optional_dict,
    require_dict,
    require_list_of_dicts,
    require_str,
)
from ..utils.yaml_helpers import load_yaml_file, load_yaml_text
from .manager_client_helper import ManagerClientHelper
from .process_base import ManagedProcessBase

Json = dict[str, Any]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    device_id: str
    action: str
    telemetry: list[TelemetryBinding]
    condition: Any
    on_block_message: str | None
    on_block_code: str | None
    allow_transform_params: dict[str, Any] | None


@dataclass(frozen=True)
class Ruleset:
    interceptor_id: str
    defaults_max_age_s: float
    rules: list[Rule]


@dataclass
class RulesetEntry:
    ruleset: Ruleset
    enabled: bool
    source: str | None = None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control interlock")
    add_manager_args(p)
    add_process_id_arg(p, default="interlock")
    add_rpc_timeout_arg(p, default_ms=2000)
    add_heartbeat_args(p, default_period_s=1.0)
    p.add_argument("--rules", action="append", default=[])
    p.add_argument("--rules-dir", default=None)
    return p.parse_args(argv)


def _parse_ruleset(raw: Any, *, source: str) -> Ruleset:
    obj = require_dict(raw, path=[])
    parse_version(obj, allow_type=False)
    interceptor_id = require_str(obj.get("interceptor_id"), path=["interceptor_id"])
    defaults = optional_dict(obj.get("defaults"), path=["defaults"])
    defaults_max_age = coerce_float(defaults.get("max_age_s"), default=2.0)
    rules_raw = require_list_of_dicts(obj.get("rules"), path=["rules"])

    rules: list[Rule] = []
    for i, rule_raw in enumerate(rules_raw):
        try:
            name = require_str(rule_raw.get("name"), path=["rules", i, "name"])
            match = require_dict(rule_raw.get("match"), path=["rules", i, "match"])
            device_id = require_str(
                match.get("device_id"), path=["rules", i, "match", "device_id"]
            )
            action = require_str(
                match.get("action"), path=["rules", i, "match", "action"]
            )
            inputs = optional_dict(rule_raw.get("inputs"), path=["rules", i, "inputs"])
            telemetry = parse_telemetry_bindings(
                inputs,
                path=["rules", i, "inputs"],
                default_max_age_s=defaults_max_age,
                require_nonempty=False,
            )

            if "condition" not in rule_raw:
                raise ConfigError(
                    path=f"rules[{i}].condition", message="condition is required"
                )
            condition = rule_raw.get("condition")

            on_block = optional_dict(
                rule_raw.get("on_block"), path=["rules", i, "on_block"]
            )
            message = on_block.get("message")
            if message is not None and not isinstance(message, str):
                message = str(message)
            code = on_block.get("code")
            if code is not None and not isinstance(code, str):
                code = str(code)

            allow_transform_params: dict[str, Any] | None = None
            if "allow_transform" in rule_raw and rule_raw.get("allow_transform") is not None:
                allow_transform = require_dict(
                    rule_raw.get("allow_transform"),
                    path=["rules", i, "allow_transform"],
                )
                if "device_id" in allow_transform or "action" in allow_transform:
                    raise ConfigError(
                        path=f"rules[{i}].allow_transform",
                        message="device_id/action rewrites are not supported",
                    )
                extra_keys = [k for k in allow_transform.keys() if k != "params"]
                if extra_keys:
                    raise ConfigError(
                        path=f"rules[{i}].allow_transform",
                        message="only params are supported in allow_transform",
                    )
                params_raw = allow_transform.get("params")
                if params_raw is None:
                    allow_transform_params = {}
                elif not isinstance(params_raw, dict):
                    raise ConfigError(
                        path=f"rules[{i}].allow_transform.params",
                        message="params must be an object/dict",
                    )
                else:
                    allow_transform_params = params_raw

            rules.append(
                Rule(
                    rule_id=f"r{i}",
                    name=name,
                    device_id=device_id,
                    action=action,
                    telemetry=telemetry,
                    condition=condition,
                    on_block_message=message,
                    on_block_code=code,
                    allow_transform_params=allow_transform_params,
                )
            )
        except ConfigError as e:
            raise ValueError(f"{source}: {e}") from None

    return Ruleset(
        interceptor_id=interceptor_id,
        defaults_max_age_s=defaults_max_age,
        rules=rules,
    )


def _load_ruleset(path: Path) -> Ruleset:
    raw = load_yaml_file(path)
    return _parse_ruleset(raw, source=str(path))


def _load_ruleset_text(text: str, *, source: str) -> Ruleset:
    raw = load_yaml_text(text, source=source)
    return _parse_ruleset(raw, source=source)


def _collect_rulesets(paths: list[Path]) -> list[RulesetEntry]:
    rulesets: list[RulesetEntry] = []
    for path in paths:
        rulesets.append(RulesetEntry(ruleset=_load_ruleset(path), enabled=True, source=str(path)))
    return rulesets


def _collect_routes(rulesets: list[Ruleset]) -> list[Json]:
    routes: list[Json] = []
    seen: set[tuple[str, str]] = set()
    for ruleset in rulesets:
        for rule in ruleset.rules:
            key = (rule.device_id, rule.action)
            if key in seen:
                continue
            seen.add(key)
            routes.append({"device_id": rule.device_id, "action": rule.action})
    return routes


def _rule_matches(rule: Rule, device_id: str, action: str) -> bool:
    if rule.device_id != "*" and rule.device_id != device_id:
        return False
    if rule.action != "*" and rule.action != action:
        return False
    return True


def _telemetry_error(
    *, code: str, message: str, binding: TelemetryBinding, sample: dict[str, Any] | None
) -> Json:
    details: Json = {
        "binding": binding.alias,
        "device": binding.device_id,
        "signal": binding.signal,
        "max_age_s": binding.max_age_s,
    }
    if sample is not None:
        details["value"] = sample.get("value")
        details["quality"] = sample.get("quality")
        details["t_mono"] = sample.get("t_mono")
        details["t_wall"] = sample.get("t_wall")
        details["age_s"] = sample.get("age_s")
    return {
        "code": code,
        "message": message,
        "details": details,
    }


def evaluate_interlock_rule(
    *,
    rule: Rule,
    cmd: Json,
    telemetry_getter: Callable[[str, str], dict[str, Any] | None],
    now_mono: float,
) -> tuple[str, Json | None, Json | None]:
    env: dict[str, Any] = {
        "params": to_attrdict(cmd.get("params", {})),
        "device_id": cmd.get("device_id"),
        "action": cmd.get("action"),
    }

    for binding in rule.telemetry:
        sample = telemetry_getter(binding.device_id, binding.signal)
        if sample is None:
            err = _telemetry_error(
                code="TELEMETRY_MISSING",
                message=f"Telemetry missing for {binding.device_id}.{binding.signal}",
                binding=binding,
                sample=None,
            )
            return "reject", None, err

        quality = str(sample.get("quality", "MISSING"))
        if quality != "OK":
            err = _telemetry_error(
                code="TELEMETRY_NOT_OK",
                message=f"Telemetry not OK for {binding.device_id}.{binding.signal}",
                binding=binding,
                sample=sample,
            )
            return "reject", None, err

        age_s = sample.get("age_s")
        if age_s is None:
            t_mono = sample.get("t_mono")
            if t_mono is not None:
                age_s = now_mono - float(t_mono)
        if age_s is None:
            err = _telemetry_error(
                code="TELEMETRY_MISSING",
                message=f"Telemetry missing timestamp for {binding.device_id}.{binding.signal}",
                binding=binding,
                sample=sample,
            )
            return "reject", None, err
        if age_s > binding.max_age_s:
            err = _telemetry_error(
                code="TELEMETRY_STALE",
                message=f"Telemetry stale for {binding.device_id}.{binding.signal}",
                binding=binding,
                sample=sample,
            )
            err["details"]["age_s"] = float(age_s)
            return "reject", None, err

        env[binding.alias] = to_attrdict(
            {
                "value": sample.get("value"),
                "units": sample.get("units"),
                "quality": quality,
                "t_mono": sample.get("t_mono"),
                "t_wall": sample.get("t_wall"),
                "age_s": float(age_s),
            }
        )

    try:
        cond_ok = eval_condition(rule.condition, env)
    except Exception:
        cond_ok = False

    if not cond_ok:
        message = rule.on_block_message or "Command rejected by interceptor"
        code = rule.on_block_code or "CONDITION_FAILED"
        err = {"code": code, "message": message, "details": {}}
        return "reject", None, err

    if rule.allow_transform_params is not None:
        try:
            rendered = render_templates(rule.allow_transform_params, env)
            if not isinstance(rendered, dict):
                raise TypeError("allow_transform.params must be a dict")
            new_params = dict(cmd.get("params", {}))
            new_params.update(rendered)
        except Exception as e:
            err = {
                "code": "TRANSFORM_ERROR",
                "message": str(e) or "Transform failed",
                "details": {},
            }
            return "reject", None, err
        new_cmd = {
            "device_id": cmd.get("device_id"),
            "action": cmd.get("action"),
            "params": new_params,
        }
        return "transform", new_cmd, None

    return "allow", None, None


class InterlockProcess(ManagedProcessBase):
    def __init__(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        process_id: str,
        rpc_timeout_ms: int,
        heartbeat_endpoint: str | None,
        heartbeat_period_s: float,
        rulesets: list[RulesetEntry],
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
        self._ruleset_entries: dict[str, RulesetEntry] = {}
        self._ruleset_order: list[str] = []
        self._rule_enabled: dict[tuple[str, str], bool] = {}
        for entry in rulesets:
            interceptor_id = entry.ruleset.interceptor_id
            if interceptor_id in self._ruleset_entries:
                raise ValueError(f"Duplicate interceptor_id {interceptor_id!r}")
            self._ruleset_entries[interceptor_id] = entry
            self._ruleset_order.append(interceptor_id)
            self._set_default_rule_states(interceptor_id, entry.ruleset)

        self._init_rpc_router()
        self._manager = self._manager_helper.init_client(
            ctx=self._ctx,
            process_id=self._process_id,
            subscribe_telemetry=True,
        )
        self._init_poller()

        self._advertise_process_rpc()
        self._register_routes()
        self._start_heartbeat_thread(state_provider=lambda: "RUNNING")

    @staticmethod
    def _rule_key(interceptor_id: str, rule_id: str) -> tuple[str, str]:
        return (interceptor_id, rule_id)

    def _drop_rule_states(self, interceptor_id: str) -> None:
        for key in list(self._rule_enabled.keys()):
            if key[0] == interceptor_id:
                self._rule_enabled.pop(key, None)

    def _set_default_rule_states(self, interceptor_id: str, ruleset: Ruleset) -> None:
        self._drop_rule_states(interceptor_id)
        for rule in ruleset.rules:
            self._rule_enabled[self._rule_key(interceptor_id, rule.rule_id)] = True

    def _rule_enabled_state(self, interceptor_id: str, rule_id: str) -> bool:
        return bool(self._rule_enabled.get(self._rule_key(interceptor_id, rule_id), True))

    def _routes_for_enabled_rules(self) -> list[Json]:
        routes: list[Json] = []
        seen: set[tuple[str, str]] = set()
        for entry in self._iter_ruleset_entries(enabled_only=True):
            interceptor_id = entry.ruleset.interceptor_id
            for rule in entry.ruleset.rules:
                if not self._rule_enabled_state(interceptor_id, rule.rule_id):
                    continue
                key = (rule.device_id, rule.action)
                if key in seen:
                    continue
                seen.add(key)
                routes.append({"device_id": rule.device_id, "action": rule.action})
        return routes

    def _rule_status_payload(self, interceptor_id: str, rule: Rule) -> Json:
        return {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "enabled": self._rule_enabled_state(interceptor_id, rule.rule_id),
            "match": {"device_id": rule.device_id, "action": rule.action},
            "telemetry": [
                {
                    "as": binding.alias,
                    "device_id": binding.device_id,
                    "signal": binding.signal,
                    "max_age_s": binding.max_age_s,
                }
                for binding in rule.telemetry
            ],
            "on_block": {
                "code": rule.on_block_code,
                "message": rule.on_block_message,
            },
            "has_allow_transform": rule.allow_transform_params is not None,
        }

    def _iter_ruleset_entries(self, *, enabled_only: bool) -> list[RulesetEntry]:
        entries: list[RulesetEntry] = []
        for interceptor_id in self._ruleset_order:
            entry = self._ruleset_entries.get(interceptor_id)
            if entry is None:
                continue
            if enabled_only and not entry.enabled:
                continue
            entries.append(entry)
        return entries

    def _register_routes(self) -> None:
        routes = self._routes_for_enabled_rules()
        payload = {
            "type": "command_interceptor.register",
            "process_id": self._process_id,
            "routes": routes,
            "replace": True,
        }
        resp = self._manager.call(payload)
        if resp is None:
            raise RuntimeError("Failed to register interlock routes: no response")
        if not isinstance(resp, dict) or not resp.get("ok", False):
            raise RuntimeError(f"Failed to register interlock routes: {resp}")

    def _handle_rpc(self, req: Json) -> Json:
        req_id = req.get("request_id")
        rtype = str(req.get("type", ""))
        common = self._handle_common_rpc(req)
        if common is not None:
            return common
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "invalid_params"},
            }

        if rtype == "process.capabilities":
            members = [
                method("interlock.list", params=None, doc="List loaded interceptors."),
                method(
                    "interlock.status",
                    params=None,
                    doc="List loaded interceptors with per-rule enabled state.",
                ),
                method(
                    "interlock.load",
                    params=[
                        param("path", required=False, default=None, annotation="str"),
                        param("text", required=False, default=None, annotation="str"),
                        param("replace", required=False, default=True, annotation="bool"),
                        param("enable", required=False, default=None, annotation="bool"),
                        param("source", required=False, default=None, annotation="str"),
                    ],
                    doc="Load/replace an interceptor ruleset.",
                ),
                method(
                    "interlock.enable",
                    params=[
                        param("interceptor_id", required=True, default=None, annotation="str"),
                    ],
                    doc="Enable an interceptor ruleset.",
                ),
                method(
                    "interlock.disable",
                    params=[
                        param("interceptor_id", required=True, default=None, annotation="str"),
                    ],
                    doc="Disable an interceptor ruleset.",
                ),
                method(
                    "interlock.enable_rule",
                    params=[
                        param("interceptor_id", required=True, default=None, annotation="str"),
                        param("rule_id", required=True, default=None, annotation="str"),
                    ],
                    doc="Enable one rule inside an interceptor ruleset.",
                ),
                method(
                    "interlock.disable_rule",
                    params=[
                        param("interceptor_id", required=True, default=None, annotation="str"),
                        param("rule_id", required=True, default=None, annotation="str"),
                    ],
                    doc="Disable one rule inside an interceptor ruleset.",
                ),
                method("interlock.enable_all", params=None, doc="Enable all interceptors."),
                method("interlock.disable_all", params=None, doc="Disable all interceptors."),
            ]
            members = self._with_common_capabilities(members)
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": capabilities_payload(members),
            }

        if rtype == "interlock.list":
            items: list[Json] = []
            for entry in self._iter_ruleset_entries(enabled_only=False):
                ruleset = entry.ruleset
                enabled_rule_count = sum(
                    1
                    for rule in ruleset.rules
                    if self._rule_enabled_state(ruleset.interceptor_id, rule.rule_id)
                )
                items.append(
                    {
                        "interceptor_id": ruleset.interceptor_id,
                        "enabled": entry.enabled,
                        "source": entry.source,
                        "rule_count": len(ruleset.rules),
                        "enabled_rule_count": enabled_rule_count,
                        "routes": _collect_routes([ruleset]),
                    }
                )
            return {"request_id": req_id, "ok": True, "result": {"interceptors": items}}

        if rtype == "interlock.status":
            items: list[Json] = []
            for entry in self._iter_ruleset_entries(enabled_only=False):
                ruleset = entry.ruleset
                rules_out = [
                    self._rule_status_payload(ruleset.interceptor_id, rule)
                    for rule in ruleset.rules
                ]
                enabled_rule_count = sum(
                    1 for rule in rules_out if bool(rule.get("enabled"))
                )
                items.append(
                    {
                        "interceptor_id": ruleset.interceptor_id,
                        "enabled": entry.enabled,
                        "source": entry.source,
                        "rule_count": len(ruleset.rules),
                        "enabled_rule_count": enabled_rule_count,
                        "routes": _collect_routes([ruleset]),
                        "rules": rules_out,
                    }
                )
            return {"request_id": req_id, "ok": True, "result": {"interceptors": items}}

        if rtype == "interlock.load":
            path = params.get("path")
            text = params.get("text")
            replace = bool(params.get("replace", True))
            enable_param = params.get("enable")
            if (path is None and text is None) or (path is not None and text is not None):
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_load", "message": "path or text required"},
                }
            try:
                if path is not None:
                    ruleset = _load_ruleset(Path(str(path)).expanduser().resolve())
                    source = str(path)
                else:
                    source = str(params.get("source") or "rpc")
                    ruleset = _load_ruleset_text(str(text), source=source)
            except Exception as e:
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "load_failed", "message": str(e)},
                }
            interceptor_id = ruleset.interceptor_id
            existing = self._ruleset_entries.get(interceptor_id)
            if existing is not None and not replace:
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "interceptor_exists"},
                }
            if enable_param is None:
                enabled = existing.enabled if existing is not None else True
            else:
                enabled = bool(enable_param)
            prev_rule_states = {
                key[1]: value
                for key, value in self._rule_enabled.items()
                if key[0] == interceptor_id
            }
            entry = RulesetEntry(ruleset=ruleset, enabled=enabled, source=source)
            self._ruleset_entries[interceptor_id] = entry
            if existing is None:
                self._ruleset_order.append(interceptor_id)
            self._set_default_rule_states(interceptor_id, ruleset)
            try:
                self._register_routes()
            except Exception as e:
                if existing is None:
                    self._ruleset_entries.pop(interceptor_id, None)
                    self._ruleset_order = [
                        current for current in self._ruleset_order if current != interceptor_id
                    ]
                else:
                    self._ruleset_entries[interceptor_id] = existing
                self._drop_rule_states(interceptor_id)
                for rule_id, state in prev_rule_states.items():
                    self._rule_enabled[self._rule_key(interceptor_id, rule_id)] = state
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "route_update_failed", "message": str(e)},
                }
            return {
                "request_id": req_id,
                "ok": True,
                "result": {"interceptor_id": interceptor_id, "enabled": enabled},
            }

        if rtype in {"interlock.enable", "interlock.disable"}:
            interceptor_id = str(params.get("interceptor_id", ""))
            if not interceptor_id:
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "missing_interceptor_id"},
                }
            entry = self._ruleset_entries.get(interceptor_id)
            if entry is None:
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "unknown_interceptor"},
                }
            prev_enabled = entry.enabled
            entry.enabled = rtype == "interlock.enable"
            try:
                self._register_routes()
            except Exception as e:
                entry.enabled = prev_enabled
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "route_update_failed", "message": str(e)},
                }
            return {
                "request_id": req_id,
                "ok": True,
                "result": {"interceptor_id": interceptor_id, "enabled": entry.enabled},
            }

        if rtype in {"interlock.enable_rule", "interlock.disable_rule"}:
            interceptor_id = str(params.get("interceptor_id", "")).strip()
            rule_id = str(params.get("rule_id", "")).strip()
            if not interceptor_id:
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "missing_interceptor_id"},
                }
            if not rule_id:
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "missing_rule_id"},
                }
            entry = self._ruleset_entries.get(interceptor_id)
            if entry is None:
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "unknown_interceptor"},
                }
            if not any(rule.rule_id == rule_id for rule in entry.ruleset.rules):
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "unknown_rule"},
                }
            enabled = rtype == "interlock.enable_rule"
            key = self._rule_key(interceptor_id, rule_id)
            prev_enabled = self._rule_enabled_state(interceptor_id, rule_id)
            self._rule_enabled[key] = enabled
            try:
                self._register_routes()
            except Exception as e:
                self._rule_enabled[key] = prev_enabled
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "route_update_failed", "message": str(e)},
                }
            return {
                "request_id": req_id,
                "ok": True,
                "result": {
                    "interceptor_id": interceptor_id,
                    "rule_id": rule_id,
                    "enabled": enabled,
                },
            }

        if rtype in {"interlock.enable_all", "interlock.disable_all"}:
            enabled = rtype == "interlock.enable_all"
            prev_enabled = {
                interceptor_id: entry.enabled
                for interceptor_id, entry in self._ruleset_entries.items()
            }
            for entry in self._ruleset_entries.values():
                entry.enabled = enabled
            try:
                self._register_routes()
            except Exception as e:
                for interceptor_id, was_enabled in prev_enabled.items():
                    current = self._ruleset_entries.get(interceptor_id)
                    if current is not None:
                        current.enabled = was_enabled
                return {
                    "request_id": req_id,
                    "ok": False,
                    "error": {"code": "route_update_failed", "message": str(e)},
                }
            return {
                "request_id": req_id,
                "ok": True,
                "result": {"enabled": enabled, "count": len(self._ruleset_entries)},
            }

        if rtype != "command_interceptor.check":
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "unknown_request"},
            }

        command = req.get("command")
        if not isinstance(command, dict):
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "invalid_command"},
            }
        device_id = str(command.get("device_id", ""))
        action = str(command.get("action", ""))
        params = command.get("params", {})
        if not device_id or not action or not isinstance(params, dict):
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "invalid_command"},
            }

        cur_cmd: Json = {"device_id": device_id, "action": action, "params": params}
        modified = False
        last_rule: str | None = None
        last_interceptor: str | None = None

        now_mono = time.monotonic()
        for entry in self._iter_ruleset_entries(enabled_only=True):
            ruleset = entry.ruleset
            for rule in ruleset.rules:
                if not self._rule_enabled_state(ruleset.interceptor_id, rule.rule_id):
                    continue
                if not _rule_matches(rule, device_id, action):
                    continue
                verdict, new_cmd, err = evaluate_interlock_rule(
                    rule=rule,
                    cmd=cur_cmd,
                    telemetry_getter=self._manager.get_latest,
                    now_mono=now_mono,
                )
                if verdict == "reject":
                    return {
                        "request_id": req_id,
                        "ok": True,
                        "allow": False,
                        "interceptor_id": ruleset.interceptor_id,
                        "rule": rule.name,
                        "error": err or {"code": "CONDITION_FAILED"},
                    }
                if verdict == "transform" and new_cmd is not None:
                    cur_cmd = new_cmd
                    modified = True
                    last_rule = rule.name
                    last_interceptor = ruleset.interceptor_id

        resp: Json = {"request_id": req_id, "ok": True, "allow": True}
        if modified:
            resp.update(
                {
                    "command": cur_cmd,
                    "interceptor_id": last_interceptor,
                    "rule": last_rule,
                    "note": "transformed",
                }
            )
        return resp

    def run(self) -> None:
        try:
            while True:
                self._poll_and_drain(50)
        finally:
            self.close()


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    rule_paths: list[Path] = []
    for raw in ns.rules:
        rule_paths.append(Path(str(raw)).expanduser().resolve())
    if ns.rules_dir:
        rules_dir = Path(str(ns.rules_dir)).expanduser().resolve()
        for path in sorted(rules_dir.glob("*.yml")) + sorted(rules_dir.glob("*.yaml")):
            rule_paths.append(path)
    if not rule_paths:
        raise SystemExit("No rules provided (--rules or --rules-dir)")

    rulesets = _collect_rulesets(rule_paths)
    proc = InterlockProcess(
        manager_rpc=ns.manager_rpc,
        manager_pub=ns.manager_pub,
        process_id=ns.process_id,
        rpc_timeout_ms=ns.rpc_timeout_ms,
        heartbeat_endpoint=ns.heartbeat_endpoint,
        heartbeat_period_s=ns.heartbeat_period_s,
        rulesets=rulesets,
    )
    proc.run()


if __name__ == "__main__":
    main()
