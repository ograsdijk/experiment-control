# Review followups — remaining work

This file captures the work that came out of the May 2026 thorough
code review but **wasn't bundled into the 10 open review-driven PRs**
(#48–#57). Each entry has enough context that a future contributor
can pick it up cold.

PRs already opened from this review track are summarised in the
appendix at the bottom for cross-reference.

---

## 1. Resolved code-quality items (concrete bugs)

These were identified in the original review and have now been addressed
on top of the merged May 2026 review track.

### 1.1 `stream_analysis` `last_fit` becomes stale silently

**Original review item:** #21.

**Location:** `src/experiment_control/processes/stream_analysis.py` —
the fit-state cache that backs the workspace's `last_fit` output.

**Problem.** A successful fit populates `last_fit`. If the next batch
of telemetry produces an unfittable input (NaN, scipy `curve_fit`
raises `OptimizeWarning`, etc.), the cached `last_fit` is left in
place unchanged. UI consumers reading the workspace see the prior
fit's parameters and have no way to tell "this is a fresh fit" from
"this is the last successful fit from N minutes ago, the current data
is unfittable".

**Resolution.** `FitCurve1DState` now stamps `last_fit_attempt_ts_mono`
and `last_fit_success_ts_mono` into the retained fit payload. Failed
attempts preserve the last known good fit while advancing the attempt
timestamp, so consumers can detect stale fits without losing backward
compatibility.

---

### 1.2 InfluxDB `429`/`503` retry doesn't honour `Retry-After`

**Original review item:** #20b.

**Location:**
`src/experiment_control/processes/influx_writer.py:944-965` (the
`HTTPError` handling in `_flush_destination_batch`) and the
re-queue loop at `_http_thread_run:1007-1043`.

**Problem.** On `HTTPError` from the InfluxDB write endpoint, the
writer records `_write_errors += 1`, sets `_last_error`, returns
`False`, and the bg thread immediately re-queues for the next loop
iteration. There's no per-destination backoff and no honouring of
the `Retry-After` header. On a rate-limited InfluxDB instance the
writer would hammer the endpoint at full speed.

**Resolution.** The writer now tracks per-destination retry state,
honours `Retry-After` as integer seconds or HTTP-date, falls back to
bounded exponential backoff, skips destinations still in backoff while
continuing to flush other destinations, and resets state on success.

---

### 1.3 `_step_wait_until` grows samples unbounded without `reduce.window_s`

**Original review item:** #46.

**Location:** `src/experiment_control/sequencer/runtime.py`, the
`_step_wait_until` implementation.

**Problem.** `_step_wait_until` samples telemetry at the configured
cadence and accumulates values into a list. When the step uses a
`reduce:` block with a `window_s` parameter, the list is trimmed.
**Without `window_s`**, the list grows indefinitely while the wait
condition is false. A long-running wait (waiting for cryogenic
temperature equilibration, slow lock acquire, etc.) can accumulate
hundreds of thousands of samples and exhaust memory.

**Resolution.** `wait_until.reduce.max_samples` now caps retained
samples, defaulting to 10 000 when no explicit cap is provided. The
cap is applied alongside any `window_s` trimming and the `samples`
implicit variable reflects the retained tail.

---

### 1.4 Interlock `rule_error` publish off the command-interceptor critical path

**Original review item:** Local review of PR #52 (Group E watchdog/
interlock observability) — deferred until PR #52 lands.

**Location:** Will be on
`src/experiment_control/processes/interlock.py` once PR #52 is
merged. The relevant call site is the new
`_make_condition_error_callback` in `InterlockProcess`, which
synchronously calls `ManagerClientHelper.publish_event` from inside
the command-interceptor RPC handler.

