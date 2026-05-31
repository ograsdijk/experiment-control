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
    parse_on_unknown,
    parse_severity,
    parse_telemetry_bindings,
    parse_version,
)
from ..sequencer.eval import eval_condition, to_attrdict
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.responses import is_response_ok
from ..utils.config_parsing import (
    ConfigError,
    normalize_list,
    optional_dict,
    require_dict,
    require_list_of_dicts,
    require_str,
)
from ..utils.rpc_dispatch import RpcDispatchRegistry
from ..utils.value_coercion import coerce_bool, coerce_float, coerce_int
from ..utils.yaml_helpers import load_yaml_file
from .manager_client_helper import ManagerClientHelper
from .process_base import ManagedProcessBase

Json = dict[str, Any]


@dataclass(frozen=True)
class CommandAction:
    device_id: str
    action: str
    params: dict[str, Any]
    timeout_s: float | None
    retries: int


@dataclass(frozen=True)
class WatchdogArm:
    condition: Any
    disarm_condition: Any | None
    disarm_on_trigger: bool


@dataclass(frozen=True)
class WatchdogRule:
    name: str
    severity: str
    message: str | None
    telemetry: list[TelemetryBinding]
    condition: Any
    stable_for_s: float
    cooldown_s: float
    latch: bool
    on_unknown: str
    actions: list[CommandAction]
    arm: WatchdogArm | None = None


@dataclass(frozen=True)
class WatchdogRuleset:
    watchdog_id: str
    rules: list[WatchdogRule]


@dataclass
class WatchdogEntry:
    ruleset: WatchdogRuleset
    enabled: bool


@dataclass
class RuleState:
    stable_since_mono: float | None = None
    last_trigger_mono: float | None = None
    latched: bool = False
    armed: bool = False
    last_evaluated_mono: float | None = None
    alarm: bool | None = None
    unknown: bool | None = None
    snapshot: Json | None = None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control watchdog")
    add_manager_args(p)
    add_process_id_arg(p, default="watchdog", flags=("--process-id", "--id"))
    add_rpc_timeout_arg(p, default_ms=2000)
    add_heartbeat_args(p, default_period_s=1.0)
    p.add_argument("--rules", action="append", default=[])
    p.add_argument("--rules-dir", default=None)
    p.add_argument("--tick-s", type=float, default=0.5)
    return p.parse_args(argv)


def _parse_watchdog_defaults(
    obj: dict[str, Any],
) -> tuple[float, float, float, bool, str]:
    defaults = optional_dict(obj.get("defaults"), path=["defaults"])
    defaults_max_age = coerce_float(defaults.get("max_age_s"), default=2.0)
    defaults_stable = coerce_float(defaults.get("stable_for_s"), default=0.0)
    defaults_cooldown = coerce_float(defaults.get("cooldown_s"), default=5.0)
    defaults_latch = coerce_bool(defaults.get("latch"), default=False)
    defaults_on_unknown = parse_on_unknown(
        defaults.get("on_unknown"), path=["defaults", "on_unknown"], default="ignore"
    )
    return (
        defaults_max_age,
        defaults_stable,
        defaults_cooldown,
        defaults_latch,
        defaults_on_unknown,
    )


def _parse_watchdog_arm(
    *, rule_raw: dict[str, Any], rule_index: int
) -> WatchdogArm | None:
    raw = rule_raw.get("arm")
    if raw is None:
        return None
    arm = require_dict(raw, path=["rules", rule_index, "arm"])
    if "condition" not in arm:
        raise ConfigError(
            path=f"rules[{rule_index}].arm.condition", message="condition is required"
        )
    disarm_condition = arm.get("disarm_condition")
    disarm_on_trigger = coerce_bool(arm.get("disarm_on_trigger"), default=False)
    return WatchdogArm(
        condition=arm.get("condition"),
        disarm_condition=disarm_condition,
        disarm_on_trigger=disarm_on_trigger,
    )


