# Watchdog Process

The watchdog is a managed process that evaluates telemetry-driven alarm rules and triggers actions (usually safe shutdown commands) when alarms become true. It runs asynchronously, independent of command traffic.

## How it fits in

1. The watchdog starts, loads one or more YAML rulesets, and subscribes to `manager.telemetry_update`.
2. On each tick, it evaluates alarm conditions against cached telemetry.
3. When a rule triggers, it publishes watchdog events and sends command actions via the manager.

## YAML schema (v1)

Each YAML file is a ruleset (`watchdog_id`).

```yaml
version: 1
watchdog_id: vacuum_watchdog

defaults:
  max_age_s: 2.0
  stable_for_s: 0.0
  cooldown_s: 5.0
  latch: false
  on_unknown: ignore   # ignore | trigger

rules:
  - name: vacuum_high_hv_off
    severity: critical
    message: "Vacuum high: turning off HV"

    inputs:
      telemetry:
        - as: vac_p
          device: vgc301
          signal: pressure
          max_age_s: 2.0     # optional override

    condition:
      gt: ["${vac_p.value}", 1.0e-6]   # ALARM WHEN TRUE

    stable_for_s: 2.0      # optional override
    cooldown_s: 10.0       # optional override
    latch: true            # optional override
    on_unknown: trigger    # optional override

    actions:
      - command:
          device_id: hv
          action: enable_output
          params: {enabled: false}
          timeout_s: 2.0     # optional
          retries: 1         # optional (1 = one extra retry)

      - command:
          device_id: hv
          action: set_voltage
          params: {voltage: 0.0}
```

Notes:
- `defaults.*` apply to every rule unless overridden.
- `max_age_s` defaults to `defaults.max_age_s` per telemetry binding.
- `severity` must be one of `info`, `warn`, `critical` (load-time enforced).
- `inputs.telemetry` is required for now.

## Telemetry binding environment

For `as: vac_p`, the rule environment provides:

- `vac_p.value`
- `vac_p.age_s`
- `vac_p.quality`
- `vac_p.device`
- `vac_p.signal`

Bindings are available to template expressions inside `${...}`.

## Unknown telemetry handling

A binding is **usable** only if:
- sample exists
- `quality == "OK"`
- `age_s <= max_age_s`

If any required binding is unusable, the rule is **UNKNOWN**.
- `on_unknown: ignore` ? alarm = false (stable timer resets)
- `on_unknown: trigger` ? alarm = true (still subject to stability/cooldown)

## Rule evaluation semantics

- `condition` is an **alarm**: if it evaluates `true`, the rule wants to trigger.
- `stable_for_s` requires the alarm to stay true for a duration before triggering.
- `cooldown_s` prevents retriggering too quickly.
- `latch: true` triggers once until a manual clear.

Stable timer logic:
- starts on the first `true` alarm
- keeps counting while alarm remains true
- resets when alarm becomes false

## Actions

Each action is **exactly one** of two types — `command` (device RPC) or
`process` (process RPC). Mixing both keys in one action, or neither, is a
config error.

```yaml
actions:
  - command:                 # device RPC
      device_id: hv
      action: enable_output
      params: {enabled: false}
  - process:                 # process RPC (e.g. pause the sequencer)
      process_id: sequencer
      action: sequencer.pause
      params: {}
```

A `command` action sends a device command via the manager:

```json
{
  "type": "command",
  "device_id": "hv",
  "action": "enable_output",
  "params": {"enabled": false},
  "caller_process_id": "watchdog"
}
```

A `process` action wraps the verb in the manager's process-RPC envelope:

```json
{
  "type": "manager.processes.rpc",
  "process_id": "sequencer",
  "request": {"type": "sequencer.pause", "params": {}},
  "caller_process_id": "watchdog"
}
```

A failed action (e.g. `sequencer.pause` when the sequencer is not running →
`process_not_running`) is published as `manager.watchdog.action_failed` and does
**not** block the remaining actions in the rule.

Retries:
- `retries: 0` = one attempt
- `retries: 1` = two attempts

### Implementation note (cleanup debt)

The `manager.processes.rpc` envelope above is hand-built in three places —
`WatchdogProcess._execute_actions`, `client/apis/_base` (the SDK facade), and
`fastapi/app.py` (the gateway) — and `StateMachineProcessBase.command()`
similarly hand-builds the `command` envelope. If the envelope shape changes,
all of these must be updated in lockstep. A shared builder (on
`ManagedProcessBase` or a small helper) would centralize it. Deferred:
low-risk while the shapes are stable, and the right fix spans the SDK + gateway
beyond the watchdog.

## Watchdog RPC (via `manager.processes.rpc`)

Use the manager `manager.processes.rpc` API to call watchdog RPCs.

### `watchdog.status`
Returns loaded rules and state, including the latest evaluated alarm state when
the watchdog has completed at least one rule tick.

```json
{"type": "watchdog.status"}
```

### `watchdog.clear_latch`
Clear latch for a specific rule, or all rules.

```json
{"type": "watchdog.clear_latch", "params": {"watchdog_id": "vacuum_watchdog", "rule": "vacuum_high_hv_off"}}
```

```json
{"type": "watchdog.clear_latch", "params": {"all": true}}
```

If `watchdog_id` is omitted and the rule name is ambiguous, the watchdog returns an error listing matches.

### `watchdog.enable` / `watchdog.disable`
Enable or disable a specific watchdog ruleset.

```json
{"type": "watchdog.enable", "params": {"watchdog_id": "vacuum_watchdog"}}
```

```json
{"type": "watchdog.disable", "params": {"watchdog_id": "vacuum_watchdog"}}
```

### `watchdog.enable_all` / `watchdog.disable_all`

```json
{"type": "watchdog.enable_all"}
```

```json
{"type": "watchdog.disable_all"}
```

## Events (published by manager)

The watchdog publishes events through the manager event bus:

- `manager.watchdog.rules_loaded`
- `manager.watchdog.triggered`
- `manager.watchdog.action_sent`
- `manager.watchdog.action_failed`
- `manager.watchdog.cleared`

See `protocol.md` for payload fields.

## CLI

Example:

```bash
python -m experiment_control.processes.watchdog \
  --id watchdog_main \
  --rules monitors/vacuum.yaml \
  --rules monitors/temp.yaml \
  --tick-s 0.5
```
