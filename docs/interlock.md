# Interlock Process

The interlock is a managed process that evaluates safety/policy rules before the manager dispatches device commands.
It registers routes with the manager, listens to telemetry updates, and synchronously answers
`command_interceptor.check` RPC requests.
It can also load/enable/disable rulesets at runtime via RPC.

## How it fits in

1. The interlock starts, loads one or more YAML rulesets, and registers command routes.
2. The manager consults the interlock for matching commands before routing them to devices.
3. The interlock answers using only cached telemetry from `manager.telemetry_update`.
4. If any rule rejects, the manager blocks the command (fail-closed).

## Rule evaluation summary

- Routes are matched by `device_id` + `action` (wildcards `*` allowed).
- Rules are checked in file order, then rule order.
- A rule may:
  - reject the command (with structured error)
  - allow the command unchanged
  - allow with parameter transform (params only)

## YAML schema (v1)

```yaml
version: 1
interceptor_id: hv_safety
defaults:
  max_age_s: 2.0

rules:
  - name: block_hv_enable_pressure
    match: {device_id: hv, action: enable_output}
    inputs:
      telemetry:
        - as: vac_p
          device: vgc301
          signal: pressure
          max_age_s: 2.0
    condition:
      lt: ["${vac_p.value}", 1.0e-6]
    on_block:
      message: "HV enable blocked: pressure too high"
      code: CONDITION_FAILED

  - name: clamp_hv_setpoint_max
    match: {device_id: hv, action: set_voltage}
    condition: {always: true}
    allow_transform:
      params:
        voltage: "${min(params.voltage, 5000.0)}"
```

Notes:
- `defaults.max_age_s` applies to telemetry bindings that omit `max_age_s`.
- Rules without `inputs.telemetry` are valid (pure params rules).
- `on_block.message` is optional; a generic message is used if missing.
- `on_block.code` is optional; defaults to `CONDITION_FAILED`.
- `allow_transform` only supports `params`. `device_id` or `action` rewrites are rejected.

## Telemetry bindings

Each telemetry binding provides a value in the rule environment under its `as` name:

- `<alias>.value`
- `<alias>.units`
- `<alias>.quality`
- `<alias>.t_mono`
- `<alias>.t_wall`
- `<alias>.age_s`

Telemetry checks happen before evaluating `condition`:

- Missing sample -> `TELEMETRY_MISSING`
- Not OK quality -> `TELEMETRY_NOT_OK`
- Older than `max_age_s` -> `TELEMETRY_STALE`

Quality is considered OK only when `quality == "OK"`.

## Condition evaluation

Conditions reuse the sequencer condition DSL (`sequencer.eval`), including:

- `eq`, `ne`, `gt`, `ge`, `lt`, `le`
- `and`, `or`, `not`
- `abs_lt`
- `always` (must be the only key if present)

`always` is a trivial condition that evaluates to true/false directly and ignores all other operators. Use it for “always-on” rules such as pure parameter clamping.

Expressions inside `${...}` are evaluated in a restricted environment. Available values:

- `params.<key>` for incoming command parameters
- Telemetry bindings (see above)
- `device_id` and `action`

## Interlock RPC (via `manager.processes.rpc`)

These requests are sent to the interlock process through the manager
`manager.processes.rpc` API.

### `interlock.list`
Returns the currently loaded interceptors and their enabled state.

```json
{"type": "interlock.list", "request_id": "optional"}
```

### `interlock.status`
Returns loaded interceptors with per-rule runtime status.

```json
{"type": "interlock.status", "request_id": "optional"}
```

Notes:
- Each rule entry includes `rule_id`, `name`, `enabled`, match info, telemetry bindings,
  and whether it has `allow_transform`.
- Rule enable/disable state is runtime-only and resets when the process restarts.

### `interlock.load`
Load a new interceptor ruleset while running.

```json
{"type": "interlock.load", "params": {"path": "path/to/rules.yaml", "replace": true, "enable": true}}
```

or load from raw text:

```json
{"type": "interlock.load", "params": {"text": "...yaml...", "replace": true, "enable": true, "source": "optional-label"}}
```

Notes:
- `replace` controls whether an existing `interceptor_id` is replaced (default true).
- If `enable` is omitted and the interceptor already exists, its current enabled state is preserved.

### `interlock.enable` / `interlock.disable`

```json
{"type": "interlock.enable", "params": {"interceptor_id": "hv_safety"}}
```

```json
{"type": "interlock.disable", "params": {"interceptor_id": "hv_safety"}}
```

### `interlock.enable_rule` / `interlock.disable_rule`

```json
{"type": "interlock.enable_rule", "params": {"interceptor_id": "hv_safety", "rule_id": "r0"}}
```

```json
{"type": "interlock.disable_rule", "params": {"interceptor_id": "hv_safety", "rule_id": "r0"}}
```

### `interlock.enable_all` / `interlock.disable_all`

```json
{"type": "interlock.enable_all"}
```

```json
{"type": "interlock.disable_all"}
```

## Response behavior

If a rule rejects, the interlock returns:

```json
{
  "ok": true,
  "allow": false,
  "interceptor_id": "hv_safety",
  "rule": "block_hv_enable_pressure",
  "error": {
    "code": "CONDITION_FAILED",
    "message": "HV enable blocked: pressure too high",
    "details": {"binding": "vac_p", "device": "vgc301", "signal": "pressure"}
  }
}
```

If a rule transforms parameters, it returns:

```json
{
  "ok": true,
  "allow": true,
  "command": {"device_id": "hv", "action": "set_voltage", "params": {"voltage": 5000.0}},
  "interceptor_id": "hv_safety",
  "rule": "clamp_hv_setpoint_max",
  "note": "transformed"
}
```

## Fail-closed behavior (manager)

The manager blocks commands when:

- the interlock process is unavailable (`INTERCEPTOR_UNAVAILABLE`)
- it times out (`INTERCEPTOR_TIMEOUT`)
- it returns malformed data (`INTERCEPTOR_BAD_RESPONSE`)
- it explicitly rejects (`INTERCEPTOR_REJECTED`)

## Runtime notes

- The interlock never performs synchronous reads from the manager during checks.
- It relies only on the telemetry cache populated from `manager.telemetry_update`.
- Interlock startup registers routes via `manager.interceptors.register` with
  `replace: true`.