def _parse_watchdog_actions(
    *,
    rule_raw: dict[str, Any],
    rule_index: int,
) -> list[CommandAction]:
    actions_raw = normalize_list(rule_raw.get("actions"), path=["rules", rule_index, "actions"])
    if not actions_raw:
        raise ConfigError(path=f"rules[{rule_index}].actions", message="actions are required")
    actions: list[CommandAction] = []
    for action_index, action_raw in enumerate(actions_raw):
        if not isinstance(action_raw, dict):
            raise ConfigError(
                path=f"rules[{rule_index}].actions[{action_index}]",
                message="must be an object/dict",
            )
        if "command" not in action_raw:
            raise ConfigError(
                path=f"rules[{rule_index}].actions[{action_index}]",
                message="only command actions are supported",
            )
        cmd = require_dict(
            action_raw.get("command"),
            path=["rules", rule_index, "actions", action_index, "command"],
        )
        device_id = require_str(
            cmd.get("device_id"),
            path=["rules", rule_index, "actions", action_index, "command", "device_id"],
        )
        action = require_str(
            cmd.get("action"),
            path=["rules", rule_index, "actions", action_index, "command", "action"],
        )
        params = cmd.get("params", {}) or {}
        if not isinstance(params, dict):
            raise ConfigError(
                path=f"rules[{rule_index}].actions[{action_index}].command.params",
                message="params must be a dict",
            )
        timeout_s = cmd.get("timeout_s")
        timeout_s_val = None if timeout_s is None else float(timeout_s)
        retries = coerce_int(cmd.get("retries"), default=0)
        if retries < 0:
            retries = 0
        actions.append(
            CommandAction(
                device_id=device_id,
                action=action,
                params=params,
                timeout_s=timeout_s_val,
                retries=retries,
            )
        )
    return actions


def _parse_watchdog_rule(
    *,
    rule_raw: dict[str, Any],
    rule_index: int,
    watchdog_id: str,
    seen_rules: set[str],
    defaults_max_age: float,
    defaults_stable: float,
    defaults_cooldown: float,
    defaults_latch: bool,
    defaults_on_unknown: str,
) -> WatchdogRule:
    name = require_str(rule_raw.get("name"), path=["rules", rule_index, "name"])
    if name in seen_rules:
        raise ConfigError(
            path=f"rules[{rule_index}].name",
            message=f"duplicate rule {name!r} in watchdog {watchdog_id!r}",
        )
    seen_rules.add(name)
    severity = parse_severity(rule_raw.get("severity"), path=["rules", rule_index, "severity"])
    message = rule_raw.get("message")
    if message is not None and not isinstance(message, str):
        message = str(message)
    inputs = optional_dict(rule_raw.get("inputs"), path=["rules", rule_index, "inputs"])
    telemetry = parse_telemetry_bindings(
        inputs,
        path=["rules", rule_index, "inputs"],
        default_max_age_s=defaults_max_age,
        require_nonempty=True,
    )
    if "condition" not in rule_raw:
        raise ConfigError(path=f"rules[{rule_index}].condition", message="condition is required")
    condition = rule_raw.get("condition")
    stable_for_s = coerce_float(rule_raw.get("stable_for_s"), default=defaults_stable)
    cooldown_s = coerce_float(rule_raw.get("cooldown_s"), default=defaults_cooldown)
    latch = coerce_bool(rule_raw.get("latch"), default=defaults_latch)
    on_unknown = parse_on_unknown(
        rule_raw.get("on_unknown"),
        path=["rules", rule_index, "on_unknown"],
        default=defaults_on_unknown,
    )
    arm = _parse_watchdog_arm(rule_raw=rule_raw, rule_index=rule_index)
    actions = _parse_watchdog_actions(rule_raw=rule_raw, rule_index=rule_index)
    return WatchdogRule(
        name=name,
        severity=severity,
        message=message,
        telemetry=telemetry,
        condition=condition,
        stable_for_s=max(0.0, float(stable_for_s)),
        cooldown_s=max(0.0, float(cooldown_s)),
        latch=latch,
        on_unknown=on_unknown,
        actions=actions,
        arm=arm,
    )


