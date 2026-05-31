from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..capabilities import capabilities_payload, method, param
from ..rules.rules_common import (
    TelemetryBinding,
    parse_telemetry_bindings,
    parse_version,
)
from ..sequencer.eval import eval_condition, render_templates, to_attrdict
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.config_parsing import (
    ConfigError,
    optional_dict,
    require_dict,
    require_list_of_dicts,
    require_str,
)
from ..utils.rpc_dispatch import RpcDispatchRegistry
from ..utils.value_coercion import coerce_float
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


def _parse_on_block(rule_raw: dict[str, Any], *, rule_index: int) -> tuple[str | None, str | None]:
    on_block = optional_dict(rule_raw.get("on_block"), path=["rules", rule_index, "on_block"])
    message = on_block.get("message")
    if message is not None and not isinstance(message, str):
        message = str(message)
    code = on_block.get("code")
    if code is not None and not isinstance(code, str):
        code = str(code)
    return message, code


def _parse_allow_transform_params(
    rule_raw: dict[str, Any],
    *,
    rule_index: int,
) -> dict[str, Any] | None:
    if "allow_transform" not in rule_raw or rule_raw.get("allow_transform") is None:
        return None
    allow_transform = require_dict(
        rule_raw.get("allow_transform"),
        path=["rules", rule_index, "allow_transform"],
    )
    if "device_id" in allow_transform or "action" in allow_transform:
        raise ConfigError(
            path=f"rules[{rule_index}].allow_transform",
            message="device_id/action rewrites are not supported",
        )
    extra_keys = [k for k in allow_transform.keys() if k != "params"]
    if extra_keys:
        raise ConfigError(
            path=f"rules[{rule_index}].allow_transform",
            message="only params are supported in allow_transform",
        )
    params_raw = allow_transform.get("params")
    if params_raw is None:
        return {}
    if not isinstance(params_raw, dict):
        raise ConfigError(
            path=f"rules[{rule_index}].allow_transform.params",
            message="params must be an object/dict",
        )
    return params_raw


def _parse_interlock_rule(
    rule_raw: dict[str, Any],
    *,
    rule_index: int,
    default_max_age_s: float,
) -> Rule:
    name = require_str(rule_raw.get("name"), path=["rules", rule_index, "name"])
    match = require_dict(rule_raw.get("match"), path=["rules", rule_index, "match"])
    device_id = require_str(
        match.get("device_id"), path=["rules", rule_index, "match", "device_id"]
    )
    action = require_str(match.get("action"), path=["rules", rule_index, "match", "action"])
    inputs = optional_dict(rule_raw.get("inputs"), path=["rules", rule_index, "inputs"])
    telemetry = parse_telemetry_bindings(
        inputs,
        path=["rules", rule_index, "inputs"],
        default_max_age_s=default_max_age_s,
        require_nonempty=False,
    )
    if "condition" not in rule_raw:
        raise ConfigError(
            path=f"rules[{rule_index}].condition", message="condition is required"
        )
    condition = rule_raw.get("condition")
    message, code = _parse_on_block(rule_raw, rule_index=rule_index)
    allow_transform_params = _parse_allow_transform_params(
        rule_raw, rule_index=rule_index
    )
    return Rule(
        rule_id=f"r{rule_index}",
        name=name,
        device_id=device_id,
        action=action,
        telemetry=telemetry,
        condition=condition,
        on_block_message=message,
        on_block_code=code,
        allow_transform_params=allow_transform_params,
    )


def _parse_interlock_rules(
    *,
    rules_raw: list[dict[str, Any]],
    default_max_age_s: float,
    source: str,
) -> list[Rule]:
    rules: list[Rule] = []
    for i, rule_raw in enumerate(rules_raw):
        try:
            rules.append(
                _parse_interlock_rule(
                    rule_raw,
                    rule_index=i,
                    default_max_age_s=default_max_age_s,
                )
            )
        except ConfigError as e:
            raise ValueError(f"{source}: {e}") from None
    return rules


