# experiment-control Refactor Plan

Status as of 2026-06-02: Phases 1–7 and 9–10 complete/implemented; Phase 8 pending.

This plan reorganizes ~20% of the codebase (~2700 LOC reduction) without
breaking the downstream consumer `centrex-experimental-stack` (sibling repo
at `..\centrex-experimental-stack`). It assumes coordinated changes to the
downstream repo are permitted ("Full coordinated rename pass" mode).

---

## Wire contracts that MUST be preserved

(Mirrored in `AGENTS.md`.) These are the only surfaces downstream actually
depends on; every refactor step below must verify they still hold.

1. **Manager-client methods**: `call(payload, *, timeout_ms)`,
   `get_latest(device_id, signal)`, `drain_telemetry()`,
   `publish_event(topic=, payload=, severity=, device_id=)`.
2. **Response envelope** from `manager.call(...)`:
   `{ok: bool, result?, error?: {code, message}, devices?, status?}`.
3. **Process base hooks** (on `StateMachineProcessBase` / `ManagedProcessBase`):
   `rpc_ok/rpc_err/rpc_invalid_params/rpc_unknown` plus deprecated
   `_rpc_ok/_err/_invalid_params/_unknown` aliases, `command` plus deprecated
   `_command`, `transition`, `handle_state_machine_rpc` plus deprecated
   `_handle_state_machine_rpc`, `publish_transition_event` plus deprecated
   `_publish_transition_event`, `append_run_event` if present,
   `allowed_transitions`, `last_transition`, `sequence_error_cls`, static
   `_derive_allowed_transitions_from_graph_edges`.
4. **HTTP routes** (exact paths + envelope):
   `POST /api/{devices,processes}/{id}/call`, `GET /api/processes`,
   `GET /api/processes/{id}/capabilities`,
   `GET /api/processes/{id}/cached-call`, `GET /api/snapshots/telemetry`.
5. **ZMQ topics**: `manager.telemetry_update`, `manager.command`.
6. **Driver action names addressed over HTTP**:
   SynthHD uses parameterized `set_frequency/get_frequency/set_power/get_power/set_enable/get_enable/set_phase/get_phase` actions with a `channel` parameter.
7. **Importable symbols** (downstream `from experiment_control.X import Y`):
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

---

## Validation commands (run after every step)

```powershell
uv run ruff check src tests examples
uv run mypy src/experiment_control          # baseline: 114 errors, do not regress
uv run python -m unittest discover -s tests -p "test_*.py" -q   # baseline: 1 pre-existing failure
```

After Phases 4, 5, 6, 7, 8, additionally run downstream smoke tests:

```powershell
cd ..\centrex-experimental-stack
uv run python -m unittest discover -s tests -p "test_*.py" -q
# Plus at least one instance test, e.g.:
uv run python -m unittest discover -s instances\state-preparation\tests -p "test_*.py" -q
```

---

## Phase 1 — Pure-internal cleanups ✅ COMPLETE (2026-06-01)

**Risk:** Low. **LOC delta:** -68 source + 25 new (`utils/errors.py`).

- [x] **1.1** Move `_TRANSIENT_CAPABILITIES_CODES` to
      `src/experiment_control/utils/errors.py` as
      `TRANSIENT_CAPABILITIES_ERROR_CODES`; import from
      `manager_log_events.py` and `fastapi/app.py`.
- [x] **1.2** Delete `manager_internal_routes_device.py` (verified dead code —
      none of its 4 `route_*` functions were imported anywhere; the same
      logic exists on `Manager` at `manager.py:2018-2035`).
- [x] **1.3** Delete dead regex definitions from `manager.py:357-372`
      (`_LOG_LEVEL_PREFIX_RE`, `_LOG_LEVEL_BRACKET_PREFIX_RE`,
      `_LOG_LEVEL_INLINE_RE`, `_LOG_LEVEL_TABLE_RE`, `_EXCEPTION_LINE_RE` —
      all unreferenced; `manager_process_logs.py` has its own correct copies).
      Removed unused `import re`.