def _parse_watchdog_rules(
    *,
    rules_raw: list[dict[str, Any]],
    watchdog_id: str,
    defaults_max_age: float,
    defaults_stable: float,
    defaults_cooldown: float,
    defaults_latch: bool,
    defaults_on_unknown: str,
    source: str,
) -> list[WatchdogRule]:
    seen_rules: set[str] = set()
    rules: list[WatchdogRule] = []
    for i, rule_raw in enumerate(rules_raw):
        try:
            rules.append(
                _parse_watchdog_rule(
                    rule_raw=rule_raw,
                    rule_index=i,
                    watchdog_id=watchdog_id,
                    seen_rules=seen_rules,
                    defaults_max_age=defaults_max_age,
                    defaults_stable=defaults_stable,
                    defaults_cooldown=defaults_cooldown,
                    defaults_latch=defaults_latch,
                    defaults_on_unknown=defaults_on_unknown,
                )
            )
        except ConfigError as e:
            raise ValueError(f"{source}: {e}") from None
    return rules


def _parse_ruleset(raw: Any, *, source: str) -> WatchdogRuleset:
    obj = require_dict(raw, path=[])
    parse_version(obj, allow_type=False)
    watchdog_id = require_str(obj.get("watchdog_id"), path=["watchdog_id"])
    (
        defaults_max_age,
        defaults_stable,
        defaults_cooldown,
        defaults_latch,
        defaults_on_unknown,
    ) = _parse_watchdog_defaults(obj)

    rules_raw = require_list_of_dicts(obj.get("rules"), path=["rules"])
    rules = _parse_watchdog_rules(
        rules_raw=rules_raw,
        watchdog_id=watchdog_id,
        defaults_max_age=defaults_max_age,
        defaults_stable=defaults_stable,
        defaults_cooldown=defaults_cooldown,
        defaults_latch=defaults_latch,
        defaults_on_unknown=defaults_on_unknown,
        source=source,
    )
    return WatchdogRuleset(watchdog_id=watchdog_id, rules=rules)


def _load_ruleset(path: Path) -> WatchdogRuleset:
    raw = load_yaml_file(path)
    return _parse_ruleset(raw, source=str(path))


def collect_rulesets(paths: list[Path]) -> list[WatchdogRuleset]:
    rulesets: list[WatchdogRuleset] = []
    for path in paths:
        rulesets.append(_load_ruleset(path))
    return rulesets


