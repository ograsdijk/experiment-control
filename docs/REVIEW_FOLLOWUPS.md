# Review followups — remaining work

This file captures the work that came out of the May 2026 thorough
code review but **wasn't bundled into the 10 open review-driven PRs**
(#48–#57). Each entry has enough context that a future contributor
can pick it up cold.

PRs already opened from this review track are summarised in the
appendix at the bottom for cross-reference.

---

## 1. Open code-quality items (concrete bugs)

These were identified in the original review but deliberately deferred
because they each need a small design decision that doesn't fit the
"surgical fix" pattern of the existing PRs.

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

**Why not yet fixed.** Needs a "staleness" design choice:

* Add a `last_fit_ts_mono` field and let the UI compute staleness
  itself; payload schema change.
* Add a `last_fit_stale: bool` flag set when the most recent fit
  attempt failed; payload schema change.
* Invalidate `last_fit` after N consecutive failures and let the
  field go to `null`; arguably the cleanest but changes behaviour
  for consumers that explicitly want "last known good".

**Suggested approach.** Add `last_fit_attempt_ts_mono` AND
`last_fit_success_ts_mono` to the payload; let the UI compute "fit
is stale because attempt > success by > threshold". Backward-compatible
(additive fields).

**Effort.** Small — touches 1 fit-cache class and the workspace
serializer. Needs a passing UI rendering update at the same time
(otherwise operators don't see the new fields).

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

**Why not yet fixed.** Needs per-destination state:

* A `dict[DestinationName, RetryState]` where `RetryState` carries
  `next_attempt_mono` and `consecutive_429s`.
* The bg loop has to skip a destination whose `next_attempt_mono` is
  in the future, while still processing other destinations.
* `Retry-After` parsing: handle both integer-seconds and HTTP-date
  formats per RFC 9110 §10.2.3.

**Suggested approach.** Add a tiny `BackoffPolicy` class with
`next_delay(status_code, retry_after_header) -> float`. Default
exponential backoff if `Retry-After` is missing (e.g. `min(60,
2 ** consecutive_failures)`). Reset on first successful write.

**Effort.** Medium — needs the new state container, plumbing in
`_http_thread_run`, and tests with a mock HTTP server that returns
429 with various `Retry-After` shapes.

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

**Why not yet fixed.** Needs a default `window_s` decision:

* Cap at a hard maximum (e.g. 10 000 samples) regardless of
  configuration?
* Apply a default `window_s` (e.g. `60.0`) when the step doesn't
  specify one?
* Switch to a `collections.deque(maxlen=...)` so old samples are
  dropped silently?

Each has trade-offs for sequence YAMLs that genuinely want "the
median of every sample seen during the wait".

**Suggested approach.** Default to `deque(maxlen=10000)` with a
runtime warning when the cap is hit; let the operator opt into a
larger cap via `reduce.max_samples`. Documented behaviour change
in `docs/sequencer.md`.

**Effort.** Small in code; medium in deciding the default + writing
the migration note.

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

**Why not yet fixed.** PR #52 isn't on master yet; the
`_make_condition_error_callback` hook + `on_condition_error=` keyword
don't exist on master, so there's no integration point on a
master-based branch.

**Suggested approach** (once PR #52 lands). Queue the publish onto a
daemon thread (or onto the watchdog's existing single-worker
`ThreadPoolExecutor` if it makes sense to share — they're in
different processes though). Fire-and-forget; if the manager is
unresponsive, drops are acceptable for observability events.

**Effort.** Small — ~30-line addition to the InterlockProcess + a
test that asserts the interceptor RPC doesn't block on the publish.

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

**Why not yet done.** Large PR with high coordination cost across the
10 in-flight PRs. The dup-block in `manager.py:3466` (60 lines, fixed
in PR #48 commit `690cc7f`) is what triggered the "this file is hard
to navigate" observation in the first place — once #48 lands the
file is smaller.

**Suggested approach.**

1. Create `manager/` as a package with `__init__.py` re-exporting
   the `Manager` class.
2. Split into ~5–8 modules: `manager/core.py` (Manager class + main
   loop), `manager/sockets.py` (zmq context + socket lifecycle),
   `manager/handles.py` (DeviceHandle / ProcessHandle), etc.
3. Each split is a separate commit so reviewers can `git log -p`
   one file at a time.
4. No behaviour change; pure code organisation.

**Effort.** Large. ~1 week of focused work + a downstream smoke run
to verify nothing imports from a now-moved location.

---

### 2.2 `stream_analysis.py` package split

**Original review note:** same paragraph.

**Problem.** `processes/stream_analysis.py` is the largest file in
the repo (~5 500 lines). It has at least 7 distinct concerns:
workspaces, fitting, bin stats, snapshot publishing, source nodes,
validation, RPC dispatch. Cross-concern bugs are hard to spot.

**Suggested approach.** Same shape as 2.1 — make
`processes/stream_analysis/` a package with files per concern.
Pin the existing `_NodeValidator` Protocol from PR #55 in the
public surface.

**Effort.** Large. Hardest of the four splits because the in-file
imports are tangled.

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

**Effort.** Medium — file is large but the concerns are already
relatively well-separated.

---

### 2.4 `tui_manager.py` package split

**Original review note:** same paragraph.

**Problem.** `tui_manager.py` is ~2 800 lines mixing TUI screens,
actions, RPC client, capabilities cache. The `_on_dismiss`
duplicate-name pattern across nested closures was specifically
called out as making the file hard to navigate.

**Suggested approach.** Make `tui/` a package:
* `tui/app.py` — `ManagerTUI`
* `tui/screens/` — one file per modal screen (`ConfirmScreen` etc.)
* `tui/actions.py` — `action_*` methods (can stay on ManagerTUI but
  extracted as mixin)
* `tui/rpc.py` — the `_rpc_call` / `_pub_thread` helpers
* `tui/state.py` — `_device_status` / `_processes` etc. caches

**Effort.** Medium. Coupled with the deferred TUI worker conversion
(now landed in PR #56 as F.19) so the worker pattern survives the
refactor.

---

## 3. Open operational work

### 3.1 Centrex downstream smoke matrix

**Origin.** Each of the 10 open PRs has a "Downstream impact
(centrex-experimental-stack)" table in its description; the entries
were verified by static analysis (grep + AST inspection) but not by
running the centrex test suite against each branch.

**Suggested workflow** for each open PR:

```bash
# In centrex-experimental-stack worktree
git checkout main
git pull
# Point centrex at the PR branch via the local clone
uv pip install -e ../experiment-control@<branch-name>
# Run the smoke matrix
pytest instances/electrostatic-lens/tests
pytest instances/state-preparation-b-detection/tests
pytest instances/state-preparation/tests
pytest instances/vacuum-cryo/tests
```

These four instances exercise the entire downstream public API
surface my PRs touch (`evaluate_*_rule`, `collect_rulesets`,
`StateMachineProcessBase`, the state-machine subclasses,
`ManagerClient`, `process_base`).

**Effort.** Small per PR (~5 min to install + run); medium total
for all 10 PRs (~1 hour).

---

### 3.2 PR merge ordering + conflict resolution

**Origin.** All 10 PRs branch off the same `master` commit; some
touch overlapping files.

**Known overlap:**

* **#54 ↔ #56** both touch `processes/state_machine_base.py`:
  - #54's `cb4e78e` adds the F.34 record-into-`_last_error`.
  - #56's `b14c4d1` supersedes the F.34 record AND adds the
    matching clear-on-success path.
  - **Resolution:** whichever lands first wins; the other PR needs
    a one-hunk rebase to drop the duplicated record block. The clear
    path is unique to #56 either way.

* **#54 ↔ #57** both touch `processes/influx_writer.py`:
  - #54 doesn't touch this file (re-check).
  - #57 adds the `_signals_skipped_invalid` counter + status RPC.
  - No overlap expected; verify on rebase.

* **#48 ↔ #56** both touch `manager.py`:
  - #48 deletes the dup-block at `manager.py:3466-3524` (60 lines).
  - #56 adds `_CLOSE_RPC_LOCK_WAIT_S` and the bounded-acquire
    `_close_*_rpc` methods.
  - No real conflict (different parts of the file); a clean
    rebase will likely succeed.

**Suggested merge order** (least → most coupled):

1. #48 (Group A — trivial fixes; only deletes things + small
   surgical fixes)
2. #50 (Group D — HDF; isolated to `hdf_writer.py`)
3. #53 (Group G — sequencer / stream_analysis / shm; isolated)
4. #55 (Group H — mypy hygiene; touches many files but only
   adds helpers / standardises signatures)
5. #49 (Group C — telemetry; additive payload)
6. #51 (Group B — DEALER correlation)
7. #52 (Group E — watchdog/interlock; unblocks 1.4 above)
8. #54 (Group F — manager hardening)
9. #56 (Deferred cleanups; rebase on #54 to drop the
   duplicated state_machine_base hunk)
10. #57 (Misc review followups; rebase on #54 if needed)

After #52 lands, item 1.4 above becomes actionable as a new small
PR.

**Effort.** ~30 min per PR if there's a rebase needed; less if not.

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
| [#48](https://github.com/ograsdijk/experiment-control/pull/48) | Group A — trivial fixes + federation spoofing | +15 | Open |
| [#49](https://github.com/ograsdijk/experiment-control/pull/49) | Group C — telemetry observability (closes ISSUES.md) | +14 | Open |
| [#50](https://github.com/ograsdijk/experiment-control/pull/50) | Group D — HDF lock coverage | +7 | Open |
| [#51](https://github.com/ograsdijk/experiment-control/pull/51) | Group B — DEALER correlation + server-side echo | +12 | Open |
| [#52](https://github.com/ograsdijk/experiment-control/pull/52) | Group E — watchdog/interlock observability | +12 | Open |
| [#53](https://github.com/ograsdijk/experiment-control/pull/53) | Group G — SEM/sequencer/shm_ring | +10 | Open |
| [#54](https://github.com/ograsdijk/experiment-control/pull/54) | Group F — manager hardening | +17 | Open |
| [#55](https://github.com/ograsdijk/experiment-control/pull/55) | Group H — mypy hygiene | +15 | Open |
| [#56](https://github.com/ograsdijk/experiment-control/pull/56) | Deferred cleanups (F.25 + F.19 + sticky-error reset) | +12 | Open |
| [#57](https://github.com/ograsdijk/experiment-control/pull/57) | Misc review followups (#48 + #24 + #20a + #47) | +19 | Open |

Cumulative across all 10: ~133 new tests (vs 427 baseline). Zero
regressions on any branch. Ruff + CI complexity guard + mojibake
clean across the board.
