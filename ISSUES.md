# Issues

(no open issues — see git history for resolved entries)

## Resolved

### Telemetry exceptions are swallowed and not surfaced in GUI logs

Originally observed while debugging the `vacuum-cryo` CTC100 device in
`centrex-experimental-stack`. Fixed by PR "feat(driver): surface per-call
telemetry exceptions in published bundle + rate-limited stderr log".

`DeviceRunner.read_telemetry` now binds exceptions from individual
telemetry calls, populates `_telemetry_last_call_errors` (keyed by the
call's `method` name) and `_telemetry_last_signal_errors` (keyed by
signal name), and rate-limits one stderr emission per `(method,
exception class)` per 30 seconds.

`DeviceRunner._publish_telemetry` threads both into the published
bundle:

- bundle level: `call_errors: dict[str, str]` (omitted when empty)
- per signal: `error: str | None` (present on BAD signals when the
  cause is a runtime exception, truncated to ≤200 chars)

The manager (`manager_driver_pub.ingest_telemetry`) forwards
`call_errors` verbatim into `manager.telemetry_update`, defensively
filtering to (str, str) entries. See `docs/protocol.md` for the
payload shape.

The previously-vague `last_error` and the new structured payload now
let the UI distinguish a "BAD signal because the value is genuinely
out-of-range" from "BAD signal because the driver call raised". UI
work to render the new field is tracked separately.