def resolve_rule_paths(
    *,
    rules: str | Path | list[str | Path] | None = None,
    rules_dir: str | Path | None = None,
) -> list[Path]:
    """Same semantics as ``experiment_control.processes.interlock.resolve_rule_paths``.

    Each `rules` entry is expanduser+resolve'd; `rules_dir` is globbed for
    ``*.yml`` and ``*.yaml`` in sorted order. Returns an empty list if neither
    is provided.
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


def _normalize_rulesets_arg(
    *,
    rulesets: list[WatchdogRuleset] | None,
    rules: str | Path | list[str | Path] | None,
    rules_dir: str | Path | None,
) -> list[WatchdogRuleset]:
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


# Backwards-compat alias for the canonical predicate. New code should import
# is_response_ok from experiment_control.utils.responses directly.
_resp_ok = is_response_ok


def _resolve_watchdog_bindings(
    rule: WatchdogRule,
    *,
    telemetry_getter: Callable[[str, str], dict[str, Any] | None],
    now_mono: float,
) -> tuple[Json, Json, bool]:
    env: Json = {"params": to_attrdict({})}
    snapshot: Json = {}
    unknown = False
    for binding in rule.telemetry:
        entry, alias_env, is_unknown = _resolve_watchdog_binding(
            binding=binding,
            telemetry_getter=telemetry_getter,
            now_mono=now_mono,
        )
        if alias_env is not None:
            env[binding.alias] = alias_env
        if is_unknown:
            unknown = True
        snapshot[binding.alias] = entry
    return env, snapshot, unknown


def _resolve_watchdog_binding_age_s(sample: dict[str, Any], *, now_mono: float) -> float | None:
    age_s = sample.get("age_s")
    if age_s is not None:
        return float(age_s)
    t_mono = sample.get("t_mono")
    if t_mono is None:
        return None
    return now_mono - float(t_mono)


def _resolve_watchdog_binding(
    *,
    binding: TelemetryBinding,
    telemetry_getter: Callable[[str, str], dict[str, Any] | None],
    now_mono: float,
) -> tuple[Json, Json | None, bool]:
    sample = telemetry_getter(binding.device_id, binding.signal)
    entry: Json = {
        "device": binding.device_id,
        "signal": binding.signal,
        "max_age_s": binding.max_age_s,
    }
    if sample is None:
        entry.update({"value": None, "quality": "MISSING", "age_s": None, "ok": False})
        return entry, None, True

    age_s = _resolve_watchdog_binding_age_s(sample, now_mono=now_mono)
    quality = sample.get("quality")
    entry.update(
        {
            "value": sample.get("value"),
            "quality": quality,
            "age_s": age_s,
        }
    )
    ok = quality == "OK" and age_s is not None and age_s <= binding.max_age_s
    entry["ok"] = ok
    if not ok:
        return entry, None, True
    alias_env: Json = to_attrdict(
        {
            "value": sample.get("value"),
            "age_s": age_s,
            "quality": quality,
            "device": binding.device_id,
            "signal": binding.signal,
        }
    )
    return entry, alias_env, False


def _evaluate_watchdog_alarm(*, rule: WatchdogRule, env: Json, unknown: bool) -> bool:
    if unknown:
        return rule.on_unknown == "trigger"
    try:
        return bool(eval_condition(rule.condition, env))
    except Exception:
        return False


def _evaluate_watchdog_condition(condition: Any, env: Json, *, unknown: bool) -> bool:
    if unknown:
        return False
    try:
        return bool(eval_condition(condition, env))
    except Exception:
        return False


def _update_watchdog_armed_state(
    *, rule: WatchdogRule, state: RuleState, env: Json, unknown: bool
) -> None:
    if rule.arm is None:
        return
    if rule.arm.disarm_condition is not None and _evaluate_watchdog_condition(
        rule.arm.disarm_condition, env, unknown=unknown
    ):
        state.armed = False
    if _evaluate_watchdog_condition(rule.arm.condition, env, unknown=unknown):
        state.armed = True


def _watchdog_stable_ready(*, rule: WatchdogRule, state: RuleState, now_mono: float) -> bool:
    if rule.stable_for_s <= 0:
        return True
    if state.stable_since_mono is None:
        state.stable_since_mono = now_mono
    return (now_mono - state.stable_since_mono) >= rule.stable_for_s


def _watchdog_cooldown_ready(*, rule: WatchdogRule, state: RuleState, now_mono: float) -> bool:
    if state.last_trigger_mono is None:
        return True
    return (now_mono - state.last_trigger_mono) >= rule.cooldown_s


def evaluate_watchdog_rule(
    *,
    rule: WatchdogRule,
    state: RuleState,
    telemetry_getter: Callable[[str, str], dict[str, Any] | None],
    now_mono: float,
) -> tuple[bool, bool, bool, Json]:
    env, snapshot, unknown = _resolve_watchdog_bindings(
        rule, telemetry_getter=telemetry_getter, now_mono=now_mono
    )
    _update_watchdog_armed_state(rule=rule, state=state, env=env, unknown=unknown)
    alarm = _evaluate_watchdog_alarm(rule=rule, env=env, unknown=unknown)
    state.last_evaluated_mono = now_mono
    state.alarm = alarm
    state.unknown = unknown
    state.snapshot = snapshot

    if rule.arm is not None and not state.armed:
        state.stable_since_mono = None
        return False, alarm, unknown, snapshot

    if not alarm:
        state.stable_since_mono = None
        return False, alarm, unknown, snapshot

    if rule.latch and state.latched:
        return False, alarm, unknown, snapshot

    if not _watchdog_stable_ready(rule=rule, state=state, now_mono=now_mono):
        return False, alarm, unknown, snapshot

    if not _watchdog_cooldown_ready(rule=rule, state=state, now_mono=now_mono):
        return False, alarm, unknown, snapshot

    state.last_trigger_mono = now_mono
    if rule.latch:
        state.latched = True
    if rule.arm is not None and rule.arm.disarm_on_trigger:
        state.armed = False
    return True, alarm, unknown, snapshot


class WatchdogProcess(ManagedProcessBase):
    def __init__(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        process_id: str,
        rpc_timeout_ms: int,
        heartbeat_endpoint: str | None,
        heartbeat_period_s: float,
        tick_s: float,
        rulesets: list[WatchdogRuleset] | None = None,
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
        self._tick_s = max(0.05, float(tick_s))

        self._watchdog_entries: dict[str, WatchdogEntry] = {}
        self._ruleset_order: list[str] = []
        for ruleset in rulesets:
            if ruleset.watchdog_id in self._watchdog_entries:
                raise ValueError(f"Duplicate watchdog_id {ruleset.watchdog_id!r}")
            self._watchdog_entries[ruleset.watchdog_id] = WatchdogEntry(
                ruleset=ruleset, enabled=True
            )
            self._ruleset_order.append(ruleset.watchdog_id)

        self._states: dict[tuple[str, str], RuleState] = {}
        for ruleset in rulesets:
            for rule in ruleset.rules:
                self._states[(ruleset.watchdog_id, rule.name)] = RuleState()

        self._init_rpc_router()
        self._manager = self._manager_helper.init_client(
            ctx=self._ctx,
            process_id=self._process_id,
            subscribe_telemetry=True,
        )
        self._init_poller()

        self._advertise_process_rpc()
        self._start_heartbeat_thread(state_provider=lambda: "RUNNING")
        self._publish_rules_loaded()
        self._rpc_registry = self._build_rpc_registry()

    def _publish_event(self, topic: str, payload: Json) -> None:
        self._manager_helper.publish_event(self._manager, topic=topic, payload=payload)

    def _publish_rules_loaded(self) -> None:
        rules: list[Json] = []
        watchdog_ids: list[str] = []
        for watchdog_id in self._ruleset_order:
            ruleset = self._watchdog_entries[watchdog_id].ruleset
            watchdog_ids.append(watchdog_id)
            for rule in ruleset.rules:
                rules.append(
                    {
                        "watchdog_id": watchdog_id,
                        "rule": rule.name,
                        "severity": rule.severity,
                    }
                )
        payload = {
            "process_id": self._process_id,
            "watchdog_ids": watchdog_ids,
            "rules": rules,
        }
        self._publish_event("manager.watchdog.rules_loaded", payload)

    def _publish_triggered(
        self,
        *,
        watchdog_id: str,
        rule: WatchdogRule,
        unknown: bool,
        snapshot: Json,
        state: RuleState,
    ) -> None:
        payload = {
            "process_id": self._process_id,
            "watchdog_id": watchdog_id,
            "rule": rule.name,
            "severity": rule.severity,
            "message": rule.message
            or f"Watchdog {watchdog_id}:{rule.name} triggered",
            "alarm": True,
            "unknown": unknown,
            "snapshot": snapshot,
            "timing": {
                "stable_for_s": rule.stable_for_s,
                "cooldown_s": rule.cooldown_s,
                "latched": rule.latch,
                "armed": state.armed,
            },
        }
        self._publish_event("manager.watchdog.triggered", payload)

    def _publish_action_sent(
        self,
        *,
        watchdog_id: str,
        rule: WatchdogRule,
        command: Json,
        attempt: int,
        retries: int,
    ) -> None:
        payload = {
            "process_id": self._process_id,
            "watchdog_id": watchdog_id,
            "rule": rule.name,
            "command": command,
            "attempt": attempt,
            "retries": retries,
        }
        self._publish_event("manager.watchdog.action_sent", payload)

    def _publish_action_failed(
        self,
        *,
        watchdog_id: str,
        rule: WatchdogRule,
        command: Json,
        attempt: int,
        retries: int,
        error: Any,
    ) -> None:
        payload = {
            "process_id": self._process_id,
            "watchdog_id": watchdog_id,
            "rule": rule.name,
            "command": command,
            "attempt": attempt,
            "retries": retries,
            "error": error,
        }
        self._publish_event("manager.watchdog.action_failed", payload)

    def _execute_actions(
        self, *, watchdog_id: str, rule: WatchdogRule
    ) -> None:
        for action in rule.actions:
            total_attempts = 1 + max(0, int(action.retries))
            command_payload = {
                "device_id": action.device_id,
                "action": action.action,
                "params": action.params,
            }
            for attempt in range(1, total_attempts + 1):
                self._publish_action_sent(
                    watchdog_id=watchdog_id,
                    rule=rule,
                    command=command_payload,
                    attempt=attempt,
                    retries=action.retries,
                )
                req = {
                    "type": "command",
                    "device_id": action.device_id,
                    "action": action.action,
                    "params": action.params,
                    "caller_process_id": self._process_id,
                }
                timeout_ms = None
                if action.timeout_s is not None:
                    timeout_ms = max(1, int(float(action.timeout_s) * 1000))
                resp = self._require_manager().call(req, timeout_ms=timeout_ms)
                if resp is not None and _resp_ok(resp):
                    break
                error = resp if resp is not None else "timeout"
                self._publish_action_failed(
                    watchdog_id=watchdog_id,
                    rule=rule,
                    command=command_payload,
                    attempt=attempt,
                    retries=action.retries,
                    error=error,
                )

    def _evaluate_rules(self) -> None:
        now_mono = time.monotonic()
        for watchdog_id in self._ruleset_order:
            entry = self._watchdog_entries[watchdog_id]
            if not entry.enabled:
                continue
            ruleset = entry.ruleset
            for rule in ruleset.rules:
                state = self._states[(watchdog_id, rule.name)]
                triggered, _alarm, unknown, snapshot = evaluate_watchdog_rule(
                    rule=rule,
                    state=state,
                    telemetry_getter=self._require_manager().get_latest,
                    now_mono=now_mono,
                )
                if triggered:
                    self._publish_triggered(
                        watchdog_id=watchdog_id,
                        rule=rule,
                        unknown=unknown,
                        snapshot=snapshot,
                        state=state,
                    )
                    self._execute_actions(watchdog_id=watchdog_id, rule=rule)

    def _clear_rule_state(self, watchdog_id: str, rule_name: str) -> Json:
        state = self._states.get((watchdog_id, rule_name))
        if state is None:
            return {"ok": False, "error": "rule not found"}
        prev_latched = bool(state.latched)
        prev_armed = bool(state.armed)
        state.latched = False
        state.armed = False
        state.stable_since_mono = None
        payload = {
            "process_id": self._process_id,
            "watchdog_id": watchdog_id,
            "rule": rule_name,
            "previous_latched": prev_latched,
            "previous_armed": prev_armed,
        }
        self._publish_event("manager.watchdog.cleared", payload)
        return {"ok": True, "previous_latched": prev_latched, "previous_armed": prev_armed}

    def _watchdog_capability_members(self) -> list[Json]:
        members = [
            method("watchdog.status", params=None, doc="Get watchdog status."),
            method(
                "watchdog.clear_latch",
                params=[
                    param("watchdog_id", required=False, default=None, annotation="str"),
                    param("rule", required=False, default=None, annotation="str"),
                    param("all", required=False, default=False, annotation="bool"),
                ],
                doc="Clear latch for a rule or all rules.",
            ),
            method(
                "watchdog.enable",
                params=[
                    param("watchdog_id", required=True, default=None, annotation="str"),
                ],
                doc="Enable a watchdog ruleset.",
            ),
            method(
                "watchdog.disable",
                params=[
                    param("watchdog_id", required=True, default=None, annotation="str"),
                ],
                doc="Disable a watchdog ruleset.",
            ),
            method("watchdog.enable_all", params=None, doc="Enable all watchdogs."),
            method("watchdog.disable_all", params=None, doc="Disable all watchdogs."),
        ]
        return self._with_common_capabilities(members)

    def _rpc_watchdog_capabilities(self, req: Json) -> Json:
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": capabilities_payload(self._watchdog_capability_members()),
        }

    def _rpc_watchdog_status(self, req: Json) -> Json:
        watchdogs: list[Json] = []
        for watchdog_id in self._ruleset_order:
            entry = self._watchdog_entries[watchdog_id]
            ruleset = entry.ruleset
            rules_out: list[Json] = []
            for rule in ruleset.rules:
                state = self._states[(watchdog_id, rule.name)]
                rules_out.append(
                    {
                        "name": rule.name,
                        "severity": rule.severity,
                        "message": rule.message,
                        "condition": rule.condition,
                        "arm": None if rule.arm is None else {
                            "condition": rule.arm.condition,
                            "disarm_condition": rule.arm.disarm_condition,
                            "disarm_on_trigger": rule.arm.disarm_on_trigger,
                        },
                        "telemetry": [
                            {
                                "as": binding.alias,
                                "device_id": binding.device_id,
                                "signal": binding.signal,
                                "max_age_s": binding.max_age_s,
                            }
                            for binding in rule.telemetry
                        ],
                        "actions": [
                            {
                                "device_id": action.device_id,
                                "action": action.action,
                                "params": action.params,
                                "timeout_s": action.timeout_s,
                                "retries": action.retries,
                            }
                            for action in rule.actions
                        ],
                        "stable_for_s": rule.stable_for_s,
                        "cooldown_s": rule.cooldown_s,
                        "latch": rule.latch,
                        "on_unknown": rule.on_unknown,
                        "latched": state.latched,
                        "armed": state.armed,
                        "alarm": state.alarm,
                        "unknown": state.unknown,
                        "snapshot": state.snapshot,
                        "last_evaluated_mono": state.last_evaluated_mono,
                        "stable_since_mono": state.stable_since_mono,
                        "last_trigger_mono": state.last_trigger_mono,
                    }
                )
            watchdogs.append(
                {
                    "watchdog_id": watchdog_id,
                    "enabled": entry.enabled,
                    "rules": rules_out,
                }
            )
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": {"watchdogs": watchdogs},
        }

    def _rpc_watchdog_enable_disable(self, req: Json, *, enable: bool) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return {
                "request_id": req.get("request_id"),
                "ok": False,
                "error": {"code": "invalid_params"},
            }
        watchdog_id = params.get("watchdog_id")
        if not watchdog_id:
            return {
                "request_id": req.get("request_id"),
                "ok": False,
                "error": {"code": "missing_watchdog_id"},
            }
        watchdog_id_text = str(watchdog_id)
        entry = self._watchdog_entries.get(watchdog_id_text)
        if entry is None:
            return {
                "request_id": req.get("request_id"),
                "ok": False,
                "error": {"code": "unknown_watchdog"},
            }
        entry.enabled = enable
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": {"watchdog_id": watchdog_id_text, "enabled": entry.enabled},
        }

    def _rpc_watchdog_enable(self, req: Json) -> Json:
        return self._rpc_watchdog_enable_disable(req, enable=True)

    def _rpc_watchdog_disable(self, req: Json) -> Json:
        return self._rpc_watchdog_enable_disable(req, enable=False)

    def _rpc_watchdog_enable_all_disable_all(self, req: Json, *, enable: bool) -> Json:
        for entry in self._watchdog_entries.values():
            entry.enabled = enable
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": {"enabled": enable, "count": len(self._watchdog_entries)},
        }

    def _rpc_watchdog_enable_all(self, req: Json) -> Json:
        return self._rpc_watchdog_enable_all_disable_all(req, enable=True)

    def _rpc_watchdog_disable_all(self, req: Json) -> Json:
        return self._rpc_watchdog_enable_all_disable_all(req, enable=False)

    def _rpc_watchdog_clear_all_latches(self, req_id: Any) -> Json:
        cleared: list[Json] = []
        for watchdog_id in self._ruleset_order:
            ruleset = self._watchdog_entries[watchdog_id].ruleset
            for rule in ruleset.rules:
                result = self._clear_rule_state(watchdog_id, rule.name)
                if result.get("ok"):
                    cleared.append(
                        {
                            "watchdog_id": watchdog_id,
                            "rule": rule.name,
                            "previous_latched": result.get("previous_latched"),
                            "previous_armed": result.get("previous_armed"),
                        }
                    )
        return {"request_id": req_id, "ok": True, "result": {"cleared": cleared}}

    def _rpc_watchdog_clear_scoped(
        self, req_id: Any, *, watchdog_id: str, rule_name: str
    ) -> Json:
        if watchdog_id not in self._watchdog_entries:
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "unknown_watchdog"},
            }
        result = self._clear_rule_state(watchdog_id, rule_name)
        if not result.get("ok"):
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "unknown_rule"},
            }
        return {
            "request_id": req_id,
            "ok": True,
            "result": {
                "watchdog_id": watchdog_id,
                "rule": rule_name,
                "previous_latched": result.get("previous_latched"),
                "previous_armed": result.get("previous_armed"),
            },
        }

    def _find_watchdog_rule_matches(self, *, rule_name: str) -> list[tuple[str, str]]:
        matches: list[tuple[str, str]] = []
        for watchdog_id in self._ruleset_order:
            for rule in self._watchdog_entries[watchdog_id].ruleset.rules:
                if rule.name == rule_name:
                    matches.append((watchdog_id, rule.name))
        return matches

    def _rpc_watchdog_clear_unscoped(self, req_id: Any, *, rule_name: str) -> Json:
        matches = self._find_watchdog_rule_matches(rule_name=rule_name)
        if not matches:
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "unknown_rule"},
            }
        if len(matches) > 1:
            return {
                "request_id": req_id,
                "ok": False,
                "error": {
                    "code": "ambiguous_rule",
                    "matches": [{"watchdog_id": m[0], "rule": m[1]} for m in matches],
                },
            }
        w_id, r_name = matches[0]
        result = self._clear_rule_state(w_id, r_name)
        return {
            "request_id": req_id,
            "ok": True,
            "result": {
                "watchdog_id": w_id,
                "rule": r_name,
                "previous_latched": result.get("previous_latched"),
                "previous_armed": result.get("previous_armed"),
            },
        }

    def _rpc_watchdog_clear_latch(self, req: Json) -> Json:
        req_id = req.get("request_id")
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "invalid_params"},
            }
        if params.get("all"):
            return self._rpc_watchdog_clear_all_latches(req_id)

        watchdog_id = params.get("watchdog_id")
        rule_name = params.get("rule")
        if not rule_name:
            return {
                "request_id": req_id,
                "ok": False,
                "error": {"code": "missing_rule"},
            }
        rule_name_text = str(rule_name)
        if watchdog_id is not None:
            return self._rpc_watchdog_clear_scoped(
                req_id, watchdog_id=str(watchdog_id), rule_name=rule_name_text
            )
        return self._rpc_watchdog_clear_unscoped(req_id, rule_name=rule_name_text)

    def _build_rpc_registry(self) -> RpcDispatchRegistry:
        handlers = {
            "process.capabilities": self._rpc_watchdog_capabilities,
            "watchdog.status": self._rpc_watchdog_status,
            "watchdog.enable": self._rpc_watchdog_enable,
            "watchdog.disable": self._rpc_watchdog_disable,
            "watchdog.enable_all": self._rpc_watchdog_enable_all,
            "watchdog.disable_all": self._rpc_watchdog_disable_all,
            "watchdog.clear_latch": self._rpc_watchdog_clear_latch,
        }
        return RpcDispatchRegistry(
            handlers=handlers,
            aliases={"watchdog.get_status": "watchdog.status"},
        )

    def _handle_rpc(self, req: Json) -> Json:
        common = self._handle_common_rpc(req)
        if common is not None:
            return common
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self._rpc_invalid_params(req)
        if not hasattr(self, "_rpc_registry"):
            self._rpc_registry = self._build_rpc_registry()
        dispatched = self._rpc_registry.dispatch_with_canonical(req)
        if dispatched is not None:
            return dispatched
        return self._rpc_unknown(req)

    def run(self) -> None:
        try:
            next_tick = time.monotonic() + self._tick_s
            while True:
                now = time.monotonic()
                timeout_s = max(0.0, next_tick - now)
                timeout_ms = int(min(timeout_s, 0.5) * 1000)
                self._set_phase("poll", f"timeout_ms={timeout_ms}")
                self._poll_and_drain(timeout_ms)

                now = time.monotonic()
                if now >= next_tick:
                    self._set_phase("evaluate_rules")
                    self._evaluate_rules()
                    self._mark_progress("rules evaluated")
                    next_tick = now + self._tick_s
                else:
                    self._set_phase("idle")
                    self._mark_progress()
        finally:
            self.close()


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    try:
        proc = WatchdogProcess(
            manager_rpc=ns.manager_rpc,
            manager_pub=ns.manager_pub,
            process_id=ns.process_id,
            rpc_timeout_ms=ns.rpc_timeout_ms,
            heartbeat_endpoint=ns.heartbeat_endpoint,
            heartbeat_period_s=ns.heartbeat_period_s,
            tick_s=ns.tick_s,
            rules=list(ns.rules) if ns.rules else None,
            rules_dir=ns.rules_dir,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    proc.run()


if __name__ == "__main__":
    main()