**Problem.** When an interlock rule's condition expression raises
(e.g. typo'd YAML field name), the callback publishes a
`manager.interlock.rule_error` event via a blocking DEALER round-trip
to the manager. This adds up to ~100 ms (healthy) / ~1500 ms (manager
unresponsive) latency to the rule's rejection response. The rate
limit (1/30s per (interceptor_id, rule_name)) caps the impact but
the first occurrence per window still blocks the upstream command's
rejection reply.

**Resolution.** The callback now rate-limits synchronously, submits the
`manager.interlock.rule_error` publish to a single-worker executor, and
returns immediately. Shutdown cancels pending observability publishes.

---

## 2. Open structural work

### 2.1 `manager.py` package split

**Original review note:** observed at the end of the consolidated
review report.

**Problem.** `manager.py` is currently ~3 100 lines after the dup-block
removal in PR #48. It's a god-object that mixes lifecycle, RPC,
device routing, process supervision, pubsub, log forwarding, and
runtime metadata. The recent in-tree refactor (`manager_*` siblings:
`manager_lifecycle.py`, `manager_pubsub.py`, `manager_rpc_calls.py`,
`manager_route_handlers.py`, `manager_internal_rpc.py`,
`manager_device_routing.py`, `manager_process_supervision.py`,
`manager_process_logs.py`, `manager_runtime_metadata.py`, ...)
moved most logic out, but `manager.py` is still the orchestrator
holding sockets and most state.

**Resolution.** Public imports remain on `experiment_control.manager`,
while model/config concerns were split into `manager_models.py` and
`manager_config.py`. The existing `manager_*` sibling modules continue
to hold lifecycle, routing, pubsub, supervision, logs, and runtime
metadata logic.

---

### 2.2 `stream_analysis.py` package split

**Original review note:** same paragraph.

**Problem.** `processes/stream_analysis.py` is the largest file in
the repo (~5 500 lines). It has at least 7 distinct concerns:
workspaces, fitting, bin stats, snapshot publishing, source nodes,
validation, RPC dispatch. Cross-concern bugs are hard to spot.

**Resolution.** Fit-related state, validation, execution, and hist-fit
helpers were split into `processes/stream_analysis_fit.py` and
re-exported from `processes/stream_analysis.py`, preserving the existing
public import path while reducing the largest file's mixed concerns.

---

### 2.3 `hdf_writer.py` package split

**Original review note:** same paragraph.

**Problem.** `processes/hdf_writer.py` is ~4 000 lines. Recent
PRs touched it heavily (bg flush thread foundation, lock-coverage
fix in PR #50, super().close() delegation, rotate-failure cleanup,
unlocked-write audit). The file would benefit from being broken
into:

* `hdf_writer/core.py` — `HdfWriter` class + main loop
* `hdf_writer/bg_flush.py` — bg thread + queue + drain
* `hdf_writer/file_lifecycle.py` — rotate / start / stop
* `hdf_writer/datasets.py` — per-dataset write helpers
* `hdf_writer/measurement.py` — measurement-note table + metadata

**Resolution.** Background request/queue payload types moved to
`processes/hdf_writer_bg.py`, and HDF dtype/dataset helper functions
moved to `processes/hdf_writer_dtypes.py`. `processes/hdf_writer.py`
continues to expose `HdfWriter` and orchestrate the process.

---

### 2.4 `tui_manager.py` package split

**Original review note:** same paragraph.

**Problem.** `tui_manager.py` is ~2 800 lines mixing TUI screens,
actions, RPC client, capabilities cache. The `_on_dismiss`
duplicate-name pattern across nested closures was specifically
called out as making the file hard to navigate.

**Resolution.** `DeviceStatus` moved to `tui_models.py`, and modal
screens moved to `tui_screens.py`. `tui_manager.py` remains the public
entry point for `ManagerTUI` and keeps action/RPC orchestration in one
place for compatibility.

---

## 3. Open operational work

### 3.1 Centrex downstream smoke matrix

**Origin.** Each of the 10 open PRs has a "Downstream impact
(centrex-experimental-stack)" table in its description; the entries
were verified by static analysis (grep + AST inspection) but not by
running the centrex test suite against each branch.

**Status.** The downstream checkout exists at
`../centrex-experimental-stack`, but the smoke matrix could not be run
because dependency resolution is currently unsatisfiable: `linien-client
==2.1.0` requires `numpy<2`, while `experiment-control==0.2.0` requires
`numpy>=2.4.1`. The attempted command was:

```bash
uv pip install -e ../experiment-control
uv run pytest instances/electrostatic-lens/tests \
  instances/state-preparation-b-detection/tests \
  instances/state-preparation/tests \
  instances/vacuum-cryo/tests
```

Once the downstream environment pins are reconciled, this smoke matrix
should be rerun.

---

### 3.2 PR merge ordering + conflict resolution

Resolved. PRs #48–#58 are merged. PR #56 needed one conflict-resolution
merge after #54 landed; the remaining PRs were clean after GitHub
recomputed mergeability.

---

## 4. Open documentation work

### 4.1 Document the new heartbeat / telemetry / log event shapes

The 10 PRs add several new optional fields to wire payloads:

| Topic | New field | Added in |
|---|---|---|
| `{device_id}/telemetry` | `call_errors: dict[str, str]` | PR #49 |
| `{device_id}/telemetry` | per-signal `error: str` (on BAD signals) | PR #49 |
| `manager.telemetry_update` | `call_errors` forwarded verbatim | PR #49 |
| `manager.process.failed` (was `.exited`) | new transition path | PR #54 |
| `manager.process.failed` | `last_failure_pid` populated on stop-detected crash | PR #56 |
| `manager.lifecycle.events_dropped` | new event topic | PR #54 |
| `manager.watchdog.rule_error` | new event topic | PR #52 |
| `manager.interlock.rule_error` | new event topic | PR #52 |
| `manager.watchdog.action_chain_error` | new event topic | PR #52 |

`docs/protocol.md` was updated for the PR #49 telemetry fields. The
rest still need entries in the topic catalogue.

**Effort.** Small — one section per topic in `docs/protocol.md`.

---

### 4.2 Operator-facing changelog

The 10 PRs change operator-visible behaviour in several places:

* CTC100 (and any other telemetry-call-failing device) now publishes
  actionable diagnostics instead of stale-or-OK signals (PR #49)
* Federation `relay.only_mirrored_devices=False` actually works now
  (PR #48 A.3); operators who had set it to `False` expecting it to
  be a no-op will start seeing peer events on the local bus
* TUI bulk start/stop no longer freezes (PR #56 F.19)
* Watchdog actions no longer wedge the tick on a slow remediation
  (PR #52 E.16)
* Misconfigured rules now produce `rule_error` events on the bus
  (PR #52 E.18)
* InfluxDB writer now logs which signals are being dropped (PR #57
  #20a)
* Sequencer `range:` with wrong-sign step now raises instead of
  silently producing an empty list (PR #57 #47)

Operators should know about the federation flag (1) before
upgrading; everything else is strict improvement.

**Effort.** Small — single `notes/CHANGELOG-<date>.md` page or an
entry per PR in a shared CHANGELOG.md.

---

## Appendix: open review PRs (cross-reference)

10 PRs landed from the May 2026 review track. All have passed local
review.

| PR | Title (short) | Tests | Status |
|---|---|---|---|
| [#48](https://github.com/ograsdijk/experiment-control/pull/48) | Group A — trivial fixes + federation spoofing | +15 | Merged |
| [#49](https://github.com/ograsdijk/experiment-control/pull/49) | Group C — telemetry observability (closes ISSUES.md) | +14 | Merged |
| [#50](https://github.com/ograsdijk/experiment-control/pull/50) | Group D — HDF lock coverage | +7 | Merged |
| [#51](https://github.com/ograsdijk/experiment-control/pull/51) | Group B — DEALER correlation + server-side echo | +12 | Merged |
| [#52](https://github.com/ograsdijk/experiment-control/pull/52) | Group E — watchdog/interlock observability | +12 | Merged |
| [#53](https://github.com/ograsdijk/experiment-control/pull/53) | Group G — SEM/sequencer/shm_ring | +10 | Merged |
| [#54](https://github.com/ograsdijk/experiment-control/pull/54) | Group F — manager hardening | +17 | Merged |
| [#55](https://github.com/ograsdijk/experiment-control/pull/55) | Group H — mypy hygiene | +15 | Merged |
| [#56](https://github.com/ograsdijk/experiment-control/pull/56) | Deferred cleanups (F.25 + F.19 + sticky-error reset) | +12 | Merged |
| [#57](https://github.com/ograsdijk/experiment-control/pull/57) | Misc review followups (#48 + #24 + #20a + #47) | +19 | Merged |

Cumulative across all 10: ~133 new tests (vs 427 baseline). Zero
regressions on any branch. Ruff + CI complexity guard + mojibake
clean across the board.