- [~] **1.4** ~~Extract `RpcHandleBase` mixin for `DeviceHandle`/`ProcessHandle`~~
      **DEFERRED** to Phase 8 — field reordering risk outweighs payoff in
      isolation.
- [x] **1.5** Write `AGENTS.md` documenting build/test commands, baseline
      state, and downstream wire contracts.

---

## Phase 2 — Boilerplate extraction in manager helpers ✅ COMPLETE (2026-06-01)

**Risk:** Low (internal; no contract change). **Est. LOC delta:** -150.

- [x] **2.1** `manager_route_handlers.py:315-431` (`route_process_rpc`):
      Bind `_publish = functools.partial(manager._publish_process_command_response, ...)`
      with the common kwargs at the top; collapse the 8 publish call sites
      to a single trailing `return _publish(response=resp)`. Add helper
      `def _reply(code: str, **kw) -> Json` for error returns.
- [x] **2.2** `manager_device_routing.py:115-205`: Extract
      `_resolve_local_device(manager, req) -> tuple[str | None, DeviceHandle | None, Json | None]`
      that returns `(device_id, handle, err_response)`. Apply to all 6
      device handlers (`_route_device_driver_start/_stop/_restart/_recover/_connect/_disconnect`)
      and `_route_command`.
- [x] **2.3** `manager_rpc_calls.py:79-256`: Extract
      `_blocking_call_with_pump(sock, request_b, *, timeout_ms, response_filter, pump_fn)`
      capturing the shared poll-and-pump loop. Apply to both
      `call_device_rpc` and `call_process_rpc`.
- [x] **2.4** `manager.py:1004-1043`: Unify `_record_supervisor_raw_log`
      and `_record_supervisor_emitted_log` (parameterize deque + severity flag).
- [x] **2.5** `manager_route_handlers.py:644-695`: Extract
      `_simple_params_call(callable_, params)` for `route_manager_log_tail`
      and `route_manager_command_journal_tail`.
- [x] **2.6** `manager_route_handlers.py:633-641, 699-711`: Share validator
      between `route_manager_log_publish` and `route_manager_event_publish`
      (both validate `payload is dict` then normalize `topic`).
- [x] **2.7** Run validation suite. `ruff` passed; `mypy` remained at the documented 114-error baseline; `unittest discover` remained at the documented 1-error baseline; complexity guard passed.

---

## Phase 3 — Defensive `getattr` cleanup ✅ COMPLETE (2026-06-02)

**Risk:** Low. **Est. LOC delta:** -200.

- [x] **3.1** Audit `Manager.__init__` (`manager.py:390-704`); enumerate every
      `self._foo = ...` it assigns. Document in a comment block at the top of
      `__init__`. Add explicit type annotations.
- [x] **3.2** Audit helper-module lazy state attached to manager via
      `setattr` (e.g. `_process_rss_cache`, `_telemetry_device_order`,
      `_chunk_device_order`, `_process_hb_refresh_error_suppressed`). For each:
      add explicit init in `Manager.__init__` (typed empty container / `None`).
      Remove the helper's lazy `getattr(...None); setattr(...)` initialization.
- [x] **3.3** Remove all `getattr(manager, "_attr", default)` calls now that
      defaults are unnecessary. Highest concentration:
      `manager_route_handlers.py:501-573` (`route_manager_identity`,
      24 calls), `manager_process_supervision.py:131-1322`,
      `manager_driver_pub.py:18-101`, `manager_log_events.py:73-95`.
- [x] **3.4** Remove the `_instance_lock_funcs()` late-import indirection
      (`manager_route_handlers.py:42-49`). Inject the functions as a
      constructor argument or class attribute on `Manager`. Update any
      tests that patch `experiment_control.manager.read_instance_lock_status`
      to patch the new injection point.
