# Agent Notes

## Build / test commands

```powershell
# Lint (must pass before commit)
uv run ruff check src tests examples

# Complexity guard (incremental; runs in CI)
uv run ruff check src/experiment_control/utils/command_interceptors.py src/experiment_control/utils/network_hosts.py src/experiment_control/processes/stream_analysis.py src/experiment_control/processes/influx_writer.py src/experiment_control/sequencer/sequencer.py src/experiment_control/processes/interlock.py src/experiment_control/processes/watchdog.py --select C901

# Tests (both runners work; CI uses unittest discover)
uv run python -m unittest discover -s tests -p "test_*.py" -q
# pytest also works now: `pythonpath = ["."]` in [tool.pytest.ini_options] puts
# the repo root on sys.path so `from tests._temp_utils import ...` resolves
# (tests/ is a namespace package, no __init__.py).
uv run pytest -q

# Typecheck (107 pre-existing errors as of 2026-06-08; do not introduce new ones)
uv run mypy src/experiment_control
```

## Known pre-existing test/lint state (baseline, do not regress)

- `mypy`: 107 errors in 32 files
- `pytest`: passes (collection fixed via `pythonpath = ["."]` in pyproject;
  675 passed, 1 skipped as of 2026-06-17).
- `unittest discover`: passes (669 tests, 1 skipped as of 2026-06-17).

## Frontend (React UI)

The web UI lives in `web/react_ui/` (Vite + React + Mantine + TypeScript, tested with vitest). Run from `web/react_ui/`:

```powershell
npm install
npm test            # vitest run (65 tests pass as of 2026-06-17)
npm run typecheck   # tsc --noEmit (clean; do not introduce new errors)
npm run build       # tsc --noEmit && vite build && compress-dist
```

### Deploy model (read before changing the UI)

The FastAPI gateway serves a **packaged** copy of the built UI from `src/experiment_control/_ui_dist`, which is **committed to git** so a plain `pip install` ships the UI without a Node build. Serve precedence (`fastapi/app.py` `_default_ui_dist_path`, gated by env `EXPERIMENT_CONTROL_SERVE_UI`):

1. `EXPERIMENT_CONTROL_UI_DIST` env override, if set
2. packaged `src/experiment_control/_ui_dist`
3. dev fallback `web/react_ui/dist` (source tree only)

Consequence: **a UI source change does not ship until `_ui_dist` is rebuilt and committed.** Rebuild and stage the packaged bundle alongside the source change:

```powershell
.\scripts\build_packaged_ui.ps1   # npm install + npm run build, then dist -> _ui_dist
```

Asset filenames are content-hashed, so several `_ui_dist/**` files rename on each build — committing the whole regenerated tree is expected.

## Downstream dependency

The package is consumed by `centrex-experimental-stack` (sibling repo at `..\centrex-experimental-stack`). Wire contracts that MUST be preserved:

1. **Manager-client methods**: `call(payload, *, timeout_ms)`, `get_latest(device_id, signal)`, `drain_telemetry()`, `publish_event(topic=, payload=, severity=, device_id=)`
2. **Response envelope**: `{ok: bool, result?, error?: {code, message}, devices?, status?}`
3. **Process base hooks** (on `StateMachineProcessBase` / `ManagedProcessBase`): `rpc_ok/rpc_err/rpc_invalid_params/rpc_unknown`, `command`, `transition`, `handle_state_machine_rpc`, `publish_transition_event`, `append_run_event` if present, `allowed_transitions`, `last_transition`, `sequence_error_cls`, static `_derive_allowed_transitions_from_graph_edges`
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
