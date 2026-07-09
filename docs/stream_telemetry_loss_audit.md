# Stream & telemetry loss-point audit

Code-level map of every place a streaming sample or telemetry sample can be
dropped, skipped, overwritten, or fail to reach the HDF5 file. Written after
tracing both pipelines end-to-end (device runner → SHM ring / ZMQ PUB →
manager drain/republish → HDF writer drain → buffers → background flush →
datasets). Motivated by an acquired file in which an expected FS740 stream
wrote nothing.

Paths are relative to `src/experiment_control` (abbreviated `EC`). Line
numbers are from branch `manager-chunk-ready-priority` (commit `83e606d`,
2026-07-08) and will drift.

---

## 1. Pipeline diagrams

### Stream path

```
device method (e.g. fs740.read_timestamp_record)
  └─ build_stream_wrapper._wrapper            EC/_driver/stream_wrappers.py:207
       └─ DeviceRunner.publish_stream          EC/_driver/runner.py:1318
            ├─ ShmRingWriter.write (SHM ring, slot_count=ring_slots, default 1024)
            │                                  EC/shm/shm_ring.py:182
            └─ PUB "<dev>/chunk_ready"         runner.py:1367-1374   [driver PUB, SNDHWM=1000 default]
                 └─ manager._sub (RCVHWM=1000, single SUB, FIFO)
                      └─ handle_driver_pub (drain, 256+256 cap)     EC/_manager/driver_pub.py:259
                           └─ ingest_chunk_ready (seq dedup)        driver_pub.py:721
                                └─ _publish_manager_event("manager.chunk_ready")
                                       EC/_manager/pubsub.py:76   [external_pub, SNDHWM=1000]
   (parallel path: stream RPC result → _call_device_rpc → _ingest_chunk_ready,
    EC/_manager/rpc_calls.py:275-295 — same dedup collapses the duplicate)
                                     └─ HdfWriter._sub (RCVHWM=10 000)
                                          └─ _drain_socket (unbounded)  EC/processes/hdf_writer.py:3217
                                               └─ _handle_chunk_ready              hdf_writer.py:4391
                                                    ├─ _ensure_chunk_ready_reader (attach, seed last_seq=initial_seq-1)  :4477
                                                    ├─ ShmRingReader.read_events(last_seq)   shm_ring.py:344
                                                    ├─ context resolve / _stream_pending_by_seq (TTL 5 s, cap 10 000)   :4629-4758
                                                    └─ _stream_buffers[key] (unbounded lists; hard cap 4×200k rows)
                                                         └─ _enqueue_flush_batch → bg thread → _write_stream_buffers_batch  :5107
                                                              └─ /streams/<dev>/<stream>/session_NNN/{data,seq,t0_*,context_id}
```

### Telemetry path

```
DeviceRunner._publish_telemetry (skipped if DISCONNECTED; no catch-up on missed ticks)  runner.py:1110
  └─ PUB "<dev>/telemetry" → manager._sub → handle_driver_pub → ingest_telemetry  driver_pub.py:410
       └─ republish "manager.telemetry_update"  driver_pub.py:526
            └─ HdfWriter topic handler (enabled-device filter)  hdf_writer_topics.py:38
                 └─ _buffer_append → _buf deque (maxlen 200 000, drop_newest)  hdf_writer.py:3012
                      └─ flush batch → _write_buffered_rows_batch (per-row _ensure_device gate)  :4314
                           └─ /telemetry/<device>/data  (or /process_telemetry/<process>)
```

---

## 2. Loss/skip point table

Severity legend: 🔴 can silently lose data with no trace · 🟠 loses data but
leaves evidence · 🟡 by-design skip, observable.