def _parse_ruleset(raw: Any, *, source: str) -> Ruleset:
    obj = require_dict(raw, path=[])
    parse_version(obj, allow_type=False)
    interceptor_id = require_str(obj.get("interceptor_id"), path=["interceptor_id"])
    defaults = optional_dict(obj.get("defaults"), path=["defaults"])
    defaults_max_age = coerce_float(defaults.get("max_age_s"), default=2.0)
    rules_raw = require_list_of_dicts(obj.get("rules"), path=["rules"])
    rules = _parse_interlock_rules(
        rules_raw=rules_raw,
        default_max_age_s=defaults_max_age,
        source=source,
    )
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


def collect_rulesets(paths: list[Path]) -> list[RulesetEntry]:
    rulesets: list[RulesetEntry] = []
    for path in paths:
        rulesets.append(RulesetEntry(ruleset=_load_ruleset(path), enabled=True, source=str(path)))
    return rulesets


def resolve_rule_paths(
    *,
    rules: str | Path | list[str | Path] | None = None,
    rules_dir: str | Path | None = None,
) -> list[Path]:
    """Resolve `rules` / `rules_dir` kwargs into a flat ordered list of Paths.

    Mirrors the CLI's `--rules` / `--rules-dir` semantics: each `rules` entry
    is expanduser+resolve'd; `rules_dir` is globbed for ``*.yml`` and ``*.yaml``
    in sorted order. Either, both, or neither may be provided.

    Returns an empty list when neither is given; callers decide whether that is
    an error in their context.
    """
    paths: list[Path] = []
    if rules is not None:
        items: list[str | Path]
        if isinstance(rules, (str, Path)):
            items = [rules]
        else:
            items = list(rules)
        for raw in items:
            paths.append(Path(str(raw)).expanduser().resolve())
    if rules_dir is not None:
        rules_dir_path = Path(str(rules_dir)).expanduser().resolve()
        for path in sorted(rules_dir_path.glob("*.yml")) + sorted(rules_dir_path.glob("*.yaml")):
            paths.append(path)
    return paths


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


def _resolve_binding_age_s(sample: dict[str, Any], *, now_mono: float) -> float | None:
    age_s = sample.get("age_s")
    if age_s is not None:
        try:
            return float(age_s)
        except Exception:
            return None
    t_mono = sample.get("t_mono")
    if t_mono is None:
        return None
    try:
        return now_mono - float(t_mono)
    except Exception:
        return None


def _optional_binding_env(
    *,
    binding: TelemetryBinding,
    sample: dict[str, Any] | None,
    ok: bool,
    reason: str,
    age_s: float | None,
) -> Json:
    return {
        "available": sample is not None,
        "ok": bool(ok),
        "value": None if sample is None else sample.get("value"),
        "units": None if sample is None else sample.get("units"),
        "quality": "MISSING" if sample is None else str(sample.get("quality", "MISSING")),
        "t_mono": None if sample is None else sample.get("t_mono"),
        "t_wall": None if sample is None else sample.get("t_wall"),
        "age_s": age_s,
        "reason": reason,
        "device": binding.device_id,
        "signal": binding.signal,
        "max_age_s": binding.max_age_s,
    }


