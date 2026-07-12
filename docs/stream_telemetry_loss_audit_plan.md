# Stream & telemetry loss audit — remaining-items verification & plan

Companion to `docs/stream_telemetry_loss_audit.md`. That doc's §5 and §9
listed a set of proposed instrumentation/robustness fixes; the structured-
dtype schema poisoning (§10) and the torn-slot race (table row #20) have
since been fixed and verified (commits `14ae1ca`, `eb54477`). This doc
covers the rest: a 2026-07-11 re-verification (via a fresh read of current
source, not trusting the doc's line numbers, which drift) of every item
still marked open, followed by a concrete implementation plan for whatever
is still a real gap.

---

## Phase 1 — verification

Verdicts below, each with the concrete code that decides it.

### §5 items

**Item 1 — driver missed scheduled ticks — VALID (still open).**
`DeviceRunner._publish_scheduled_streams` (`src/experiment_control/_driver/runner.py`,
~line 1219) computes `missed = max(0, int((now - plan.next_due_s) // plan.period_s))`
and advances `plan.next_due_s += (missed + 1) * plan.period_s` (lines
1225-1226) but never records `missed` anywhere. The heartbeat payload
(`_publish_heartbeat`, lines 1103-1116) carries only `loop_lag_s`,
`device_state`, `last_error` — no missed-tick counter.

**Item 2 — produced-count in heartbeat — VALID.**
The heartbeat payload (lines 1103-1116) has no per-stream produced-seq
field. `publish_stream` (line 1329) computes `seq` from `writer.write(...)`
and returns it in the descriptor but stores nothing on the runner. Nothing
lets a consumer diff produced-vs-written.

**Item 3 — heartbeat vs blocking device calls — VALID (design gap).**
`DeviceRunner.run()` (lines 208-279) is a single-threaded poll loop;
`_publish_heartbeat` is called inline (line 271), so any blocking device
RPC/stream/telemetry call stalls the heartbeat. On the manager side
`enforce_device_driver_heartbeat_timeout` (`_manager/process_supervision.py`,
lines 965-1026) ages against `manager._heartbeat_timeout_s` (a single
global, set once in `manager.py:346` from `models.py:216` default `3.0`)
with `_heartbeat_hard_timeout_multiplier=3.0` (manager.py:436) → hard kill
at ~9 s. There is **no per-device driver heartbeat-timeout override** (the
per-`handle.spec.heartbeat_timeout_s` at process_supervision.py:1557 is for
*managed processes*, not device drivers). An FS740 `fetch_timeout_s=10 s`
wait is therefore unreachable — the driver is terminated first.

**Item 4 — buffer-discard on re-attach/reset not counted — VALID.**
`_ensure_chunk_ready_reader` (hdf_writer.py) sets
`self._stream_dropped_total[key] = 0` (line 4527) then
`self._stream_buffers.pop(key, None)` (line 4528) — unflushed frames
discarded, uncounted, and the counter is even zeroed the line before.
`_reset_stream_runtime_state` (line 4472) does
`self._stream_buffers.pop(key, None)` (line 4475) with no accounting.
Confirmed at both sites.

**Item 5 — per-frame payload-size drop clears whole batch — VALID.**
`_write_single_stream_buffer`, lines 5245-5253:
`if any(len(payload) != expected_nbytes for payload in data_list):` bumps
`payload_size_failures_total` by 1 and `self._clear_stream_buffer(buf)` —
the entire batch is dropped, good frames included, counter += 1 regardless
of how many were bad.

**Item 6 — parse failures & dtype/shape-unknown drops uncounted — VALID
(both sub-cases).**
(a) `_handle_manager_chunk_ready` (`hdf_writer_topics.py` lines 63-67):
`ChunkReadyMessage.parse(msg)` → `None` → silent `return`, no counter.
(b) `_write_single_stream_buffer` lines 5200-5202:
`if dtype_raw is None or shape_raw is None: self._clear_stream_buffer(buf); return`
— silent, no counter.

**Item 7 — telemetry device-missing-from-schema — VALID.**
`_write_buffered_rows_batch` line 4329:
`if not self._ensure_device(device_id): continue` — silent row skip, no
counter. `_ensure_device` (lines 3239-3260) re-fires
`_schema_rpc(...)` every call when the device isn't in `self._datasets` and
only bumps `schema.rpc` on *exception* — a device merely absent from schema
causes a fresh RPC every batch with no negative-result caching.

**Item 8 — manager dedup key ignores shm_name — VALID.**
`ingest_chunk_ready` (`_manager/driver_pub.py` lines 727-731): dedup is
`if isinstance(previous, dict) and previous.get("seq") == seq: return` —
compares `seq` only. `_store_chunk_descriptor` (line 160) stores the full
desc including `shm_name`, so the data to compare is available but unused.

**Item 9 — per-topic lifecycle drop counter — VALID but reduced severity.**
`_publish_manager_event` (`_manager/pubsub.py` lines 103-109): on
`queue.Full` it bumps only the aggregate `self._lifecycle_event_dropped`;
`_drain_lifecycle_events` (manager.py:1768-1785) emits one aggregate
`manager.lifecycle.events_dropped` with a total count, no per-topic split.
**Caveat:** the doc's motivating scenario (a dropped `manager.chunk_ready`
ingested during an off-thread RPC pump) is now largely unreachable — see
§9 item 2 below — so this is now an observability nicety, not a data-loss
localizer. `manager.chunk_ready` is also *not* in `_AUDIT_TOPICS`
(pubsub.py:33-39, which holds only the three `manager.command*` topics), so
it would still be a `put_nowait` drop candidate *if* it were ever published
off-thread.

**Item 11 — seq-gap check skips last_seq==0 — VALID.**
`_append_chunk_ready_events` line 4580:
`if last_seq and seq > last_seq + 1:`. `last_seq` is seeded to
`initial_seq - 1` on attach, but falls back to `0` when the triggering
chunk carried no seq (line 4526, and `_stream_last_seq.get(key, 0)` default
at line 4444). With `last_seq == 0`, `if last_seq` is falsy, so a gap
immediately after a no-seq attach is never counted.

### §9 items

**Item 2 — worker-thread drain of `manager._sub` — NOT A VALID GAP ANYMORE
(already fixed).**
`_pump_manager_subscriptions` (`_manager/rpc_calls.py` lines 150-160) now
opens with:
```python
if threading.get_ident() != self._main_thread_id:
    return
```
So when `_call_device_rpc` runs on a lifecycle worker thread and calls
`_blocking_call_with_pump(..., pump_fn=self._pump_manager_subscriptions)`
(lines 267-273), the pump is a no-op on that thread — it never touches
`self._sub`/`_process_hb_sub`/`_process_data_sub`. The concurrent-`recv`
ZMQ thread-safety violation the doc describes cannot occur. This has
already been corrected in `docs/stream_telemetry_loss_audit.md` (§9 item 2
now marked DONE).

**Item 4 — writer process telemetry publishes no real seq — VALID.**
`_publish_writing_active_telemetry` (hdf_writer.py lines 1724-1775) builds
the `manager.process_telemetry_update` payload with `version`, `signals`,
`ts` — **no `seq` field**. Downstream, `_write_buffered_rows_batch` reads
`seq = int(msg.get("seq", -1))` (line 4344), so every writer self-telemetry
row lands with `seq = -1`; a writer restart mid-run is invisible in the
file (unlike driver telemetry, whose schema includes a real `seq`,
hdf_writer.py:240).

---

## Phase 2 — implementation plan

Naming follows existing conventions: per-stream `*_total` counters
registered in `_STREAM_COUNTER_ATTRS` (hdf_writer.py:84-94) and persisted
by `_persist_stream_attrs` (line 2247); live-only diagnostics via
`_bump_error(...)`; manager counters via `_counter_add(manager, "_...")`
(driver_pub.py:43).

### Batch A — Driver-side heartbeat & counters
`src/experiment_control/_driver/runner.py`, plus manager config for A3.

**A1 (item 1) — count missed scheduled ticks. DONE (2026-07-11).**
- In `_publish_scheduled_streams`, after computing `missed` (line 1225),
  add `self._scheduled_stream_missed_total += missed`. Initialize
  `self._scheduled_stream_missed_total = 0` in `__init__` (near line 134
  with the other seq counters).
- On the exception branch (lines 1235-1238), also record a per-plan
  last-skip reason: `self._scheduled_stream_last_error = f"{plan.action_name}: {e!r}"`
  (or reuse `_last_error`, already set).
- In `_publish_heartbeat` (payload dict, lines 1103-1116) add
  `"scheduled_stream_missed_total": self._scheduled_stream_missed_total`.
- Test (new, `tests/test_driver_runner*.py` or extend an existing driver
  test): construct a `DeviceRunner` with one `StreamCall` having
  `period_s=0.05` and a stream wrapper/handler that sleeps ~0.5 s; drive
  one loop iteration (or call `_publish_scheduled_streams` after advancing
  a fake `now`), then assert `scheduled_stream_missed_total >= 9` and that
  it appears in the heartbeat payload. Mirrors
  `docs/stream_telemetry_loss_audit.md` §6 test 7.
- **Implemented as planned**, minus the per-plan last-skip-reason field
  (the existing `_last_error`, already set on the exception branch, was
  judged sufficient — not worth a second field for this pass). Test:
  `test_missed_scheduled_stream_ticks_are_counted`
  (`tests/test_driver_stream_schedule.py`) — drives the loop 5.5 periods
  past due and asserts `scheduled_stream_missed_total == 5`.

**A2 (item 2) — publish per-stream produced seq. DONE (2026-07-11).**
- Add `self._stream_last_published_seq: dict[str, int] = {}` in
  `__init__`. In `publish_stream`, after `seq = writer.write(...)` (line
  1359) set `self._stream_last_published_seq[stream] = int(seq)`.
- In `_publish_heartbeat` add
  `"stream_last_published_seq": dict(self._stream_last_published_seq)`.
- Test: call `publish_stream` twice for a stream, assert the heartbeat
  payload's `stream_last_published_seq[stream]` equals the last returned
  seq.
- **Implemented as planned.** Tests:
  `test_publish_stream_records_last_published_seq`,
  `test_heartbeat_includes_missed_ticks_and_published_seq`
  (`tests/test_driver_stream_schedule.py`) — both pass, full suite
  (6 tests in that file) green.

**A3 (item 3) — heartbeat survives long blocking device calls. FLAG:
touches process-kill behavior. STILL OPEN — design settled 2026-07-11,
not yet implemented.**

This is the FS740-killer: `DeviceRunner.run()` (runner.py:208-279) is
single-threaded, so a long blocking device call (e.g. FS740's
`fetch_timeout_s=10 s` hardware wait) stalls the heartbeat too, and the
manager's `enforce_device_driver_heartbeat_timeout`
(process_supervision.py:965-1026) — global `heartbeat_timeout_s=3.0`,
hard-kill at ~9 s (`_heartbeat_hard_timeout_multiplier=3.0`,
manager.py:436) — kills the driver process before the call can finish.
Confirmed real, actual data loss (not just missing accounting), and the
secondary root cause investigated in the original audit alongside the
now-fixed schema poisoning.

Three options were discussed, in order of increasing correctness and
(mostly) decreasing risk once a key constraint was identified:

- *Option 1: per-device heartbeat-timeout floor.* Give device drivers the
  same per-device `heartbeat_timeout_s` capability managed processes
  already have. Thread a per-device timeout into
  `enforce_device_driver_heartbeat_timeout` (process_supervision.py:991,
  replace `manager._heartbeat_timeout_s` with
  `max(manager._heartbeat_timeout_s, handle-configured floor)`), sourced
  from the device spec / driver launch config, defaulting to
  `max(global, max_device_call_timeout + margin)`. Contained, low risk,
  but only *tolerates* the stall longer — doesn't fix the underlying
  single-threaded-blocking design, and a genuinely hung main loop is
  detected later (mitigated by keeping the hard-timeout multiplier: dead
  drivers are still reaped, just at `hard_timeout = effective_timeout * 3`
  off the raised floor).
- *Option 2: dedicated heartbeat-publishing thread in `DeviceRunner`.*
  **Rejected as originally scoped.** A thread that just ticks heartbeats
  on its own timer decouples "heartbeat arrived" from "main loop alive" —
  a truly deadlocked main loop (not a legitimate slow device call, an
  actual hang) would look perfectly healthy forever, defeating the point
  of heartbeat-based hang detection. Fixing that requires the main loop to
  stamp a liveness timestamp every iteration, the heartbeat thread to
  publish that timestamp's *staleness* (not just "I published, therefore
  alive"), and — critically — a corresponding change to
  `enforce_device_driver_heartbeat_timeout` to check staleness of the
  embedded field instead of message-arrival cadence. Also has the
  ZMQ-PUB-is-not-thread-safe issue (can't share `self.pub` with the main
  loop; needs its own PUB or a thread-safe hand-off). More invasive than
  Option 1 for a worse-calibrated payoff unless combined with Option 3.
- *Option 3 (recommended): move device I/O (send, and receive when the
  command has one) off the main loop into a single-worker executor,
  single-flight.* `DeviceRunner` gets one dedicated worker (e.g.
  `ThreadPoolExecutor(max_workers=1)`) that owns all device command
  execution. The main loop submits a command, gets a `Future`, and keeps
  iterating — publishing heartbeats inline (unchanged, no second PUB
  socket, no liveness-timestamp plumbing) and polling the `Future`
  non-blockingly each iteration. Single-flight (one command at a time,
  enforced by the main loop refusing to submit a second command while one
  is outstanding) means no concurrent access to the device handle and no
  correlation-id/multi-call tracking needed — the constraint that makes
  this tractable rather than a full async rewrite. The RPC caller's
  existing timeout (`device_rpc_timeout_ms` / `fetch_timeout_s`) still
  governs how long the *caller* waits and can return an error promptly
  even if the worker thread is still running underneath; the manager never
  needs to kill the driver process just because one command is slow. This
  fixes the root cause (the main loop blocking at all) rather than
  tolerating a longer stall, and gets Option 2's intended benefit (honest,
  inline heartbeat) for free since the main loop genuinely never blocks.
  **Remaining gap to design:** a true hardware/firmware hang leaves the
  worker thread stuck forever holding the device (Python threads blocked
  in native I/O generally can't be cancelled), and since it's
  single-flight every subsequent command to that device queues
  indefinitely. Needs a much longer, configurable "stuck-device" ceiling
  (tens of seconds to minutes, not 3 s) after which the driver logs a hard
  error and falls back to the existing manager restart-supervision — a
  far rarer, coarser case than today's routine 3-9 s kill on any slow
  call, so a much smaller blast radius than the current bug. Touches the
  RPC/stream/telemetry dispatch path in `DeviceRunner.run()` — more code
  than Option 1, but not the sprawling rewrite a general async I/O model
  would require.

**Decision:** proceed with Option 3. Recommend splitting A3 into its own
PR from A1/A2 because of the process-kill blast radius and the dispatch-
path changes in `DeviceRunner.run()`.
- Tests (sketch, to refine at implementation time): (1) a device method
  that sleeps `2 × heartbeat_period` — assert heartbeats keep arriving
  throughout (doc §6 test 8) and the RPC still completes/returns
  correctly once the call finishes. (2) a second command issued while one
  is outstanding is queued/rejected, not run concurrently. (3) a
  `tests/test_manager_process_supervision.py`-style test confirming the
  manager no longer kills the driver for a call under the (now
  unnecessary, but still present as backstop) heartbeat timeout. (4) a
  stuck-forever command (mocked hang) exercises the stuck-device ceiling
  and eventual restart fallback, without falsely triggering on ordinary
  slow calls.

### Batch B — HDF writer counters & partial-drop policy
`src/experiment_control/processes/hdf_writer.py`.

First, extend `_STREAM_COUNTER_ATTRS` (lines 84-94) with the new persisted
counters: `"buffer_discarded_total"`, `"meta_missing_dropped_total"`.
(These auto-persist through `_persist_stream_attrs`.)

**B1 (item 4) — count buffer discards on re-attach/reset. DONE (2026-07-11).**
- In `_ensure_chunk_ready_reader`, *before*
  `self._stream_dropped_total[key] = 0` (line 4527) and the pop (line
  4528): compute
  `discard = len((self._stream_buffers.get(key) or {}).get("data", []))`
  and if `discard:`
  `self._bump_stream_counter(key, "buffer_discarded_total", discard, last_error="buffer discarded on ring re-attach")`.
- In `_reset_stream_runtime_state` (line 4472): same pattern before
  `self._stream_buffers.pop(key, None)` (line 4475).
- **Design note (deviates from the original audit doc):** the audit doc
  says add to `_stream_dropped_total`, but that map is (a) reset to 0 on
  attach (line 4527) and (b) written to the `data` dataset's
  `dropped_total` attr as the *seq-gap* drop count. Folding
  buffer-discards into it would both be erased by the reset and conflate
  two loss meanings. A dedicated `buffer_discarded_total` on the stream
  group is cleaner and survives. This is an intentional divergence from
  the original proposal.
- Test (audit doc §6 test 6): buffer some frames for `(dev, s)` via
  `_stream_buffer_for_key`, then call `_ensure_chunk_ready_reader` with a
  *different* `shm_name` (new ring); assert
  `_stream_counters[key]["buffer_discarded_total"] == n`. Second test for
  `_reset_stream_runtime_state` via a reader whose `read_events` raises
  (drives `_read_chunk_ready_events` failure path), asserting both
  `drain_failures_total` and `buffer_discarded_total` (audit doc §6 test
  5).
- **Implemented as planned**, with the `buffer_discarded_total`
  divergence noted above. `attach_failures_total`-style lazy persistence
  (no immediate `_persist_stream_attrs(key)` call at the bump site,
  consistent with how `attach_failures_total` is handled elsewhere) rather
  than an explicit persist call. New counter registered in
  `_STREAM_COUNTER_ATTRS` and the default dict in `_stream_counter_state`.
  Tests: `test_buffer_discard_on_reattach_is_counted`,
  `test_buffer_discard_on_drain_reset_is_counted`
  (`tests/test_hdf_writer.py`, `HdfWriterLossAccountingTests`) — both pass.

**B2 (item 5) — filter bad frames instead of clearing the batch. DONE
(2026-07-11).**
- Replace lines 5245-5253. Instead of `any(...)` → clear, partition: build
  `bad_idx = [i for i,p in enumerate(data_list) if len(p) != expected_nbytes]`.
  If `bad_idx`, drop those indices from
  `data_list`/`seq_list`/`t0_mono_list`/`t0_wall_list`/`context_list`, set
  `n = len(data_list)`,
  `self._bump_stream_counter(key, "payload_size_failures_total", len(bad_idx), last_error="stream payload size mismatch")`,
  add `len(bad_idx)` to a drop counter, and continue writing the good
  frames (if `n == 0`, clear and return as today). Counter now bumps by
  the number actually dropped, not +1.
- Test (audit doc §6 test 10): buffer 3 good + 1 wrong-size frame, run
  `_write_stream_buffers_batch`; assert 3 rows written to the dataset and
  `payload_size_failures_total == 1`.
- **Implemented as planned.** This was promoted out of the bare-minimum
  diagnostics batch and done as a real fix: it's an actual data-loss bug
  (good frames discarded alongside a bad one), not just missing
  accounting. Test:
  `test_payload_size_mismatch_filters_bad_frame_keeps_good_ones`
  (`tests/test_hdf_writer.py`, `HdfWriterLossAccountingTests`) — 3 good + 1
  bad frame in, 2 good rows written (seqs `[1, 3]`),
  `payload_size_failures_total == 1`. Full suite (96 tests) still passes,
  including the pre-existing all-bad-frames regression test
  (`test_write_stream_buffers_clears_context_id_on_bad_payload_size`),
  confirming the all-bad fallback-to-clear path is preserved.

**B3 (item 6b) — count dtype/shape-unresolvable drops. DONE (2026-07-11).**
- At lines 5200-5202, before `_clear_stream_buffer`, add
  `self._bump_stream_counter(key, "meta_missing_dropped_total", len(data_list), last_error="dtype/shape unresolved")`
  and `self._persist_stream_attrs(key)`.
- Test: set up a buffered stream with no `_stream_schema`, no reader, and
  `stream_meta=None`; call `_write_single_stream_buffer`; assert
  `meta_missing_dropped_total == n` and no rows written.
- **Implemented as planned**, minus the explicit `_persist_stream_attrs(key)`
  call (kept consistent with the lazy-persist pattern already used by
  sibling counters at this site). New counter registered in
  `_STREAM_COUNTER_ATTRS` and the default dict. Test:
  `test_meta_missing_drop_is_counted`
  (`tests/test_hdf_writer.py`, `HdfWriterLossAccountingTests`) — passes.

**B4 (item 6a) — count chunk-parse failures. DONE (2026-07-11).**
- In `_handle_manager_chunk_ready` (`hdf_writer_topics.py` lines 63-67), on
  `parsed is None` call `writer._bump_error("stream.chunk_parse_failed")`
  before returning. Because a failed parse yields no
  `(device_id, stream)` key, this is a **global** live counter (consistent
  with how `stream.attach`/`stream.drain` non-attributable errors are
  surfaced in `hdf.status`), not a per-stream group attr. Optionally also
  maintain a root file attr `chunk_parse_failures_total` written alongside
  `dropped_local_messages_total` if a durable count is wanted — this is a
  judgment call since per-stream attribution is impossible here.
- Test: feed a malformed chunk_ready dict (missing `descriptor`/
  `device_id`) through the topic handler; assert the
  `stream.chunk_parse_failed` error count in `hdf.status` (or the root
  attr) incremented.
- **Implemented as planned**: global live counter only (no root file attr
  added — judged not worth the extra surface for a bare-minimum batch).
  Test: `test_chunk_parse_failure_is_counted`
  (`tests/test_hdf_writer.py`, `HdfWriterLossAccountingTests`) — passes.

**B5 (item 11) — fix the `last_seq == 0` gap skip. DONE (2026-07-11).**
- Introduce an explicit unset sentinel. Change the attach seeding so that
  a no-seq attach leaves `last_seq` at a distinct "unset" value. Concrete
  recommended form: define a sentinel `_STREAM_SEQ_UNSET = -1`, seed to it
  when the chunk carried no seq (line 4526 → `-1`), default
  `_stream_last_seq.get(key, -1)` at line 4444, and write the guard as
  `if last_seq >= 0 and seq > last_seq + 1:` in
  `_append_chunk_ready_events` (currently line 4580). Because the
  seeded-from-`initial_seq` path already emits `startup_seq_gap` at attach
  time (lines 4519-4524), the residual bug is purely the `last_seq == 0`
  no-seq path. **Flag:** line 4444's default and line 4526's else-branch
  must both switch to the sentinel together, or normal streams regress
  (a `last_seq` of `0` would otherwise be misread as "unset" for streams
  that legitimately start at seq 0).
- Test: attach with a no-seq chunk (`initial_seq=None`), then deliver
  events with seqs `[1, 3]`; assert `seq_gap_total == 1` (currently 0).
- **Implemented as planned**, sentinel named `-1` inline rather than a
  module-level `_STREAM_SEQ_UNSET` constant (matches the existing style at
  this site — no other magic numbers here are named constants either).
  All three `_stream_last_seq.get(key, ...)` default sites (main dispatch,
  disabled-device tracking, and the attach seed) were switched to `-1`
  together, per the flag above. Tests:
  `test_gap_after_no_seq_attach_is_counted`,
  `test_no_seq_attach_seeds_unset_sentinel`
  (`tests/test_hdf_writer.py`, `HdfWriterLossAccountingTests`); the
  pre-existing `test_fresh_attach_without_seq_falls_back_to_zero` was
  renamed to `test_fresh_attach_without_seq_falls_back_to_unset_sentinel`
  and its assertion updated from the old, buggy `0` to `-1`.

### Batch C — Telemetry schema-absence accounting
`src/experiment_control/processes/hdf_writer.py`.

**C1 (item 7) — count skips + cache negative schema result. DONE
(2026-07-11).**
- Add per-device negative cache:
  `self._telemetry_schema_absent: dict[str, float] = {}` (device_id →
  monotonic time of last miss) with a short TTL (e.g. 2-5 s). In
  `_ensure_device`, if `device_id in self._telemetry_schema_absent` and
  within TTL, return `False` without re-firing `_schema_rpc` (fixes the
  per-batch RPC cost). On a successful RPC that still lacks the device,
  record the timestamp; clear the entry on success.
- In `_write_buffered_rows_batch` line 4329, on the
  `not self._ensure_device(...)` skip, bump a live counter
  `self._bump_error(f"telemetry.skipped_no_schema.{device_id}")`
  (per-device, matching the existing `telemetry.write.<dev>` /
  `schema.rpc` naming in the errors map). Optionally accumulate a root
  attr `telemetry_skipped_no_schema_total`.
- Test: build a writer whose manager schema RPC returns a schema *not*
  containing `dev-x`; append a telemetry row for `dev-x`; assert the row
  is skipped, `telemetry.skipped_no_schema.dev-x` counted, and that a
  second row for the same device within TTL does not trigger a second
  `_schema_rpc` (spy/patch `_schema_rpc` and assert call count == 1).
- **Implemented as planned** with a 3s TTL
  (`_telemetry_schema_absent_ttl_s`). Only the counter (not the root attr)
  was added — judged not worth the extra surface for now. This was
  promoted out of the diagnostics-only batch because rows were being
  silently and permanently dropped, a real correctness bug, not just an
  observability gap. Test:
  `test_telemetry_schema_absent_device_is_counted_and_cached`
  (`tests/test_hdf_writer.py`, `HdfWriterLossAccountingTests`) — asserts
  the schema RPC fires once across two flush batches within the TTL and
  the skip counter reaches at least 2.

### Batch D — Manager dedup & drop accounting
`src/experiment_control/_manager/driver_pub.py`,
`src/experiment_control/_manager/pubsub.py`.

**D1 (item 8) — dedup on `(shm_name, seq)`. FLAG: correctness-sensitive.**
- In `ingest_chunk_ready` (driver_pub.py lines 727-731), change the guard
  to also compare `shm_name`:
  ```python
  if isinstance(previous, dict) and previous.get("seq") == seq and previous.get("shm_name") == desc.get("shm_name"):
      return
  ```
  Optionally `_counter_add(manager, "_manager_chunk_ready_dedup_skipped_total")`
  on the swallow.
- **Side-effect flag:** loosening the dedup key means that if a driver
  ever legitimately re-emits the *same* `(shm_name, seq)` (e.g. a
  duplicate PUB + the parallel RPC-result ingest path noted in the
  pipeline diagram, driver_pub/rpc_calls both calling
  `_ingest_chunk_ready`), it is still collapsed — good. But if two
  distinct rings ever momentarily share a name (shouldn't happen; name
  embeds PID), the new key would let both through. Net risk is low and
  strictly reduces false-positive dedups.
- Test (audit doc §6 test 2, extend
  `tests/test_manager_driver_pub_bounds.py`): ingest a chunk_ready
  `(shm=A, seq=5)`, then `(shm=B, seq=5)`; assert **two**
  `manager.chunk_ready` events published (currently the second is
  swallowed). Then ingest `(shm=B, seq=5)` again and assert it *is*
  deduped. Uses the existing `_build_manager_stub()` harness.

**D2 (item 9) — per-topic lifecycle drop breakdown.**
- Replace the scalar `_lifecycle_event_dropped` bump path with a per-topic
  dict. In `pubsub.py` lines 108-109, under the lock also increment
  `self._lifecycle_event_dropped_by_topic[topic] = ... + 1`. Declare
  `_lifecycle_event_dropped_by_topic: dict[str, int]` and initialize it in
  `core_state.py` (alongside `event_dropped` at lines 190-202) and wire it
  in `bind`/mapping (lines 211-212).
- In `_drain_lifecycle_events` (manager.py:1768-1785) snapshot+reset the
  per-topic dict too and include it in the emitted
  `manager.lifecycle.events_dropped` payload
  (`payload={"dropped": dropped, "dropped_by_topic": {...}}`).
- **Severity note (from Phase 1):** with the pump guard in place,
  `manager.chunk_ready` no longer flows through the off-thread queue path,
  so this is now an observability improvement rather than a chunk-loss
  localizer. Worth doing but lower priority than Batches B/C.
- Test: drive `_publish_manager_event` from a non-main thread with a full
  `_lifecycle_event_queue` for two distinct topics; assert
  `dropped_by_topic` splits them.

### Batch E — Writer process-telemetry real seq
`src/experiment_control/processes/hdf_writer.py`.

**E1 (§9 item 4) — publish a real writer seq. DONE (2026-07-11).**
- Add `self._process_telemetry_seq = 0` in `__init__`. In
  `_publish_writing_active_telemetry` (lines 1752-1772) increment it and
  add `"seq": self._process_telemetry_seq` to the payload dict (lines
  1758-1770). Ensure the process-telemetry schema includes a `seq` field
  so `_write_buffered_rows_batch` (line 4344) persists a monotone value
  instead of `-1` (the device path already appends
  `("seq", np.int64)` at line 240; confirm the process schema builder does
  likewise, and if not, add it in the `_ingest_process_schema` path near
  line 322).
- Test: extend an existing hdf_writer process-telemetry test to publish
  two `writing_active` updates and assert the written
  `/process_telemetry/<writer>/data['seq']` values are `1, 2` (monotone,
  not `-1`), so a writer restart (seq resets) is detectable.
- **Implemented as planned.** No schema-builder change was needed:
  `_ingest_process_schema` already routes through `_create_device_dataset`,
  which unconditionally includes `("seq", np.int64)` (line 240) for every
  telemetry dataset (device and process alike) — only the payload-side
  `seq` field was missing, so that's the only change made. Test:
  `test_writer_process_telemetry_seq_increments`
  (`tests/test_hdf_writer.py`, `HdfWriterLossAccountingTests`) — passes
  (asserts the published payload's `seq` increments `1, 2` across two
  calls; did not add a full end-to-end dataset-row assertion since the
  write path is unchanged and already covered by existing telemetry-write
  tests).

---

## Suggested PR grouping / sequencing

**DONE (2026-07-11, uncommitted):**

- The "bare minimum extra diagnostics payload" subset — **B1, B3, B4,
  E1** — pure additive counters/fields that don't change what gets kept or
  dropped.
- Two real, actual-data-loss fixes, done as a targeted follow-up —
  **B2** (whole-batch discard on payload-size mismatch) and **C1**
  (telemetry silently and permanently dropped for schema-absent devices).

All six landed with regression tests in `tests/test_hdf_writer.py`
(`HdfWriterLossAccountingTests`, 7 tests total in that class), full
`test_hdf_writer.py` suite (96 tests) passing. See each item's
"Implemented as planned" note above for specifics.

**DONE (2026-07-11, uncommitted), second follow-up:** B5 (gap-check
sentinel fix) and A1/A2 (driver heartbeat missed-tick counter and
produced-seq field) — see each item's "Implemented as planned" note above.
Tests added to `tests/test_hdf_writer.py`
(`HdfWriterLossAccountingTests`, now 9 tests) and
`tests/test_driver_stream_schedule.py` (now 6 tests, up from 3); one
pre-existing test
(`test_fresh_attach_without_seq_falls_back_to_zero` →
`..._to_unset_sentinel`) updated because it asserted the old, buggy
behavior. Full `test_hdf_writer.py` suite (98 tests) and
`test_driver_stream_schedule.py` all passing, plus
`test_shm_ring_consistency`, `test_manager_reload_device_spec`,
`test_manager_driver_pub_bounds`, `test_record_streams`,
`test_manager_stream_rpc_chunk_ready` sanity-checked.

Still open, split by whether it's an actual bug (real data loss / incorrect
behavior) or purely missing observability:

**Real issues (actual data loss or incorrect behavior):**

1. **A3 — the FS740-killer.** Manager kills a driver process mid-call
   whenever a legitimate device call blocks longer than the heartbeat
   timeout (~3-9 s), because `DeviceRunner.run()` is single-threaded and a
   blocking call stalls the heartbeat too. Real, confirmed data loss.
   **Design settled 2026-07-11** (see A3 above): move device command
   execution (send + receive) off the main loop into a single-flight,
   single-worker executor, so the main loop never blocks and heartbeats
   stay honest without any manager-side change. Flagged as the riskiest
   item to implement — touches process-kill behavior and the
   RPC/stream/telemetry dispatch path in `DeviceRunner.run()`. Not yet
   implemented; own PR, ships with its own regression tests (sketch above).
2. **D1 — manager chunk_ready dedup key too loose.** `ingest_chunk_ready`
   (driver_pub.py:727-731) dedups on `seq` alone; after a driver restart a
   new SHM ring can reuse a seq number matching the old ring's cached seq,
   wrongly swallowing a legitimate chunk_ready (later recovered via ring
   backfill, but shows up as a `startup_seq_gap` instead of clean data —
   incorrect, not silently gone forever). Fix: dedup on `(shm_name, seq)`.
   Contained, single-file, low risk. Not yet implemented.