- [x] **3.5** Run validation suite + downstream smoke test (Phase 3 cannot
      affect downstream but verify nothing leaks).

---

## Phase 4 — Trace pipeline unification (`fastapi/app.py`) ⚠️ IMPLEMENTED, DOWNSTREAM SMOKE BLOCKED (2026-06-01)

**Risk:** Medium (UI bundles consume WebSocket payloads).
**Est. LOC delta:** -80.

- [x] **4.1** Define `TraceAggregator` class in
      `src/experiment_control/fastapi/_trace_aggregator.py` encapsulating
      `rolling_buf`, `rolling_sum`, `block_sum`, `block_count`, `pending_msg`,
      plus methods `add_frame()`, `flush()`, `reset()`. Unit-test in
      isolation.
- [~] **4.2** Snapshot the WebSocket payload schema BEFORE the refactor:
      record one session each of `/ws/raw_stream` and `/ws/stream/{id}`
      to a fixture file. Add an integration test that replays input
      frames and asserts byte-identical output frames. Deferred: no existing replay harness; behavior covered by isolated aggregator tests plus focused FastAPI tests.
- [x] **4.3** Replace closure-locals in `ws_raw_stream`
      (`fastapi/app.py:209-253`) with single `TraceAggregator` instance.
- [x] **4.4** Replace `_WorkspaceTraceWsState`
      (`fastapi/app.py:1800-2090`) with `dict[str, TraceAggregator]`.
      Removes `_apply_trace_average` and `_workspace_apply_trace_average`
      (line-for-line duplicates).
- [~] **4.5** Run replay test to confirm zero wire-format drift. Replay harness not present; `test_trace_aggregator`, `test_trace_processing`, `test_fastapi_gateway`, and `test_fastapi_workspace_routes` pass.
- [~] **4.6** Run downstream smoke test. Blocked by downstream environment import-path failures (`experiment_control`, `centrex_shared` not importable); root downstream test discover ran 0 tests.

---

## Phase 5 — Response shape canonicalization ⚠️ IMPLEMENTED, DOWNSTREAM SMOKE BLOCKED (2026-06-01)

**Risk:** Medium (downstream parses exact `{ok, result|error:{code,message}}` shape).
**Est. LOC delta:** ~0 (refactor, not removal).

- [x] **5.1** Define `@dataclass class RpcResponse` in
      `src/experiment_control/utils/responses.py` with fields
      `ok: bool, result: Any | None = None, error: ErrorPayload | None = None`.
      Add classmethod constructors `RpcResponse.success(result=...)`,
      `RpcResponse.failure(code, message, details=None)`, and method
      `.to_dict() -> dict[str, Any]`. **The dict shape MUST exactly match
      what downstream expects** (top-level `ok`, optional `result`, optional
      `error: {code, message}`).
- [x] **5.2** Add adapter `from_driver_status(d: dict) -> RpcResponse`
      that handles `status: "OK"/"ERROR"` legacy shape. Place in same module.
- [x] **5.3** Migrate `manager_route_handlers.py`, `manager_device_routing.py`,
      `manager_internal_rpc.py` to use `RpcResponse.failure(...).to_dict()`
      instead of inline `{"ok": False, "error": {...}}` dicts (40+ sites).
      **Wire format unchanged** — verify with grep that
      `json.dumps(RpcResponse.failure("x","y").to_dict())` is identical to
      pre-refactor output.
- [x] **5.4** Replace `_ensure_error_shape` and `_normalize_command_response`
      (`fastapi/app.py:183-194, 277-290`) with the new adapters.
- [x] **5.5** Add regression test asserting exact envelope produced for
      known error codes, mirroring downstream test
      `tests/test_spb_microwave_compatible.py:242-254` which expects
      `"invalid_state: bad state"` formatting.
- [~] **5.6** Run downstream smoke test including
      `test_spb_microwave_compatible.py`. Blocked by downstream environment (`numpy` missing after adding upstream/downstream `PYTHONPATH`; earlier run also lacked downstream import paths).