def _resolve_interlock_telemetry_env(
    *,
    rule: Rule,
    telemetry_getter: Callable[[str, str], dict[str, Any] | None],
    now_mono: float,
) -> tuple[dict[str, Any], Json | None]:
    env: dict[str, Any] = {}
    for binding in rule.telemetry:
        sample = telemetry_getter(binding.device_id, binding.signal)
        if sample is None:
            if not binding.required:
                env[binding.alias] = to_attrdict(
                    _optional_binding_env(
                        binding=binding,
                        sample=None,
                        ok=False,
                        reason="missing",
                        age_s=None,
                    )
                )
                continue
            err = _telemetry_error(
                code="TELEMETRY_MISSING",
                message=f"Telemetry missing for {binding.device_id}.{binding.signal}",
                binding=binding,
                sample=None,
            )
            return env, err
        quality = str(sample.get("quality", "MISSING"))
        if quality != "OK":
            if not binding.required:
                env[binding.alias] = to_attrdict(
                    _optional_binding_env(
                        binding=binding,
                        sample=sample,
                        ok=False,
                        reason=f"quality={quality}",
                        age_s=_resolve_binding_age_s(sample, now_mono=now_mono),
                    )
                )
                continue
            err = _telemetry_error(
                code="TELEMETRY_NOT_OK",
                message=f"Telemetry not OK for {binding.device_id}.{binding.signal}",
                binding=binding,
                sample=sample,
            )
            return env, err
        age_s = _resolve_binding_age_s(sample, now_mono=now_mono)
        if age_s is None:
            if not binding.required:
                env[binding.alias] = to_attrdict(
                    _optional_binding_env(
                        binding=binding,
                        sample=sample,
                        ok=False,
                        reason="missing timestamp",
                        age_s=None,
                    )
                )
                continue
            err = _telemetry_error(
                code="TELEMETRY_MISSING",
                message=f"Telemetry missing timestamp for {binding.device_id}.{binding.signal}",
                binding=binding,
                sample=sample,
            )
            return env, err
        if age_s > binding.max_age_s:
            if not binding.required:
                env[binding.alias] = to_attrdict(
                    _optional_binding_env(
                        binding=binding,
                        sample=sample,
                        ok=False,
                        reason=f"stale: age={age_s:.3f}s",
                        age_s=float(age_s),
                    )
                )
                continue
            err = _telemetry_error(
                code="TELEMETRY_STALE",
                message=f"Telemetry stale for {binding.device_id}.{binding.signal}",
                binding=binding,
                sample=sample,
            )
            err["details"]["age_s"] = float(age_s)
            return env, err
        env[binding.alias] = to_attrdict(
            {
                "available": True,
                "ok": True,
                "value": sample.get("value"),
                "units": sample.get("units"),
                "quality": quality,
                "t_mono": sample.get("t_mono"),
                "t_wall": sample.get("t_wall"),
                "age_s": float(age_s),
                "reason": None,
                "device": binding.device_id,
                "signal": binding.signal,
                "max_age_s": binding.max_age_s,
            }
        )
    return env, None


def _evaluate_interlock_condition(rule: Rule, env: dict[str, Any]) -> bool:
    try:
        return bool(eval_condition(rule.condition, env))
    except Exception:
        return False


def _condition_failed_error(rule: Rule) -> Json:
    message = rule.on_block_message or "Command rejected by interceptor"
    code = rule.on_block_code or "CONDITION_FAILED"
    return {"code": code, "message": message, "details": {}}


def _apply_interlock_transform(
    *,
    rule: Rule,
    cmd: Json,
    env: dict[str, Any],
) -> tuple[Json | None, Json | None]:
    if rule.allow_transform_params is None:
        return None, None
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
        return None, err
    new_cmd = {
        "device_id": cmd.get("device_id"),
        "action": cmd.get("action"),
        "params": new_params,
    }
    return new_cmd, None


def _build_interlock_env(cmd: Json) -> dict[str, Any]:
    return {
        "params": to_attrdict(cmd.get("params", {})),
        "device_id": cmd.get("device_id"),
        "action": cmd.get("action"),
    }


def evaluate_interlock_rule(
    *,
    rule: Rule,
    cmd: Json,
    telemetry_getter: Callable[[str, str], dict[str, Any] | None],
    now_mono: float,
) -> tuple[str, Json | None, Json | None]:
    env = _build_interlock_env(cmd)
    telemetry_env, telemetry_err = _resolve_interlock_telemetry_env(
        rule=rule,
        telemetry_getter=telemetry_getter,
        now_mono=now_mono,
    )
    if telemetry_err is not None:
        return "reject", None, telemetry_err
    env.update(telemetry_env)
    cond_ok = _evaluate_interlock_condition(rule, env)
    if not cond_ok:
        return "reject", None, _condition_failed_error(rule)

    new_cmd, transform_err = _apply_interlock_transform(rule=rule, cmd=cmd, env=env)
    if transform_err is not None:
        return "reject", None, transform_err
    if new_cmd is not None:
        return "transform", new_cmd, None

    return "allow", None, None


