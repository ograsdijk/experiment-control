# Agent Notes

## Build / test commands

```powershell
# Lint (must pass before commit)
uv run ruff check src tests examples

# Complexity guard (incremental; runs in CI)
uv run ruff check src/experiment_control/utils/command_interceptors.py src/experiment_control/utils/network_hosts.py src/experiment_control/processes/stream_analysis.py src/experiment_control/processes/influx_writer.py src/experiment_control/sequencer/sequencer.py src/experiment_control/processes/interlock.py src/experiment_control/processes/watchdog.py --select C901

# Tests (CI uses unittest discover, NOT pytest, because tests import `from tests._temp_utils import ...` and there is no tests/__init__.py)
uv run python -m unittest discover -s tests -p "test_*.py" -q

# Typecheck (114 pre-existing errors as of 2026-06-01; do not introduce new ones)
uv run mypy src/experiment_control
```

## Known pre-existing test/lint state (baseline, do not regress)

- `mypy`: 114 errors in 34 files
- `pytest`: collection errors due to missing `tests/__init__.py` — use `unittest discover` instead
- `unittest discover`: 1 failing test (`test_misc_review_followups.DeviceHealthDemotionTests.test_latch_survives_no_telemetry_signals_tick`) — pre-existing `AttributeError` in `driver.py:1722` for `_telemetry_last_call_errors`. Unrelated to refactor.

## Downstream dependency

The package is consumed by `centrex-experimental-stack` (sibling repo at `..\centrex-experimental-stack`). Wire contracts that MUST be preserved:

1. **Manager-client methods**: `call(payload, *, timeout_ms)`, `get_latest(device_id, signal)`, `drain_telemetry()`, `publish_event(topic=, payload=, severity=, device_id=)`
2. **Response envelope**: `{ok: bool, result?, error?: {code, message}, devices?, status?}`
3. **Process base hooks** (on `StateMachineProcessBase` / `ManagedProcessBase`): `rpc_ok/rpc_err/rpc_invalid_params/rpc_unknown` plus deprecated `_rpc_ok/_err/_invalid_params/_unknown` aliases, `command` plus deprecated `_command`, `transition`, `handle_state_machine_rpc` plus deprecated `_handle_state_machine_rpc`, `publish_transition_event` plus deprecated `_publish_transition_event`, `append_run_event` if present, `allowed_transitions`, `last_transition`, `sequence_error_cls`, static `_derive_allowed_transitions_from_graph_edges`
4. **HTTP routes** (exact paths + envelope): `POST /api/{devices,processes}/{id}/call`, `GET /api/processes`, `GET /api/processes/{id}/capabilities`, `GET /api/processes/{id}/cached-call`, `GET /api/snapshots/telemetry`
5. **ZMQ topics**: `manager.telemetry_update`, `manager.command`
6. **Importable symbols** (downstream `from experiment_control.X import Y`):
   - `experiment_control.capabilities.{method,param,capabilities_payload}`
   - `experiment_control.processes.state_machine_base.{StateMachineProcessBase,SequenceError}`
   - `experiment_control.processes.process_base.ManagedProcessBase`
   - `experiment_control.processes.manager_client_helper.ManagerClientHelper`
   - `experiment_control.processes.{interlock,watchdog}.{collect_rulesets,evaluate_*,RuleState}`
   - `experiment_control.utils.zmq_helpers.safe_json_loads`
   - `experiment_control.utils.responses.is_response_ok`
   - `experiment_control.utils.config_parsing.{require_*,optional_dict}`
   - `experiment_control.utils.manager_network.{ManagerNetworkConfig,resolve_manager_network}`
   - `experiment_control.validation.config.validate_instance_config`

When refactoring, if you change any of the above, you MUST also update the downstream repo in the same PR pair.
