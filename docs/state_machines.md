# State Machine Processes

This project includes a reusable state-machine process base:

- `src/experiment_control/processes/state_machine_base.py`

It is built on top of:

- `src/experiment_control/processes/process_base.py`

Use it when a workflow needs explicit phases, strict transition rules, and RPC
control (start/pause/resume/abort/status).

## Core Ideas

- A process has one current `state`.
- State changes are validated by `allowed_transitions`.
- The process loop runs continuously and calls `_tick_state(...)`.
- Process RPC controls and inspects the state machine.

## Base Class Responsibilities

`StateMachineProcessBase` provides:

- Managed process startup (`manager` client, RPC router, poller, heartbeat).
- Transition API (`transition`, `can_transition`, `allowed_next_states`).
- Transition events on the manager bus:
  - topic: `manager.state_machine.transition`
- Built-in RPC:
  - `<namespace>.status`
  - `<namespace>.transition`
  - `<namespace>.stop`
  - `process.capabilities`

It also exposes transition hooks:

- `_on_exit_<state>(...)`
- `_on_enter_<state>(...)`

And helper response methods inherited from `ManagedProcessBase`:

- `rpc_ok(...)`
- `rpc_err(...)`
- `rpc_unknown(...)`
- `rpc_invalid_params(...)`

## Minimal Subclass Pattern

1. Inherit from `StateMachineProcessBase`.
2. Provide `allowed_transitions`.
3. Implement `_tick_state(self, now_mono)`.
4. Optionally add workflow RPC in `_handle_rpc(...)`.
5. Optionally extend capabilities with `_extra_capability_methods(...)`.

```python
class ExampleProcess(StateMachineProcessBase):
    def __init__(self, *, manager_rpc: str, manager_pub: str, process_id: str) -> None:
        super().__init__(
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            process_id=process_id,
            rpc_namespace="example",
            initial_state="IDLE",
            allowed_transitions={
                "IDLE": {"RUNNING"},
                "RUNNING": {"PAUSED", "FAILED", "DONE"},
                "PAUSED": {"RUNNING", "FAILED"},
                "DONE": {"IDLE"},
                "FAILED": {"IDLE"},
            },
        )

    def _tick_state(self, now_mono: float) -> None:
        if self.state == "IDLE":
            return
        if self.state == "RUNNING":
            # workflow logic here
            pass

    def _handle_rpc(self, req: dict[str, object]) -> dict[str, object]:
        base = self.handle_state_machine_rpc(req)
        if base is not None:
            return base
        return self.rpc_unknown(req)
```

## RPC Model

Your process usually implements workflow RPC in addition to the base RPC.

Example command set:

- `workflow.start`
- `workflow.pause`
- `workflow.resume`
- `workflow.abort`
- `workflow.reset`
- `workflow.status`

Recommended behavior:

- `start` allowed only from terminal or idle states.
- `pause` only from active run states.
- `abort` transitions to cleanup path.
- `reset` returns to `IDLE` and clears runtime context.

## Transition Design

Treat transitions as a safety contract, not just flow control.

Recommended pattern:

- Normal path: `IDLE -> ... -> DONE`
- Fault path: `ACTIVE_STATE -> CLEANUP -> FAILED`
- Operator abort path: `ACTIVE_STATE -> CLEANUP -> IDLE` (or `ABORTED`)

Why:

- Fault and abort are semantically different.
- Both should run bounded cleanup logic.
- Terminal states stay meaningful (`DONE` vs `FAILED`).

## Status Payload

The base status contains:

- `state`
- `state_since`
- `last_error`
- `last_transition`
- `allowed_next_states`

Subclasses can add domain fields by overriding `_status_payload()`.

## Configuration and Startup

State-machine processes are started like other managed processes via process YAML.

See:

- `docs/manager_start.md`

## Testing Guidance

At minimum test:

- valid and invalid transitions
- fault-to-cleanup behavior
- abort-to-cleanup behavior
- RPC contract (`status/start/pause/resume/abort/reset`)

See:

- `tests/test_state_machine.py`