| # | Sev | Location | Condition | Silent? | Counter / evidence | In HDF5? | Whole stream empty? |
|---|-----|----------|-----------|---------|--------------------|----------|--------------------|
| 1 | 🔴 | `_publish_scheduled_streams` runner.py:1214-1215 | Driver loop blocked past due time → missed periods skipped (`next_due += (missed+1)*period`) | **Yes — nothing** | none; only `loop_lag_s` in heartbeat hints | t0 gaps but **no seq gap** (seq only increments on write) | Partially; combined with #7, yes |
| 2 | 🟡 | `_publish_scheduled_streams` runner.py:1209 / `_publish_telemetry` :1111 | `device_state == DISCONNECTED` → return | Yes | heartbeat `device_state` | absent rows only | **Yes** — never-connected device streams/telemeters nothing |
| 3 | 🔴 | scheduled stream call raises, runner.py:1224-1227 | dtype/shape mismatch, hardware error | Mostly | `_last_error` in next heartbeat (overwritten each tick); no counter, no stderr | nothing | **Yes**, if it fails every tick |
| 4 | 🟠 | RPC-driven stream (`stream__*`), runner.py:1003,1042 | device DISCONNECTED or method raises (e.g. FS740 `TimeoutError` after 10 s) | No | `manager.command` event `ok=false` (kept in `/events` even in filtered mode: `_should_keep_event` hdf_writer.py:3042) | `/events` rows | **Yes** if every call fails |
| 5 | 🔴 | Driver loop blocked by long RPC/telemetry/stream call (single-threaded `run()`, runner.py:238-279) | one blocking call stalls heartbeat + telemetry + scheduled streams | Yes for the missed samples | `loop_lag_s`; missed telemetry ticks uncounted | timestamp gaps | contributes to #7 |
| 6 | 🔴 | Driver PUB → manager SUB HWM (both default 1000; runner.py:175, core_state.py:49) | manager stalled > ~2000 msgs backlog per driver → ZMQ silently drops **newest** | **Yes — ZMQ-invisible** | none | streams: recovered from ring unless wrapped (then `seq_gap_total`); telemetry: **permanently gone, uncounted** | unlikely alone |
| 7 | 🟠 | **Manager kills blocked driver**: `enforce_device_driver_heartbeat_timeout` process_supervision.py:962-1024 | no heartbeat for >3 s (`heartbeat_timeout_s=3.0`, manager.py:299) while driver blocked in a device call → `proc.terminate()` | No | `manager.driver.failed` "heartbeat stale", manager.log | `/events` if log kept; driver restart visible | **Yes** — FS740 `fetch_timeout_s=10 s` ≫ 3 s; any wait >3 s for a trigger pulse kills the driver mid-run |
| 8 | 🟡 | Manager drain caps, driver_pub.py:289-329 | backlog > 512/tick → deferred (not lost) | No | `drain_cap_hit_total`, `manager.drain_cap_hit`, priority-scan counters | no | no |
| 9 | 🔴 | `ingest_chunk_ready` seq dedup, driver_pub.py:727-731 | new ring after driver restart re-uses a seq equal to the cached one (dedup ignores `shm_name`) → chunk_ready swallowed | Yes | none | later recovered but frame skipped as `startup_seq_gap` | first frame(s) after restart only |
| 10 | 🟠 | `_publish_manager_event` from lifecycle worker thread, pubsub.py:102-109 | lifecycle event queue full → non-audit topics dropped — includes `manager.chunk_ready`/`manager.telemetry_update` ingested during an RPC pump on a worker thread (rpc_calls.py:235) | Semi | aggregate `_lifecycle_event_dropped` → `manager.lifecycle.events_dropped` (no per-topic split) | streams recovered by next chunk; telemetry gone | no |
| 11 | 🔴 | manager `external_pub` HWM (1000) vs HdfWriter SUB (10 000) | HDF writer stalled >11k msgs → ZMQ drops | Yes | none | streams ring-recoverable; telemetry gone | no |
| 12 | 🟠 | HdfWriter not running / started late; slow-joiner | messages before SUB connect | Yes | none | streams: `startup_seq_gap` on first attach; telemetry: gone | **Yes** if writer subscribed after run (but `hdf.streams.expect` would have failed `hdf_not_writing`, hdf_writer.py:4063) |
| 13 | 🔴 | `ChunkReadyMessage.parse` → None, hdf_writer_topics.py:64-66 | malformed descriptor | **Yes, no counter** | none | nothing | yes, if systematic |
| 14 | 🟡 | Disabled device, hdf_writer_topics.py:33 / `_handle_chunk_ready_disabled_device` hdf_writer.py:4453 | device in disabled set | By design | `chunk_ready_seen_total` still increments; filter state via `hdf.devices.get` | group attrs: seen>0, rows=0; strict finalize error | **Yes** |
| 15 | 🟠 | Reader attach failure, hdf_writer.py:4494-4503 | SHM gone (driver died/terminated) | No | `attach_failures_total`, `stream.attach` error count | group attr | **Yes** if ring never attachable |
| 16 | 🟡 | First-attach seeding, hdf_writer.py:4514-4519 | frames already in ring before first seen chunk_ready are skipped | No | `startup_seq_gap`, `seq_gap_total` | group attrs | no (by design) |
| 17 | 🔴 | **Re-attach on new `shm_name`** (driver restart), hdf_writer.py:4523 | `_stream_buffers.pop(key)` discards read-but-unflushed frames (up to `write_every_s`=5 s worth) | **Yes, no counter** | none | rows just missing before session boundary | no, partial |
| 18 | 🟠 | `read_events` drain failure → `_reset_stream_runtime_state`, hdf_writer.py:4540-4552 | reader exception; also pops unflushed buffers (same silent loss as #17) | Partially | `drain_failures_total`, `stream.drain` | group attr | repeated failures → yes |
| 19 | 🟠 | **SHM ring wrap**: `read_events` shm_ring.py:344-386 | > `ring_slots` writes between drains → oldest overwritten | No | `seq_gap_total` + per-dataset `dropped_total` attr (hdf_writer.py:4570-4573) | seq gaps in `/seq` | no |
| 20 | 🔴 | Torn-slot race, shm_ring.py:356-377 | payload copied without re-checking `seq_end` after copy; concurrent overwrite → corrupted frame accepted | **Yes** | none | garbage row, valid seq | no (corruption, not loss) |
| 21 | 🔴 | Seq-gap check skips `last_seq == 0`, hdf_writer.py:4570 (`if last_seq and …`) | gap after a no-seq descriptor attach not counted | Yes | — | undercounted gaps | no |
| 22 | 🟡 | Context pending TTL/overflow, hdf_writer.py:4701-4758 | unresolved context | No — **frames are still written** with `context_id=-1` | `_context_written_minus1_missing`, `_context_evicted_pending_overflow` (in `hdf.status`) | rows present, context −1 | **No** — context issues never empty a stream |
| 23 | 🟡 | Telemetry/event deque overflow, hdf_writer.py:3012-3035 (maxlen 200 000, drop_newest) | flush stalled | No | `dropped_local_messages_total` / `dropped_event_messages_total` **file attrs** (hdf_writer.py:1440-1441) + `dropped_by_topic` in `hdf.status` | yes | no |
| 24 | 🟡 | Reservoir hard cap: `_drop_oldest_stream_frames` hdf_writer.py:1876-1908 | stream rows > 4×200 000 | No | per-stream `dropped_total` attr, `bg.reservoir_drop`, `hdf.backpressure` event | yes | no |
| 25 | 🟡 | Bg queue full, hdf_writer.py:1910-1952 | deferral is **lossless** | No | `deferred/dropped_flush_batches`, `hdf.flush_batch_deferred` | status only | no |
| 26 | 🟠 | Payload-size mismatch drops the **whole batch**, hdf_writer.py:5193-5201 | any one frame's nbytes ≠ dataset itemsize×shape → all n frames cleared | Coarse | `payload_size_failures_total` (+1, not +n) | group attr; data absent | **Yes** if shape persistently wrong |
| 27 | 🔴 | dtype/shape unresolvable, hdf_writer.py:5148-5150 | schema+reader both missing at write time | **Yes, no counter** | none | nothing | rare |
| 28 | 🟡 | `_h5 is None` at write, hdf_writer.py:5120-5123 & :4316 | frames/rows consumed while no file open → discarded | Yes (by design) | none | n/a | **Yes** for anything acquired outside a file |
| 29 | 🔴 | `_ensure_device` False, hdf_writer.py:4324-4325 | device **absent from manager schema** (or disabled, or dataset-create failed) → telemetry row silently skipped; schema RPC re-fired per batch | **Yes when merely absent** | `schema.rpc` / `ingest.device` only on exceptions | `/telemetry/<dev>` absent | telemetry-side yes |
| 30 | 🟡 | Telemetry row conversion error, hdf_writer.py:4374-4376 | bad value/dtype | No | `telemetry.write.<dev>` error count + rate-limited process event | row missing | no |
| 31 | 🟠 | Writer SIGKILL race (bg_join 2 s, supervisor ~3 s; flush cadence 15 s) | crash between flushes | Yes | truncated/unclosed HDF5 | file damage | tail loss |
| 32 | 🔴 | **Bg flush-batch exception → whole batch discarded**, `_bg_thread_run` hdf_writer.py:1475-1481 | any exception inside `_handle_flush_batch` (context/telemetry/event/stream write or `h5.flush`) → the entire snapshotted batch is dropped | **Yes — live-only `bg._FlushBatch.failed` bump, nothing in the file** | `_record_exception` + `hdf.status` errors map (not persisted) | rows silently absent in ≥1-flush-interval blocks; `dropped_total` **not** bumped | partial (confirmed cause of the PXIe holes — see §9) |

---

## 3. "FS740 stream wrote nothing" — ranked explanations

Grounding (from the `centrex-experimental-stack` instance
`state-preparation-b-detection`): FS740's stream is **RPC-driven** (no
`period_s` in `devices/fs740.yaml`) — one `stream__read_timestamp_record` per
trace, called by the sequencer inside `atomic: acquire_traces`
(`sequences/laser_frequency_triangle_scan_from_current.yaml:173-182`), after
`set_context` registers `{fs740, timestamps}` with `hdf.streams.expect
strict=True` (sequencer.py:2408). `read_timestamp_record` blocks in
`_wait_for_points` up to `fetch_timeout_s=10 s` polling for hardware edges
(`devices/drivers/fs740_timestamp_driver.py:160-171`).