def _normalize_rulesets_arg(
    *,
    rulesets: list[RulesetEntry] | None,
    rules: str | Path | list[str | Path] | None,
    rules_dir: str | Path | None,
) -> list[RulesetEntry]:
    """Allow callers to supply `rulesets=` (pre-parsed) OR `rules=`/`rules_dir=`.

    The two paths are mutually exclusive so a caller can't accidentally pass
    both and get silent priority confusion.
    """
    if rulesets is not None:
        if rules is not None or rules_dir is not None:
            raise ValueError(
                "pass either rulesets= or rules=/rules_dir=, not both"
            )
        return rulesets
    paths = resolve_rule_paths(rules=rules, rules_dir=rules_dir)
    if not paths:
        raise ValueError(
            "no rules provided: pass rulesets=, rules=, or rules_dir="
        )
    return collect_rulesets(paths)


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
        rulesets: list[RulesetEntry] | None = None,
        rules: str | Path | list[str | Path] | None = None,
        rules_dir: str | Path | None = None,
    ) -> None:
        rulesets = _normalize_rulesets_arg(
            rulesets=rulesets, rules=rules, rules_dir=rules_dir
        )
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
        self._rpc_registry = self._build_rpc_registry()

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
                    "required": binding.required,
                }
                for binding in rule.telemetry
            ],
            "condition": rule.condition,
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
            "type": "manager.interceptors.register",
            "process_id": self._process_id,
            "routes": routes,
            "replace": True,
        }
        resp = self._require_manager().call(payload)
        if resp is None:
            raise RuntimeError("Failed to register interlock routes: no response")
        if not isinstance(resp, dict) or not resp.get("ok", False):
            raise RuntimeError(f"Failed to register interlock routes: {resp}")

    def _graceful_stop(self) -> None:
        # super()._graceful_stop() sets _stop_evt and must always run so the
        # process actually exits, even if the manager round-trip fails.
        try:
            self._unregister_command_interceptor_routes()
        finally:
            super()._graceful_stop()

    def _interlock_capability_members(self) -> list[Json]:
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
        return self._with_common_capabilities(members)

    def _rpc_interlock_capabilities(self, req: Json) -> Json:
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": capabilities_payload(self._interlock_capability_members()),
        }

    def _rpc_interlock_list(self, req: Json) -> Json:
        req_id = req.get("request_id")
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

    def _rpc_interlock_status(self, req: Json) -> Json:
        req_id = req.get("request_id")
        items: list[Json] = []
        for entry in self._iter_ruleset_entries(enabled_only=False):
            ruleset = entry.ruleset
            rules_out = [
                self._rule_status_payload(ruleset.interceptor_id, rule)
                for rule in ruleset.rules
            ]
            enabled_rule_count = sum(1 for rule in rules_out if bool(rule.get("enabled")))
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

    def _rpc_interlock_load_prepare(
        self, req_id: Any, params: dict[str, Any]
    ) -> tuple[Ruleset, str, bool, Any] | Json:
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
        return ruleset, source, replace, enable_param

    def _rpc_interlock_apply_loaded_ruleset(
        self,
        req_id: Any,
        *,
        ruleset: Ruleset,
        source: str,
        replace: bool,
        enable_param: Any,
    ) -> Json:
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

    def _rpc_interlock_load(self, req: Json) -> Json:
        req_id = req.get("request_id")
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "invalid_params"},
            }
        prepared = self._rpc_interlock_load_prepare(req_id, params)
        if isinstance(prepared, dict):
            return prepared
        ruleset, source, replace, enable_param = prepared
        return self._rpc_interlock_apply_loaded_ruleset(
            req_id,
            ruleset=ruleset,
            source=source,
            replace=replace,
            enable_param=enable_param,
        )

    def _rpc_interlock_enable_disable(self, req: Json, *, enable: bool) -> Json:
        req_id = req.get("request_id")
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "invalid_params"},
            }
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
        entry.enabled = enable
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

    def _rpc_interlock_enable(self, req: Json) -> Json:
        return self._rpc_interlock_enable_disable(req, enable=True)

    def _rpc_interlock_disable(self, req: Json) -> Json:
        return self._rpc_interlock_enable_disable(req, enable=False)

    def _rpc_interlock_enable_rule_disable_rule(self, req: Json, *, enable: bool) -> Json:
        req_id = req.get("request_id")
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "invalid_params"},
            }
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
        key = self._rule_key(interceptor_id, rule_id)
        prev_enabled = self._rule_enabled_state(interceptor_id, rule_id)
        self._rule_enabled[key] = enable
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
                "enabled": enable,
            },
        }

    def _rpc_interlock_enable_rule(self, req: Json) -> Json:
        return self._rpc_interlock_enable_rule_disable_rule(req, enable=True)

    def _rpc_interlock_disable_rule(self, req: Json) -> Json:
        return self._rpc_interlock_enable_rule_disable_rule(req, enable=False)

    def _rpc_interlock_enable_all_disable_all(self, req: Json, *, enable: bool) -> Json:
        req_id = req.get("request_id")
        prev_enabled = {
            interceptor_id: entry.enabled
            for interceptor_id, entry in self._ruleset_entries.items()
        }
        for entry in self._ruleset_entries.values():
            entry.enabled = enable
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
            "result": {"enabled": enable, "count": len(self._ruleset_entries)},
        }

    def _rpc_interlock_enable_all(self, req: Json) -> Json:
        return self._rpc_interlock_enable_all_disable_all(req, enable=True)

    def _rpc_interlock_disable_all(self, req: Json) -> Json:
        return self._rpc_interlock_enable_all_disable_all(req, enable=False)

    def _rpc_interlock_check(self, req: Json) -> Json:
        req_id = req.get("request_id")
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
        self._set_phase("interlock_check", f"{device_id}.{action}")
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
                    telemetry_getter=self._require_manager().get_latest,
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

    def _build_rpc_registry(self) -> RpcDispatchRegistry:
        handlers = {
            "process.capabilities": self._rpc_interlock_capabilities,
            "interlock.list": self._rpc_interlock_list,
            "interlock.status": self._rpc_interlock_status,
            "interlock.load": self._rpc_interlock_load,
            "interlock.enable": self._rpc_interlock_enable,
            "interlock.disable": self._rpc_interlock_disable,
            "interlock.enable_rule": self._rpc_interlock_enable_rule,
            "interlock.disable_rule": self._rpc_interlock_disable_rule,
            "interlock.enable_all": self._rpc_interlock_enable_all,
            "interlock.disable_all": self._rpc_interlock_disable_all,
            "command_interceptor.check": self._rpc_interlock_check,
        }
        return RpcDispatchRegistry(
            handlers=handlers,
            aliases={"interlock.get_status": "interlock.status"},
        )

    def _handle_rpc(self, req: Json) -> Json:
        common = self._handle_common_rpc(req)
        if common is not None:
            return common
        if not hasattr(self, "_rpc_registry"):
            self._rpc_registry = self._build_rpc_registry()
        dispatched = self._rpc_registry.dispatch_with_canonical(req)
        if dispatched is not None:
            return dispatched
        return self._rpc_unknown(req)

    def run(self) -> None:
        try:
            while not self._stop_evt.is_set():
                self._set_phase("poll", "timeout_ms=50")
                self._poll_and_drain(50)
                self._set_phase("idle")
                self._mark_progress()
        finally:
            self.close()


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    try:
        proc = InterlockProcess(
            manager_rpc=ns.manager_rpc,
            manager_pub=ns.manager_pub,
            process_id=ns.process_id,
            rpc_timeout_ms=ns.rpc_timeout_ms,
            heartbeat_endpoint=ns.heartbeat_endpoint,
            heartbeat_period_s=ns.heartbeat_period_s,
            rules=list(ns.rules) if ns.rules else None,
            rules_dir=ns.rules_dir,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    proc.run()


if __name__ == "__main__":
    main()

