# Operator changelog — May 2026 review followups

- Driver telemetry call failures now publish actionable `call_errors` and per-signal error strings instead of silently presenting stale/OK-looking values.
- `relay.only_mirrored_devices: false` now takes effect; instances configured this way can see peer non-device events on the local bus.
- TUI bulk start/stop actions run without freezing the interface.
- Watchdog action chains run asynchronously and no longer wedge the tick on slow remediation.
- Watchdog and interlock rule-condition exceptions publish rate-limited `rule_error` events.
- Interlock `rule_error` event publishing is fire-and-forget and no longer delays command rejection responses.
- Influx writer reports skipped invalid telemetry signals and backs off per destination on HTTP write failures, honouring `Retry-After` when present.
- Sequencer `range:` with a wrong-sign step raises instead of silently producing an empty list.
- Sequencer `wait_until.reduce.max_samples` caps retained samples, defaulting to 10 000.
- Stream-analysis fit outputs include `last_fit_attempt_ts_mono` and `last_fit_success_ts_mono` so stale last-known-good fits are detectable.