1. **Driver killed by heartbeat supervision mid-wait (most likely for total
   absence).** Manager default `heartbeat_timeout_s=3.0` with hard multiplier
   3; the FS740 driver's single-threaded loop stops heartbeating the moment it
   enters `_wait_for_points`. If the expected pulse doesn't arrive within
   ~3 s (missing/late trigger, wrong channel/level), the manager marks the
   driver FAILED and **terminates the process**
   (process_supervision.py:1016-1023) — no cleanup, ring gone, device
   DISCONNECTED after restart until auto-reconnect. Every subsequent
   `stream__` RPC errors "Device is disconnected". One late pulse early in
   the run can zero the entire stream. Note `fetch_timeout_s=10 s` can
   *never* be reached in practice — the manager kills the driver at ~3–9 s
   first.
2. **Every stream RPC failed device-side** — FS740 never connected (connect
   failed at startup, auto-reconnect suppressed) or no pulses at all
   (`TimeoutError`). Loud in `/events` (`manager.command` with `ok=false` is
   kept even in filtered event-log mode) and in the manager RPC timeout
   mismatch: manager default `device_rpc_timeout_ms=1500` < driver-side 10 s
   wait, so the sequencer sees `zmq.Again` even when the driver eventually
   succeeds. In that partial case the sample **is** still captured via the
   PUB path (the driver publishes regardless of whether the REQ reply is
   consumed) — so "step failed but data present" is possible; "step failed
   and no data" points to the driver-side raise.
3. **Sequence never executed the reads** — `assert_no_pending_timestamps`
   raised, or an earlier step aborted the atomic block. Check sequencer
   events in `/sequencer/events`.