**Diagnostics-only (missing counters/fields, no behavior change):**

3. **D2 (per-topic lifecycle drop counter):** diagnostics only, and lower
   priority than originally scoped since the pump-thread path that
   motivated it (§9 item 2) is already dead code — deferred.

---

## Critical files for implementation

- `src/experiment_control/processes/hdf_writer.py` (Batches B, C, E;
  `_STREAM_COUNTER_ATTRS`, `_ensure_chunk_ready_reader`,
  `_reset_stream_runtime_state`, `_write_single_stream_buffer`,
  `_ensure_device`, `_write_buffered_rows_batch`,
  `_append_chunk_ready_events`, `_publish_writing_active_telemetry`)
- `src/experiment_control/_driver/runner.py` (Batch A1/A2;
  `_publish_scheduled_streams`, `_publish_heartbeat`, `publish_stream`,
  `__init__`)
- `src/experiment_control/_manager/driver_pub.py` (D1;
  `ingest_chunk_ready`)
- `src/experiment_control/_manager/process_supervision.py` (A3;
  `enforce_device_driver_heartbeat_timeout`) with
  `src/experiment_control/_manager/pubsub.py` + `manager.py` for D2
- `src/experiment_control/processes/hdf_writer_topics.py` (B4;
  `_handle_manager_chunk_ready`)

Test files to extend: `tests/test_hdf_writer.py` (B, C, E),
`tests/test_manager_driver_pub_bounds.py` (D1, D2),
`tests/test_manager_process_supervision.py` (A3), and a driver-runner test
module (A1, A2).