---

## Phase 6 — Per-channel driver methods (SynthHD) ✅ COMPLETE (2026-06-02)

**Risk:** Low (with coordinated downstream change). **Est. LOC delta:** -40.

This phase requires a coordinated commit in `centrex-experimental-stack`.

- [x] **6.1** Add parameterized methods to
      `src/experiment_control/drivers/synthhd_driver.py`:
      `set_frequency(channel, freq_hz)`, `get_frequency(channel)`,
      `set_power(channel, dbm)`, `set_enable(channel, on)`,
      `set_phase(channel, deg)`.
- [x] **6.2** Delete the 12 per-channel methods
      (`set_frequency_channel_0/1`, `get_frequency_channel_0/1`, etc.,
      `synthhd_driver.py:14-60`).
- [x] **6.3** Downstream PR: change
      `centrex-experimental-stack/legacy-interfaces/LaserLockCompatible.py:338,361`
      from `f"set_frequency_channel_{int(channel)}"` (and `get_`) to
      `{"action": "set_frequency", "params": {"channel": channel, "freq_hz": ...}}`
      and similarly for `get_frequency`.
- [x] **6.4** Add upstream SynthHD driver test verifying parameterized channel methods and removal of per-channel wrappers.
- [x] **6.5** **Cleanup (cleanup #16)**: In `drivers/dummy_*.py`, extract a
      `scalar_telemetry(name, units)` helper for the duplicated
      `DEFAULT_TELEMETRY_CALLS_*` constants across `dummy_driver.py`,
      `dummy_frequency_noise_driver.py`, `dummy_resonance_trace_driver.py`,
      `dummy_trace_driver.py`.
- [x] **6.6** Land both PRs together; do not merge upstream until downstream
      is ready. Downstream production call sites and legacy per-channel test assertions are updated; downstream smoke remains environment-dependent.

---

## Phase 7 — Client API collapse ⚠️ IMPLEMENTED, HANDLE PROXY DEFERRED (2026-06-01)

**Risk:** Medium (internal; no downstream Python imports of these classes,
but `fastapi/app.py` consumes them — HTTP routes must stay stable).
**Est. LOC delta:** -600.

- [x] **7.1** Define shared `ClientFacadeBase._call_type(...)` helper in `src/experiment_control/client/apis/_base.py` that builds the `{"type": request_type, ...kwargs}` payload and forwards to the underlying manager-client. Used private name to avoid breaking existing public `call(...)` signatures on process/device APIs.
- [x] **7.2** Convert `DeviceAPI`, `ProcessAPI`, `HdfAPI`, `SequencerAPI`, and `ManagerAPI` method bodies to use `_call_type` where applicable. `WaiterAPI` remains polling orchestration rather than simple forwarding.
- [~] **7.3** For Handle classes (`DeviceHandle`, `ProcessHandle` in
      `client/apis/device.py:158`, `client/apis/process.py:122`):
      Deferred replacing explicit methods with `__getattr__` because it would degrade method signatures/introspection; added tests verifying current handle methods remain callable.
- [x] **7.4** Run validation suite. `ruff` and complexity guard passed; `mypy` remained at 114 baseline errors; full unittest remained at 1 known baseline error; focused client/FastAPI tests passed.
- [~] **7.5** Run downstream smoke test. Still blocked by downstream environment dependencies/imports; no production downstream imports of client facade classes were found.

---

## Phase 8 — Manager mixin conversion (largest change) ✅ COMPLETE (2026-06-02)

**Risk:** Medium (lots of touch points; landed incrementally).
**Est. LOC delta (planned):** -1500.
**Actual LOC delta:** **+1445 LOC across src/** (counter-intuitive — see post-mortem below).

Goal: eliminate ~200 one-line forwarder methods on `Manager` by converting
each `manager_*.py` helper module into a mixin class that `Manager` inherits.

**Scope correction (2026-06-02 audit):** Only 16 modules out of the 19
originally enumerated actually have `manager`-taking helpers. The
three excluded modules — `manager_process_spec.py`, `manager_config.py`,
`manager_client.py` — contain pure pre-construction utilities (no
`manager` first arg) and stay as module-level functions. The migration
order below has been updated accordingly; `manager_log_events.py`
takes the slot the plan reserved for `manager_process_spec.py`.

- [x] **8.1** Scaffolding: added 16 empty mixin classes to each
      `manager_*.py` helper module. Updated `Manager` to inherit:
      ```python
      class Manager(
          PubSubMixin, CommandJournalMixin, ProcessSpecMixin,
          LogEventsMixin, LogsMixin, RuntimeMetadataMixin,
          DriverPubMixin, ConfigMixin, ManagerClientMixin,
          InternalRpcMixin, RequestRoutingMixin, RouteHandlersMixin,
          DeviceRoutingMixin, RpcCallsMixin, InterceptorRoutesMixin,
          ProcessRecoveryMixin, ProcessLogsMixin, LifecycleMixin,
          ProcessSupervisionMixin,
      ):
          ...
      ```
- [~] **8.2** Migrate one module at a time, in dependency order (least-coupled
      first). For each module:
      1. Convert `def shared_foo(manager, ...)` → `def foo(self, ...)`
         (replace `manager` parameter with `self`).
      2. Move into the mixin class.
      3. Delete corresponding wrapper from `Manager` in `manager.py`.
      4. Delete the `from .manager_X import Y as shared_Y` import.
      5. Run validation suite.
      6. **Commit per module.**

      Note: where downstream tests import the module-level helper
      directly (e.g. `from experiment_control.manager_log_events
      import maybe_publish_log_event`), the migration keeps a thin
      module-level forwarder that delegates to the mixin method, so
      both call styles continue to work until the tests migrate.

      Migration order:
      - [x] 8.2.1 `manager_pubsub.py` → `PubSubMixin` (1 method: `_publish_manager_event`)
      - [x] 8.2.2 `manager_command_journal.py` → `CommandJournalMixin`
            (2 methods + drop dead `_should_journal_command_action` forwarder)
      - [x] 8.2.3 `manager_log_events.py` → `LogEventsMixin`
            (3 methods: `_write_sink_line`, `_maybe_emit_manager_log_sink`,
            `_maybe_publish_log_event`. Slot originally reserved for
            `manager_process_spec.py`, which has no mixin candidates.)
      - [x] 8.2.4 `manager_logs.py` → `LogsMixin`
            (6 methods: `_open_manager_log_sink_file`, `_close_manager_log_sink_file`,
            `_manager_log_sink_event`, `_manager_log_sink_is_duplicate`,
            `_emit_log`, `_emit_log_from_payload`, plus `_log_tail`.
            Dropped 11 dead one-line forwarder methods on Manager and
            the vestigial `manager` first-arg from `resolve_manager_log_stderr_enabled`
            and `log_tail_filters`.)
      - [x] 8.2.5 `manager_runtime_metadata.py` → `RuntimeMetadataMixin`
            (5 methods: `_effective_metadata_for_device`,
            `_runtime_metadata_state`, `_touch_runtime_metadata_revision`,
            `_publish_device_config`, `_device_config_payload`.)
      - [x] 8.2.6 `manager_internal_rpc.py` → `InternalRpcMixin`
            (3 methods: `_handle_internal_rpc`, `_route_internal_request`,
            `_ensure_route_registries`. Three module-level trampolines
            kept for `tests.test_dealer_request_id_correlation` monkey-patches.)
      - [x] 8.2.7 `manager_request_routing.py` → `RequestRoutingMixin`
            (4 methods: `_build_internal_action_registry`,
            `_build_internal_type_registry`, `_build_process_route_registry`,
            `_build_manager_route_registry`. Forced declaring all 24
            `_route_*` handlers on `ManagerProtocol` so the registry
            builders type-check cleanly via `self`. Drift test now
            covers 34 cross-mixin contracts.)
      - [x] 8.2.8 `manager_rpc_calls.py` → `RpcCallsMixin` (3 methods + 4 helpers)
      - [x] 8.2.9 `manager_process_recovery.py` → `ProcessRecoveryMixin` (9 methods)
      - [x] 8.2.10 `manager_process_logs.py` → `ProcessLogsMixin` (10 methods, 5 trampolines kept)
      - [x] 8.2.11 `manager_lifecycle.py` → `LifecycleMixin` (9 methods incl. private waits)
      - [~] 8.2.12 `manager_interceptor_routes.py` → SKIPPED — module is consumed
            only by ``manager_route_handlers.py`` (helper-to-helper); Manager
            doesn't forward to these functions directly. Empty scaffold kept
            in MRO for symmetry; helpers stay as module-level.
      - [x] 8.2.13 `manager_driver_pub.py` → `DriverPubMixin` (2 of 23, thin wrappers)
            — kept module-level: 23-function cluster too tightly coupled for
            per-method migration; mixin wraps only the 2 Manager-side entry
            points (`_handle_driver_pub`, `_ingest_chunk_ready`). Two more
            (`_ingest_telemetry`, `_ingest_heartbeat`) stay as Manager
            forwarders because they pass Manager-module enum classes.
      - [x] 8.2.14 `manager_device_routing.py` → `DeviceRoutingMixin` (1 of 19,
            single entry point `_route_device_request`)
      - [x] 8.2.15 `manager_route_handlers.py` → `RouteHandlersMixin` (24 thin
            wrappers replacing Manager forwarders; 21 Manager forwarders deleted)
      - [x] 8.2.16 `manager_process_supervision.py` → `ProcessSupervisionMixin`
            (27 thin wrappers; ~25 Manager forwarders deleted)

## Phase 8 Post-mortem (2026-06-02)

The plan estimated **−1500 LOC**. Actual delivery: **+1445 LOC** across
`src/`. The plan was wrong about three things:

1. **Mixin overhead is significant.** Each migrated mixin pays
   ~30–100 LOC for: Protocol-import block, owned-state class-level
   annotations (so mypy can type-check method bodies), mixin class
   docstring, optional module-level trampolines for tests that
   import directly. Across 16 mixins this overhead totalled **+1542 LOC**.

2. **Cross-mixin contracts moved into `manager_protocol.py`** (+242 LOC) —
   a new file documenting every cross-mixin method signature. Necessary
   for mypy to type-check mixin method bodies that call sibling-mixin
   methods. Bought a CI drift test (`tests/test_manager_protocol.py`,
   +98 LOC) that catches signature divergence.

3. **`manager.py` shed only -97 LOC** despite removing 200+ forwarder
   methods. The shrink was offset by: mixin import block, the 16-mixin
   class header MRO list, Phase 9 init scaffolding
   (`ManagerSockets` / `ManagerCaches` / `LifecycleExecutor`), Phase 3
   explicit attribute initialization, Phase 10 deprecation aliases, and
   keeping ~10 Manager forwarders that bind Manager-module enums the
   mixins can't reach without circular imports.

### What Phase 8 actually bought

- **Type safety**: every cross-mixin call is now type-checked against
  `ManagerProtocol`. Before, sibling methods were untyped `manager.X`
  reaches. Drift test catches Protocol divergence.
- **Owned-state discipline**: each mixin declares the Manager state it
  reads as class-level annotations. Drift test
  (`tests/test_mixin_owned_state.py`) caught a real `list` vs `deque`
  annotation mismatch on first run.
- **Forwarder method removal**: ~125 one-line `def _foo: return shared_foo(self, ...)`
  methods deleted from `Manager`. The class definition is shorter and
  the dispatch is now declarative (via MRO) rather than imperative.
- **Module-level helper preservation**: tests that imported helpers
  directly continue to work via thin trampolines, avoiding test churn.

### What Phase 8 didn't buy

- LOC reduction. The mixin pattern in this codebase costs more LOC
  than it saves because Python's typing system needs explicit
  annotations that wouldn't be needed in a language with structural
  subtyping built in.
- Runtime behavior change. Phase 8 was a pure structural refactor;
  no observable behavior changed (verified by 597 tests passing at
  baseline throughout).

### Recommendation for future similar refactors

If the goal is **LOC reduction**, the mixin pattern is the wrong tool.
The right tool is to **collapse module boundaries** — fold helper
modules back into `Manager` directly, eliminating the import indirection.

If the goal is **type-checked cross-module dispatch with drift
guards**, the mixin + Protocol pattern landed here is the right tool.
It costs LOC but pays in correctness.
        (~1450 LOC, 26 mixin candidates, last)
      - ~~`manager_process_spec.py`, `manager_config.py`, `manager_client.py`~~:
        no mixin candidates; left as module-level helpers.
- [ ] **8.3** Now apply the deferred Phase 1.4 cleanup: extract a shared
      base class (or just shared field block via composition) for
      `DeviceHandle`/`ProcessHandle` in `manager_models.py` — the
      reordering is safe now because all construction is via keyword args
      from `Manager.__init__` only.
- [ ] **8.4** Add contract test that imports `Manager`, instantiates with
      mocks, and confirms `call`, `get_latest`, `drain_telemetry`,
      `publish_event` exist with expected signatures.
- [ ] **8.5** Run downstream smoke test.

---

## Phase 9 — `Manager.__init__` decomposition ✅ COMPLETE (2026-06-01)

**Risk:** Low (pure internal). **Est. LOC delta:** -100.

After Phase 8, `Manager.__init__` is still ~315 lines doing too much.
Extract subsystem constructors.

- [x] **9.1** Extract `ManagerSockets` dataclass that owns the 5 ZMQ
      sockets and their endpoints. `Manager.__init__` instantiates it once.
- [x] **9.2** Extract `ManagerCaches` dataclass owning dict/lru caches
      currently initialized inline.
- [x] **9.3** Extract `ManagerJournal.start_or_disabled(...)` factory
      classmethod (replaces the conditional journal-construction logic).
- [x] **9.4** Extract `LifecycleExecutor` class owning the ThreadPoolExecutor
      + lifecycle worker queue.
- [~] **9.5** `Manager.__init__` reduces to ~50 lines of orchestration. Constructor now delegates sockets, caches, journal startup, and lifecycle executor setup; remaining inline setup preserved for lower-risk incremental change.
- [x] **9.6** Run validation suite. `ruff` passed; complexity guard passed; focused manager lifecycle tests passed; `mypy` remained at the documented 114-error baseline; full unittest remained at the documented 1-error baseline.

---

## Phase 10 — Coordinated rename pass (cross-repo) ⚠️ IMPLEMENTED, DEPRECATION SHIMS RETAINED (2026-06-01)

**Risk:** Medium (touches both repos in lockstep). **Est. LOC delta:** ~0.

Goal: drop the awkward underscore prefixes on what are really public hooks.
This is one coordinated PR pair (upstream + downstream).

### Renames

On `experiment_control.processes.process_base.ManagedProcessBase` (inherited
by `StateMachineProcessBase`):

| Old name | New name |
|---|---|
| `_rpc_ok` | `rpc_ok` |
| `_rpc_err` | `rpc_err` |
| `_rpc_invalid_params` | `rpc_invalid_params` |
| `_rpc_unknown` | `rpc_unknown` |
| `_command` | `command` |
| `_publish_transition_event` | `publish_transition_event` |
| `_append_run_event` | `append_run_event` |
| `_handle_state_machine_rpc` | `handle_state_machine_rpc` |
| `_last_transition` (attr) | `last_transition` |
| `_allowed_transitions` (cls attr) | `allowed_transitions` |
| `_sequence_error_cls` (cls attr) | `sequence_error_cls` |

### Add typed Manager Protocol

- [x] **10.1** Define `class ManagerProtocol(typing.Protocol)` in
      `src/experiment_control/client/protocol.py` with the 4 method
      signatures `call`, `get_latest`, `drain_telemetry`, `publish_event`.
- [x] **10.2** Update `ManagerClientHelper` to be typed against `ManagerProtocol`.

### Upstream PR (this repo)

- [x] **10.3** Apply all renames in
      `src/experiment_control/processes/process_base.py` and
      `src/experiment_control/processes/state_machine_base.py`. Keep the
      old names as thin properties/methods that delegate to the new names,
      with `DeprecationWarning`, for ONE release cycle.
- [x] **10.4** Update all internal callers (processes/, sequencer/, tests/)
      to use new names.
- [x] **10.5** Update `AGENTS.md` contract list.

### Downstream PR (`centrex-experimental-stack`)

- [x] **10.6** Replace all `self._rpc_ok(...)` / `self._rpc_err(...)` /
      `self._rpc_invalid_params(...)` / `self._rpc_unknown(...)` calls
      across the 8 subclass files (~250 sites) with the new names.
      Files: `thermal_procedure_process.py`, `state_preparation.py`,
      `spa.py`, `spb_microwave.py`, `rotational_cooling_microwave.py`,
      `electrostatic_lens.py`, `laser_lock_freq_nltl_power.py`,
      `frequency_step_guard.py`.
- [x] **10.7** Update `_publish_transition_event` override in
      `thermal_procedure_process.py:501-503`.
- [x] **10.8** Update `_allowed_transitions` / `_last_transition` reads
      (`thermal_procedure_process.py:464,1919`, test files).
- [x] **10.9** Update test `_derive_allowed_transitions_from_graph_edges`
      call (`test_thermal_procedure_sim.py:304`, `test_spa.py:58`).
- [x] **10.10** Update `FakeManager`/`FakeSimManager`/`_FakeManager` to
      implement `ManagerProtocol` (`shared/python/centrex_shared/testing/fake_manager.py`,
      `instances/vacuum-cryo/tests/test_thermal_procedure_sim.py:39-107`,
      `instances/rotational-cooling/tests/test_rotational_cooling_microwave.py:13`).

### Land both together

- [ ] **10.11** Tag upstream release; bump downstream `experiment_control`
      dependency.
- [ ] **10.12** Remove deprecation shims one release later.

---

## Summary

| Phase | Risk | Est. LOC delta | Sessions | Downstream change |
|-------|------|---------------:|---------:|-------------------|
| 1     | Low  | -43            | 1 (done) | none |
| 2     | Low  | -150           | 1 (done) | none |
| 3     | Low  | -200           | 1        | none |
| 4     | Med  | -80            | 1        | none (snapshot WS) |
| 5     | Med  | ~0             | 1-2      | none (shape unchanged) |
| 6     | Low  | -40            | 1        | 2-line edit in `LaserLockCompatible.py` |
| 7     | Med  | -600           | 1-2      | none |
| 8     | Med  | -1500          | 3-5      | none |
| 9     | Low  | -100           | 1        | none |
| 10    | Med  | ~0             | 1        | rename ~250 call sites |
| **Total** | | **~-2700** | **~13** | 1 surgical edit + 1 rename PR |

## Per-phase exit criteria

For every phase to be considered "complete":

1. `uv run ruff check src tests examples` passes.
2. `uv run mypy src/experiment_control` shows ≤114 errors (baseline).
3. `uv run python -m unittest discover -s tests -p "test_*.py" -q` shows
   ≤1 failure (the pre-existing `test_latch_survives_no_telemetry_signals_tick`).
4. (Phases 4-8) Downstream smoke tests pass.
5. Each step in the phase has its own commit; the phase ends with a clean
   `git status`.
