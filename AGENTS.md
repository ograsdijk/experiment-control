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

# Typecheck (2 pre-existing errors as of 2026-06-18; do not introduce new ones)
uv run mypy src/experiment_control
```

## Known pre-existing test/lint state (baseline, do not regress)

- `mypy`: 2 errors in 2 files (143 source files checked) as of 2026-06-18. The two
  known errors — do not regress, do not add more:
  - `utils/manager_network.py:153` `[arg-type]` (`_build_tcp_endpoint` arg `str | int` vs `str`)
  - `manager.py:424` `[assignment]` (`float | None` vs base `LifecycleMixin` `float`)
- `pytest`: passes (collection fixed via `pythonpath = ["."]` in pyproject;
  723 passed, 1 skipped, +249 subtests as of 2026-06-18).
- `unittest discover`: passes (717 tests, 1 skipped as of 2026-06-18).

## Frontend (React UI)

The web UI lives in `web/react_ui/` (Vite + React + Mantine + TypeScript, tested with vitest). Run from `web/react_ui/`:

```powershell
npm install
npm test            # vitest run (65 tests in 10 files pass as of 2026-06-18)
npm run typecheck   # tsc --noEmit (clean; do not introduce new errors)
npm run build       # tsc --noEmit && vite build && compress-dist
```

### Deploy model (read before changing the UI)

The FastAPI gateway serves a **packaged** copy of the built UI from `src/experiment_control/_ui_dist`, which is **committed to git** so a plain `pip install` ships the UI without a Node build. Serve precedence (in `fastapi/app.py`: the `EXPERIMENT_CONTROL_SERVE_UI` gate and the env override live in `_resolve_ui_dist_path`; the packaged/dev-fallback selection lives in `_default_ui_dist_path`):

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

1. **Manager-client methods** (on `ManagerClient`; also declared on the `ManagerProtocol` in `client/protocol.py`): `call(payload, *, timeout_ms)`, `get_latest(device_id, signal)`, `get_latest_process(process_id, signal)` (separate process-telemetry cache; #75), `drain_telemetry()`, `publish_event(topic=, payload=, severity=, device_id=)`, `advertise_process_telemetry_schema(*, process_id, schema)` (#75)
2. **Response envelope**: `{ok: bool, result?, error?: {code, message, details?}, devices?, status?}` (`error.details` is optional; `devices?`/`status?` are legacy pass-through keys preserved by `ensure_error_shape` and read via the exact-case `status == "OK"` check in `is_response_ok` / `from_driver_status`)
3. **Process base hooks** (on `StateMachineProcessBase` / `ManagedProcessBase`): `rpc_ok/rpc_err/rpc_invalid_params/rpc_unknown`, `command`, `transition`, `handle_state_machine_rpc`, `publish_transition_event`, `append_run_event` if present, `allowed_transitions`, `last_transition`, `sequence_error_cls`, static `_derive_allowed_transitions_from_graph_edges`; plus — on `ManagedProcessBase`, #75 — `publish_telemetry(signals, *, quality="ok")` and the `process_telemetry_schema()` override hook
4. **HTTP routes** (exact paths + envelope): `POST /api/{devices,processes}/{id}/call`, `GET /api/processes`, `GET /api/processes/{id}/capabilities`, `GET /api/processes/{id}/cached-call`, `GET /api/snapshots/telemetry` (all declared via `@app.*` decorators in `fastapi/app.py`; there is no `APIRouter`)
5. **ZMQ topics**: `manager.telemetry_update`, `manager.command`, `manager.process_telemetry_update` (process-telemetry relay channel, kept separate from device telemetry so process/device ids never collide; #75)
6. **Importable symbols** (downstream `from experiment_control.X import Y`):
   - `experiment_control.capabilities.{method,param,capabilities_payload}`
   - `experiment_control.processes.state_machine_base.{StateMachineProcessBase,SequenceError}`
   - `experiment_control.processes.process_base.ManagedProcessBase`
   - `experiment_control.processes.manager_client_helper.ManagerClientHelper`
   - `experiment_control.processes.interlock.{collect_rulesets,evaluate_interlock_rule}` — note: **no** `RuleState` here; interlock's dataclasses are `Rule`/`Ruleset`/`RulesetEntry`
   - `experiment_control.processes.watchdog.{collect_rulesets,evaluate_watchdog_rule,RuleState}`
   - `experiment_control.utils.zmq_helpers.safe_json_loads`
   - `experiment_control.utils.responses.is_response_ok`
   - `experiment_control.utils.config_parsing.{require_*,optional_dict}`
   - `experiment_control.utils.manager_network.{ManagerNetworkConfig,resolve_manager_network}`
   - `experiment_control.validation.config.validate_instance_config`
   - `experiment_control.federation.MirroredProcessConfig` (#75)
7. **Federation config schema** (`federation.peers[]`, parsed by `federation/config.py`; #75): each peer declares `mirror_devices[] {local_id, remote_device_id}` and/or `mirror_processes[] {local_id, remote_process_id}` (one shared local-id namespace; a peer may declare only `mirror_processes`), plus a per-peer `policy` with fnmatch ACLs `allow_device_actions`/`deny_device_actions` (default allow-all `("*",)`) and `allow_process_actions`/`deny_process_actions` (**default deny-all** `()` — a federated process RPC is never callable unless explicitly allowed)
8. **Sequencer YAML addressing** (`sequencer/ast.py`; #75): a `call` step sets exactly one of `device:` or `process:` — `process:` targets a (possibly federated) process RPC namespace (e.g. `mw.retune`) routed via `manager.processes.rpc`; the same `device:`/`process:` choice extends to `wait_until`/`assign`/adaptive telemetry sources. Federated-RPC error codes: `federation_acl_denied`, `peer_unavailable`
9. **Run-file (HDF) layout** (`processes/hdf_writer.py`; #75): device telemetry under `/telemetry`, process telemetry under `/process_telemetry/<process_id>` (datasets carry `source_kind="process"`)

When refactoring, if you change any of the above, you MUST also update the downstream repo in the same PR pair. The process-federation contracts marked `(#75)` are stable but **not yet consumed** by `centrex-experimental-stack` (the stack adopts them incrementally, e.g. `spb_microwave` `mw.retune`); the same-PR-pair rule binds once a downstream call site exists — until then, preserve them as the agreed interface.