4. **Chunks reached the writer but were discarded**: payload-size mismatch
   (whole-batch drop, #26), attach failures against a dead ring (#15),
   repeated drain failure resets (#18). All leave per-stream counters on
   `/streams/fs740/timestamps` attrs.
5. **Writer-side gating**: fs740 disabled in the writer
   (`chunk_ready_seen_total>0, rows=0`), or file not open during acquisition
   (#28) — the latter is largely excluded because `hdf.streams.expect` fails
   with `hdf_not_writing` and the sequencer raises.
6. Manager-side chunk_ready loss (HWM #6, lifecycle-queue drop #10, dedup #9)
   — cannot explain *total* absence because SHM-ring recovery re-reads missed
   frames on the next surviving chunk_ready; only plausible if there was
   exactly one sample or the manager was down the whole run.

The single most diagnostic bit: **`chunk_ready_seen_total` on the
`/streams/fs740/timestamps` group** (created eagerly at expect-time,
hdf_writer.py:4087). `0` ⇒ loss upstream of the writer (cases 1–3); `>0`
with `rows_written_total==0` ⇒ writer-side (cases 4–5).

---

## 4. Inspection checklist for an affected file

- `/streams/fs740/timestamps` group attrs: `chunk_ready_seen_total`,
  `events_read_total`, `rows_written_total`, `seq_gap_total`,
  `startup_seq_gap`, `attach_failures_total`, `drain_failures_total`,
  `payload_size_failures_total`, `first_seq`, `last_seq`, `last_error`,
  `expected_count`, `strict_required`, `expected_context_ids_json`.
- File/measurement attrs: `acquisition_ok`, `hdf_strict_stream_errors_json`
  (sequencer registers strict=True, so an empty required stream **must**
  appear here if finalize ran — stop/rotate/close paths at
  hdf_writer.py:2593/2728/2839), `dropped_local_messages_total`,
  `dropped_event_messages_total`, `zmq_rcvhwm`.
- Session datasets: presence of `session_00N/` groups; `seq` monotonicity and
  gaps; `dropped_total` attr on `data`; multiple sessions ⇒ driver restarted
  mid-file.
- `/events`: `manager.command` rows for device_id=fs740 with `ok=false`
  (error text distinguishes "Device is disconnected" vs FS740 `TimeoutError`
  vs zmq timeout); `manager.log` rows for `manager.driver.failed`
  ("heartbeat stale"), `manager.device.auto_reconnect.*`,
  `manager.drain_cap_hit`, `manager.lifecycle.events_dropped`,
  `hdf.backpressure`, `hdf.flush_batch_deferred`.
- `/sequencer/events`: whether `acquire_traces` ran, which step failed, yaml
  snapshot.
- `/telemetry/fs740/data`: did FS740 telemetry continue? Gaps in `t_wall`
  correlate with driver blocking/restarts; `connected`/`points_pending`/
  `records_read_total` signals directly reveal driver-side reads.
- Live/status side (not in file): `hdf.status` → `errors` map
  (`stream.attach`, `stream.drain`, `bg.reservoir_drop`, `schema.rpc`),
  context counters, `dropped_by_topic`.

---

## 5. Minimal instrumentation fixes (silent → accounted)

1. **Driver, missed scheduled ticks** (runner.py:1214): accumulate `missed`
   into a `scheduled_stream_missed_total` (and a per-plan last-skip reason on
   exception) published in the heartbeat payload. Closes the only loss point
   with *zero* trace today (#1/#3).
2. **Driver, produced-count in heartbeat**: include per-stream
   `last_published_seq` (already `writer._next_seq`) in heartbeat/status.
   Lets any consumer diff produced-vs-written; turns SHM/PUB losses into a
   checkable invariant.
3. **Heartbeat vs blocking device calls (the FS740 killer, #7)**: publish
   heartbeats from a small thread in `DeviceRunner` (it only touches the PUB
   socket — give the thread its own PUB or a queue), or at minimum raise
   `heartbeat_timeout_s` per-device above the max device-call timeout. Right
   now `fetch_timeout_s=10 s` is unreachable behind the manager's 3 s kill.
4. **HDF, count buffer-discard on re-attach/reset** (hdf_writer.py:4523 and
   `_reset_stream_runtime_state`): add `len(buf["data"])` to
   `_stream_dropped_total[key]` before popping (#17/#18) — two lines each.
5. **HDF, per-frame payload-size drop** (hdf_writer.py:5193): filter bad
   frames instead of clearing the batch, bump the counter by the number
   dropped.
6. **HDF, count `ChunkReadyMessage.parse` failures**
   (`stream.chunk_parse_failed`) and the dtype/shape-unknown clear at :5148
   (`stream.meta_missing_dropped`, += n).
7. **HDF, telemetry device-missing-from-schema** (hdf_writer.py:4324): bump
   `telemetry.skipped_no_schema.<device>` and cache the negative result
   briefly (also fixes the schema-RPC-per-batch cost).
8. **Manager, dedup key** (driver_pub.py:729): compare `(shm_name, seq)` not
   just `seq`; optionally count dedup skips.
9. **Manager, per-topic lifecycle drop counter** (pubsub.py:108) so a dropped
   `manager.chunk_ready` is distinguishable from log spam.
10. **shm_ring torn-read guard** (shm_ring.py:377): re-read
    `seq_begin/seq_end` after copying the payload; discard and count on
    mismatch.
11. Fix the `if last_seq and …` gap-count skip (hdf_writer.py:4570) →
    `if last_seq >= 0 and seq > last_seq + 1` with an explicit sentinel.

---

## 6. Tests to add

1. **SHM ring overwrite → seq gap** (`test_shm_ring_wrap_gap`):
   `slot_count=4`, write 10 frames, `read_events(0)` returns exactly the 4
   newest; feed through `_append_chunk_ready_events` and assert
   `seq_gap_total == 6` and the `dropped_total` attr updated.
2. **Manager driver-PUB backlog** — extend
   `tests/test_manager_driver_pub_bounds.py` (cap-hit and chunk-priority
   already covered) with: dedup skip on same-seq-different-`shm_name` (fails
   today → drives fix 8).
3. **HDF buffer overflow policy**: `_buf` with tiny maxlen, overfill, assert
   `dropped_local_messages_total`/`dropped_by_topic` and file attrs after
   flush.
4. **Context pending TTL/overflow**: queue events without context, advance a
   fake monotonic past TTL → rows written with `context_id=-1`,
   `_context_written_minus1_missing` bumped; overflow past
   `context_pending_max_per_stream` → `_context_evicted_pending_overflow`,
   rows still written.
5. **Attach failure and reader reset**: chunk_ready with nonexistent shm →
   `attach_failures_total`; then valid chunk → recovery with
   `startup_seq_gap`. Second test: reader whose `read_events` raises →
   `drain_failures_total`, and **buffered frames are accounted in
   `dropped_total`** (fails today → drives fix 4).
6. **Driver-restart buffer discard**: buffer frames for `(dev, s)`, deliver
   chunk_ready with a new `shm_name`, assert discarded frames land in
   `dropped_total` (fails today → fix 4).
7. **Scheduled stream blocked by long call**: `DeviceRunner` with
   `period_s=0.05` and an RPC handler that sleeps 0.5 s; after one loop,
   assert `scheduled_stream_missed_total ≥ 9` in the next heartbeat (fails
   today → drives fix 1).
8. **Heartbeat continuity under blocking device call** (after fix 3): device
   method sleeping 2×heartbeat_period; assert heartbeats keep arriving —
   pins down the FS740 kill scenario permanently.
9. **Bg flush saturation + reservoir**: block the bg thread (h5_lock held by
   test), fill past `flush_every_n` → `deferred_flush_batches` grows,
   nothing lost; push stream rows past `4×buffer_max_messages` →
   `bg.reservoir_drop` and per-stream `dropped_total`.
10. **Payload-size partial batch** (after fix 5): 3 good + 1 bad frame → 3
    rows written, counter += 1 frame.

---

## Bottom line

Context resolution, ring wraps, deque overflow, and reservoir pressure are
all already accounted for in the file. The genuinely silent paths are
(a) driver-side *sample-never-created* losses — missed scheduled ticks,
blocked loops, and above all the manager's 3-second heartbeat kill of a
driver legitimately blocked in a 10-second FS740 wait — and (b) the writer's
buffer-discard on re-attach/reset and whole-batch payload-size drop. For a
file where FS740 wrote nothing, read
`/streams/fs740/timestamps@chunk_ready_seen_total` first: zero points at the
driver/manager-kill cluster, nonzero points at the writer-side discards.

---

## 7. Addendum from lab-file summary (2026-07-09)

The lab-file summary changes the strongest interpretation of the original
FS740-missing case. The bad files do not look like a pure FS740
trigger/readout failure:

- In `test_measurement.h5`, FS740 telemetry reported
  `records_read_total = 840` and `points_pending = 0`, while
  `/streams/fs740/timestamps` had no actual written stream session/data.
- In `test_new_sequencer.h5`, FS740 telemetry reported
  `records_read_total = 1380` and `points_pending = 0`, again with no FS740
  stream rows written.
- In both bad files, PXIe stream persistence was also incomplete
  (`592/840` and `886/1380` rows), with large sequence-number gaps.
- In both good/control files, FS740 and PXIe wrote matching row counts and
  contiguous sequence ranges.

That pattern says FS740 data reached at least the device driver's own readout
bookkeeping, but did not reliably reach durable HDF stream rows. It also says
the failure is broader than FS740 alone, because PXIe lost stream rows in the
same bad runs.

Important nuance: `records_read_total` is driver telemetry, not a
runner/HDF-level "published stream rows" counter. The stream persistence path
still requires the stream wrapper to return successfully, call
`DeviceRunner.publish_stream`, write the SHM ring, emit `chunk_ready`, have the
manager republish `manager.chunk_ready`, and have the HDF writer attach/drain
and flush rows. So the new evidence narrows the failure to after driver readout
bookkeeping, but not necessarily all the way to HDF writing.

Diagnostic ladder for the affected files, if the attrs can be extracted:

1. `chunk_ready_seen_total == 0`: the HDF writer never saw FS740 stream
   descriptors. With `records_read_total > 0`, focus on the gap between the
   FS740 method return and HDF subscription: stream wrapper conversion,
   `publish_stream`, driver PUB, manager ingest/dedup, manager external PUB,
   or writer subscription/HWM loss. This is less consistent with
   "FS740 never read anything" than the original ranking.
2. `chunk_ready_seen_total > 0` and `events_read_total == 0`: descriptors
   reached the writer, but SHM frames were not recovered. Check
   `attach_failures_total`, `drain_failures_total`, `startup_seq_gap`, stale
   `shm_name`, and session boundaries from driver restarts.
3. `events_read_total > 0` and `rows_written_total == 0`: the HDF writer read
   stream events but did not persist them. Check `payload_size_failures_total`,
   disabled-device state, dtype/shape metadata resolution, and pending context
   state.
4. PXIe gaps should be cross-checked against `first_seq`, `last_seq`,
   `startup_seq_gap`, `seq_gap_total`, session groups, and per-dataset
   `dropped_total`. If the dataset has missing sequence numbers but the attrs
   do not account for them, the file is exposing an accounting gap in addition
   to data loss.

One code-level caveat found during this follow-up: context loss is not quite as
closed as section 2 currently implies. `_expire_pending_context` writes
unresolved events with `context_id=-1` after the TTL during the main loop, but
`_drain_pending_to_file()` does not force-flush still-pending
`_stream_pending_by_seq` entries before stop/rotate/close. If a run stops
before the TTL sweep and all recovered stream events are still waiting for
context, `events_read_total` can exceed `rows_written_total` without rows being
persisted at final drain. This is a plausible gap to test or fix later; no code
change was made for this addendum.

Best next inspection target remains the FS740 stream group attrs, but the lab
summary makes the full triplet essential:

- `chunk_ready_seen_total`
- `events_read_total`
- `rows_written_total`

Those three counters locate the loss boundary: before HDF descriptors, between
descriptors and SHM drain, or between SHM drain and dataset write.

---

## 8. Direct inspection of added HDF files (2026-07-09)

Two HDF files were added under `docs/` and inspected directly:

- `test_new_sequencer.h5`
- `test_new_sequencer_multiple.h5`

Both files use `schema_version = 5`. They do **not** contain the newer
per-stream diagnostic attrs proposed above (`chunk_ready_seen_total`,
`events_read_total`, `rows_written_total`, attach/drain failure counters, etc.),
so the loss boundary cannot be pinned to one exact writer stage from the files
alone. The available evidence is still enough to confirm the lab summary.

### `test_new_sequencer.h5` (bad run)

- `/streams/fs740/timestamps` exists only as an empty stream group. It has no
  `session_*` group and therefore no persisted FS740 stream rows.
- `/telemetry/fs740/data` has 950 telemetry rows. Its final values are
  `records_read_total = 1380`, `points_pending = 0`, and
  `records_flushed_total = 0`; `points_pending` peaked at 6 during the run.
- `/telemetry/pxie5171/data` ends at `records_acquired_total = 1380` and
  `last_frame_seq = 1380`.
- `/context_table/data` has 92 context rows, IDs 0 through 91. With
  `n_traces = 15`, that is exactly `92 * 15 = 1380` expected shots.
- `/streams/pxie5171/waveforms/session_001` has only 886 rows. Its sequence
  numbers span 1 through 1380, so 494 sequence numbers are missing inside the
  span. There are 55 discontinuities.
- PXIe context IDs are resolved for the rows that were written: no
  `context_id = -1` rows were present. However, only 86 of the 92 contexts have
  any PXIe rows, six contexts are absent entirely (`25`, `39`, `41`, `47`,
  `53`, `85`), and 64 contexts have something other than the expected 15 rows.
- HDF-level drop accounting did not report this: root
  `dropped_local_messages_total = 0`, root `dropped_event_messages_total = 0`,
  and PXIe stream `dropped_total = 0` despite the 494 missing stream rows.
- The event log contains sequencer and command-interceptor errors, including a
  `wait_until timeout`, but these do not explain the central mismatch:
  FS740 and PXIe device telemetry both reached 1380 acquired/read records while
  FS740 persisted 0 stream rows and PXIe persisted 886.

### `test_new_sequencer_multiple.h5` (good/control run)

- `/streams/fs740/timestamps/session_001` has 4140 rows with contiguous
  sequence numbers 2761 through 6900.
- `/streams/pxie5171/waveforms/session_001` also has 4140 rows with contiguous
  sequence numbers 2761 through 6900.
- `/context_table/data` has 276 context rows, IDs 184 through 459. With
  `n_traces = 15`, that is exactly `276 * 15 = 4140` expected shots.
- Both FS740 and PXIe stream context IDs cover all 276 contexts, every context
  has exactly 15 rows, and neither stream has `context_id = -1`.
- FS740 telemetry starts at `records_read_total = 2760` and ends at 6900;
  PXIe telemetry starts at `records_acquired_total = 2760` and ends at 6900.
  The delta is 4140 for both devices, matching the persisted stream rows.
- Both stream datasets report `dropped_total = 0`, consistent with the complete
  contiguous sequences in this control file.

### Interpretation from the actual files

The added files confirm the summary: the bad run is not consistent with a pure
FS740 trigger/readout failure. In the bad file, FS740 telemetry says 1380
records were read and the final pending count is zero, but no FS740 stream
session was ever persisted. PXIe independently reached 1380 acquired records
but only 886 waveform rows were persisted, with missing sequence numbers spread
through the run rather than only truncated at the end.

Context resolution is unlikely to be the primary explanation for the PXIe loss:
the written PXIe rows have resolved context IDs and no `-1` context rows. The
problem is broader than FS740 and appears to sit in the stream publication,
manager relay, SHM drain, or HDF writer persistence path after device-level
acquisition/readout bookkeeping.

The files also show a diagnostics gap in the current HDF format/version used
for these runs. The bad file contains large stream sequence loss, but the
stored drop counters remain zero and the FS740 empty stream has no attrs that
say whether chunk descriptors were never seen, seen but not drained, or drained
but not written. Future runs need the explicit per-stream writer counters from
the diagnostic ladder above to locate the loss boundary without inference.

---

## 9. Root-cause investigation from the files + code archaeology (2026-07-09)

Deep-dive over `test_new_sequencer.h5` (bad) and `test_new_sequencer_multiple.h5`
(good) cross-checked against the code the lab was plausibly running. The
deployed writer for both runs is bracketed to the `cc5f36c` era (07-08 00:25):
schema_version 5 (bumped `fd72a36` 06-26), `t_wall_recv` present, but **no**
`#113`/`#114`/`#115`/`#116` (all later that day). The event log proves #113
was *not* deployed: its list-vs-dict `TypeError` would have failed **every**
`stream__*` RPC with a `lifecycle_error` reply (manager.py `_run_lifecycle`),
yet the bad run completed all 92×15 traces with zero fs740/pxie command
failures in a `failures_only` event log.

### Timeline / process identity (from the files)

- Bad run 16:21:58 → ~16:37:55, good run 16:46:34 → ~17:05 (CDT, 07-08).
- Driver telemetry `seq` is continuous across both files (fs740: 118→1067,
  then 1582→2689; pxie: 116→1063, then 1578→2684) ⇒ **the same driver
  processes — and therefore the same manager — served the bad and the good
  run.** Drivers had (re)started ~2 min before the bad run
  (`records_read_total` starts at 0; telemetry seq starts at ~118).
  Whatever failed is timing/state-dependent, not a static code difference.
  (The writer's own process telemetry publishes `seq = -1`, so writer process
  identity across the two files cannot be established — a diagnostics gap
  worth fixing.)
- Bad-run throughput ≈ 1.4 traces/s average with long stalls; good run
  ≈ 5.9 traces/s sustained. The bad run was a first-run-after-restart with
  degraded trigger timing (`wait_until timeout` at its end).

### PXIe: 494 missing rows = whole flush batches silently discarded (CONFIRMED)

The written pxie frames arrive in ~10 Hz bursts: contiguous-frame spacing has
median 0.109 s, p99 0.72 s. The 55 holes, in contrast, **all span ≥ 1.015 s**
of wall time and cluster at ~1.0–1.6 s, ~2.0 s, ~3.4–4.3 s, 5.7 s, 6.2 s —
i.e. integer multiples of the writer's `write_every_s = 1.0` (root attr).
Frames were lost in units of **whole flush intervals**, never smaller.

Mechanism (code path exists identically at `cc5f36c` and on HEAD,
hdf_writer.py:1475-1481): the bg thread wraps `_dispatch_bg_request` in
`except Exception` → `_bump_error("bg._FlushBatch.failed")` → **the batch is
dropped**. `_handle_flush_batch` writes context rows → telemetry rows → event
rows → **stream buffers last** → flush; an exception in the stream-write stage
(or final flush) therefore loses *only* that batch's stream frames while
telemetry survives — exactly matching the file (fs740/pxie telemetry ~99%
complete, event log intact, stream holes only).

Every alternative is excluded by the file itself:

- Ring wrap / lost-notification seq gaps → `dropped_total` would be > 0
  (gap accounting has existed since init); it is 0, and the ring is 1024
  slots (~100 s at 10 Hz) so wraps were impossible.
- Lost `manager.chunk_ready` + ring backfill → backfilled frames lose their
  per-seq context (carried only in the lost descriptor; `_resolve_context_for_seq`
  is exact-seq, no fallback) → pending → TTL 5 s →
  `_flush_pending_as_unknown` writes them with `context_id = -1`. The file
  has **zero** −1 rows.
- Pending-cap overflow → also writes −1. Reservoir drop → bumps
  `dropped_total`. Bg-queue saturation → lossless deferral in this era
  (`e97e446`, 07-01). Drain-failure reset / re-attach → would start
  `session_002`; only `session_001` exists. Device-disable flapping → gates
  telemetry identically (present), and toggles are RPC-only.

What raised the exception ~55 times during the bad window (and zero times
9 minutes later) is not recoverable from the file — it lives only in the
writer's live `hdf.status` errors map and `_record_exception` output. The
loss path itself, however, is unambiguous, and it is **still live on HEAD**:
a failed batch is neither retried, nor restored to the reservoir, nor counted
into any persisted attr. (Post-#115, the symptom would at least be visible as
`events_read_total > rows_written_total`.)

### FS740: zero chunk_ready messages processed by the writer (SUPERSEDED — see §10)

> **2026-07-09 (later): this subsection's conclusion is wrong.** The
> "no `session_*` group ⇒ zero chunk activity" inference does not hold:
> `_write_stream_buffers_batch` raises at `np.dtype(...)` (hdf_writer.py:5152)
> *before* `_ensure_stream_dataset` (5158) ever creates the session group, so a
> writer that processed every chunk can still leave an empty stream group.
> `testing_with_intentional_sequencer_failures.h5` (with #115 counters)
> proves the relay is lossless and pins the real, deterministic root cause —
> see §10. The worker-thread pump below remains a latent thread-safety
> concern but is exonerated for these losses.

- The empty `/streams/fs740/timestamps` group is created by stream-*metadata*
  ingestion (hdf_writer.py:2243 `require_group`), not by chunk handling —
  so its existence is consistent with zero chunk activity. No `session_*`
  group means `_ensure_chunk_ready_reader` never ran: the writer processed
  **zero** fs740 chunk_ready messages in ~16 minutes. (Not disabled-device
  filtering: telemetry shares the same `_is_device_enabled` gate and was
  written.)
- Driver side is exonerated: all 1380 `stream__read_timestamp_record` RPCs
  returned OK (the wrapper publishes ring+PUB *before* the reply, and a
  publish failure would fail the RPC — `failures_only` logging kept zero such
  failures), so 1380 ring writes and 1380 driver-PUB chunk_ready messages
  happened.
- The bus was healthy for everything else: fs740 telemetry (same driver PUB →
  same manager SUB → same external PUB → same writer SUB) is gap-free;
  pxie chunk_readys and manager.log events arrived.

⇒ All 1380 fs740 chunk_readys were lost between the manager's SUB ingest and
the writer's topic handler — deterministically, all run — on processes that
relayed them perfectly 9 minutes later. Leading candidate, consistent with
the timing signature: **worker-thread pump consumption during blocking stream
RPCs**. `_pump_manager_subscriptions` (rpc_calls.py:117) drains the *shared*
`manager._sub` socket from lifecycle worker threads whenever a device RPC
waits > 50 ms, concurrently with the main loop's drain — a genuine ZMQ
thread-safety violation (concurrent `recv_multipart` on one SUB socket can
interleave/tear multipart frames). In the degraded bad run, every fs740
chunk_ready lands inside the *next* pxie read's long blocking window (pump
hot), while pxie's chunk_ready lands inside the usually-instant fs740 read
(`points_pending > 0` in 137/950 samples ⇒ reply < 50 ms ⇒ pump idle) —
matching the observed 100% fs740 vs ~36% pxie suppression, and vanishing in
the fast good run where no RPC waits long enough to engage the pump. Not
provable from the file alone; discriminating evidence to pull on the lab
machine:

1. `hdf.status` → `errors` map from that session (if the writer still runs or
   was logged): `stream.attach`, `stream.drain`, `bg._FlushBatch.failed`
   counts.
2. Manager stderr/log 16:20–17:05: `manager.unknown_driver_pub` /
   decode-failure warnings (torn multiparts), `manager.lifecycle.events_dropped`.
3. Whether the hdf_writer process restarted between 16:38 and 16:46.

### Immediate code actions (beyond §5)

1. **Bg batch failure must not discard data** (hdf_writer.py:1475): on
   `_FlushBatch` failure, add the batch's stream frames to per-stream
   `dropped_total` (or better: restore them to the reservoir for retry),
   persist a `bg_flush_batch_failures_total` root attr, and per-key
   try/except inside `_write_stream_buffers_batch` so one stream's write
   error cannot discard sibling streams' frames.
2. ~~Stop draining `manager._sub` from worker threads~~ (rpc_calls.py:117):
   demoted — the relay is proven lossless in §10's run. Still a genuine ZMQ
   thread-safety violation worth fixing on hygiene grounds, but no observed
   loss is attributed to it anymore.
3. ~~Fix #113 before the next lab deploy~~ — **DONE**, PR #117 (`ae0eac4`,
   merged `e49b2da`), with real-wrapper-shape regression tests.
4. **Writer process telemetry should publish a real `seq`** (it is −1 in
   files), so writer restarts are provable from acquired files the way driver
   restarts are.
5. **Fix the structured-dtype schema poisoning** — the actual root cause of
   every fs740/pxie stream loss in §8–§10; see §10 for the required changes.

## 10. ROOT CAUSE CONFIRMED: structured-dtype schema poisoning → bg flush batch discard (2026-07-09, from `testing_with_intentional_sequencer_failures.h5`)

A fresh run on post-#117 master (file created 07-09 11:34:10, strict
expectations active) finally carries the #115 counters, and they localize the
loss exactly:

| stream | chunk_ready_seen | events_read | rows_written | attach/drain/payload failures | seq_gap |
|---|---|---|---|---|---|
| fs740/timestamps | 2760 | 2760 | **0** | 0 | 0 |
| pxie5171/waveforms | 2760 | 2760 | **1747** | 0 | 0 |

The manager relay and the ring reads are **lossless** (2760/2760 both
streams, `first_seq=1`, `last_seq=2760`, zero gaps). Everything is lost
*after* `read_events`, in the bg flush. All 101 pxie holes span ≥ 1.009 s
wall time — whole flush batches again. `acquisition_ok = False` with 195
strict errors ("required stream seen but no rows written") — #115 did its
job.

### The mechanism (all code refs on master)

1. At file open, config replay (`hdf_writer.py:2522-2534`) parses fs740's
   `stream_calls` (`kind: records` + `fields`, from `devices/fs740.yaml`)
   and stores a **real structured `np.dtype`** in `_stream_schema`
   (hdf_writer.py:5442-5449). This is the only *good* schema source.
2. The first fs740 `chunk_ready` of a fresh driver session attaches a new
   ring reader — and `_ensure_chunk_ready_reader` **pops that schema entry**
   (hdf_writer.py:4524).
3. The events-read path then repopulates it as
   `{"dtype": str(reader.layout.dtype), ...}` (hdf_writer.py:4594-4598).
   For pxie's scalar `int16` the `str()` round-trips. For fs740's 11-field
   record dtype it produces `"[('timing_metric', '<i4'), …]"` — which
   **`np.dtype()` cannot parse back** (verified: raises `TypeError`).
4. Every flush batch snapshots that poisoned string into `stream_meta`
   (hdf_writer.py:1808-1817); `_write_stream_buffers_batch` calls
   `np.dtype(dtype_raw)` (hdf_writer.py:5152) → `TypeError` → the exception
   escapes to `_bg_thread_run` (hdf_writer.py:1475-1481) → **the entire
   batch is silently discarded** (§9 action 1, live-only counter).
5. Batch iteration follows `_stream_buffers` insertion order. When pxie's
   frames entered the buffer before fs740's, pxie's rows were already
   written when the fs740 raise hit; otherwise they died with the batch.
   Hence fs740 = 0 rows always (it always raises at its own turn), pxie =
   partial. The raise also precedes `_ensure_stream_dataset`
   (hdf_writer.py:5158), so no `session_*` group is ever created for fs740 —
   the empty-group signature §9 misread as "zero chunks processed".

### Why bad-then-good on 07-08 (the state dependence, solved)

Ring readers persist for the writer-process lifetime (only dropped at close,
hdf_writer.py:2794, or on drain failure, 4551) and the ring name embeds the
driver PID. So:

- **First run after a driver (re)start** → new ring name → attach → schema
  pop (step 2) → poisoned → fs740 0 rows, pxie collateral. Both bad files
  are first-runs-after-restart (`records_read_total` starts at 0).
- **Subsequent run, same driver** → `reader.name == shm_name` → attach
  early-returns *without* popping → the config-replay schema (real dtype
  object, refreshed at every file open) survives → **everything is written**.
  That is exactly the good 16:46 run (4140/4140 on both streams).

Quantitative cross-check: pxie survival is 886/1380 = 64.2 % (07-08 bad) vs
1747/2760 = 63.3 % (07-09 bad) — the same deterministic insertion-order ratio
across two runs on different code versions. The mechanism has existed since
`8a81fa9` (05-13, structured record support); `e97e446` (07-01) routed the
poisoned string into the bg batch path.

### Fixes required

1. **Round-trippable dtype in the reader-derived schema**
   (hdf_writer.py:4594-4598): store `reader.layout.dtype` (the `np.dtype`
   object — it only crosses a thread queue, never a process boundary), not
   `str(...)`. This is the one-line root fix.
2. **Don't discard the config-derived schema on attach**
   (hdf_writer.py:4524): the config schema is authoritative; popping it in
   favour of a reader-derived fallback is backwards. Keep it, or overwrite
   only with equal-or-better information.
3. **§9 action 1 unchanged and now demonstrated**: per-key try/except in
   `_write_stream_buffers_batch` + no whole-batch discard + persisted
   `bg_flush_batch_failures_total`. One stream's poisoned schema destroyed a
   *healthy* sibling stream's data for two days without a single persisted
   error.
4. **Regression test**: a records-kind stream (structured dtype) whose
   schema is populated via the reader-fallback path (attach → pop → read)
   must still flush; plus a mixed batch where one stream's write raises must
   still write the sibling stream's frames.
