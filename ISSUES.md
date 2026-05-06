# Issues

## Telemetry exceptions are swallowed and not surfaced in GUI logs

Observed while debugging the `vacuum-cryo` CTC100 device in `centrex-experimental-stack`.

### What happens

In `src/experiment_control/driver.py`, `DeviceRunner.read_telemetry()` catches exceptions raised by an individual telemetry call and converts all outputs from that call to `quality = BAD` instead of surfacing the exception text to normal logs/UI telemetry consumers.

Then `_publish_telemetry()` stores the failure string only in `self._last_error`.

### Why this is a problem

A device can look partially healthy in the GUI while the real root cause is hidden:
- scalar telemetry calls may still succeed
- one bulk telemetry call may be failing every tick
- the actual exception text is not visible in ordinary GUI logs/telemetry views

Concrete example from CTC100:
- `read_telemetry()` raised because some `getOutput` channels legitimately returned `NaN`
- `is_output_enabled`, `read_out1_setpoint`, and `read_out2_setpoint` still worked
- the GUI therefore showed some correct values while the bulk temperature bundle was bad, without clearly surfacing the driver exception

### Relevant code paths

- `src/experiment_control/driver.py`
  - `DeviceRunner.read_telemetry()`
  - `DeviceRunner._publish_telemetry()`

### Desired follow-up

Consider one or more of the following:
- emit telemetry-call exceptions to the driver/process logs every time they occur (or with rate limiting)
- include the per-call error text in the published telemetry payload for failed signals/calls
- make `last_error` more visible in the GUI/device detail views
- distinguish between expected bad signal values and actual driver/runtime exceptions more clearly
