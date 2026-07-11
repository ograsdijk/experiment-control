# Performance, Concurrency & I/O Review — `experiment-control`

**Date:** 2026-07-09
**Scope:** static review of `src/experiment_control` (~61 k lines). No code was modified. Line numbers are approximate against the working tree at review time.

**Architecture recap (for reference in the findings):** one OS process per device driver (`DeviceRunner`, single-threaded ZMQ REP/PUB loop) → a **Manager** process (single-threaded poll loop + a 32-thread lifecycle executor) → a **DeviceRouter** process (per-device worker threads, each with its own REQ socket directly to the driver) → clients (sequencer, TUI, FastAPI gateway, HDF/Influx/analysis processes) that talk to the router on port 6000. Bulk data flows driver → shared-memory ring → HDF writer / stream analysis, with only small descriptors over ZMQ.

---

## 1. Executive summary

The overall architecture is sound and shows deliberate engineering: per-device driver processes, per-device router workers, a lifecycle thread pool with per-device locks, bounded queues with drop counters everywhere, shared-memory rings for bulk data, DNS kept off ZMQ I/O threads, orjson on hot paths, and good failure-isolation defaults. Most classic mistakes (global device locks, unbounded buffers, DNS on the poll loop, reconnect-per-command) are already avoided.

The main problems are **latency quantization and hidden coupling on the command path**, not raw throughput:

1. **~30–50 ms added to every device command** because the DeviceRouter only sends worker replies after its 50 ms poll expires (replies land in a plain `queue.Queue` that cannot wake the poller). For a sequential sequencer this is the dominant per-step cost — a 1 000-step scan pays +30–50 s.
2. **Every device command's reply is gated on a blocking RPC to the Manager** (the `manager.command` audit event is published via a full request/reply round-trip *before* the reply is enqueued). Any Manager main-loop stall therefore delays commands to **all** devices.
3. **The Manager main loop can stall for seconds**: auto-reconnect performs blocking disconnect+connect device RPCs inline in the supervision tick (~2.5 s per attempting device), and federation forwards block up to the peer timeout.
4. **A genuine thread-safety violation was found and fixed**: lifecycle worker threads could drain the Manager's SUB sockets (`_pump_manager_subscriptions`) concurrently with the main poll loop. The pump now returns immediately off the main thread.
5. **Cross-device parallelism is now opt-in at the sequencing layer**: bounded `parallel` branches can overlap distinct device/process calls while each atomic branch remains internally sequential.
6. Within one device, **telemetry/scheduled-stream reads share the single driver thread with command RPCs**, so a sequencer command can wait behind a multi-register serial telemetry sweep.

Data handling (HDF writer with background flush thread, Influx with background HTTP thread, SHM rings) is in good shape; findings there are second-order (an extra copy per SHM write, a seqlock gap, HTTP connection reuse).

---

## 2. Prioritized findings table

| # | Sev | Location | Issue | Impact | Recommended action |
|---|-----|----------|-------|--------|--------------------|
| F1 | Critical | ✅ **FIXED** — `_manager/rpc_calls.py:150` | SUB sockets drained from lifecycle worker threads concurrently with main loop | Undefined behavior in libzmq; corruption/crashes under load | Main-thread guard makes the pump a no-op on lifecycle workers |
| F2 | High | ✅ **FIXED** — `processes/device_router.py` (run loop + `_BaseWorker`) | Command replies quantized to 50 ms router poll | +30–50 ms on *every* device command; dominates per-shot overhead | inproc PUSH→PULL doorbell wakes the poll loop the instant a reply is enqueued |
| F3 | High | ✅ **FIXED** — `processes/device_router.py:840, 464-473` | Blocking `manager.command` publish RPC on the reply critical path | Couples all device command latency to Manager loop health | Audit publish moved to a bounded background thread (`_AsyncEventPublisher`) |
| F4 | Critical | ✅ **FIXED** — `_manager/process_supervision.py:1188-1290` | Auto-reconnect does blocking device RPCs on the Manager main loop | ~2.5 s loop stall per attempt; stalls all RPC, HB ingestion, and (via F3) all device commands | Reconnect I/O dispatched to the existing lifecycle executor under the per-device lock |
| F5 | High | ✅ **FIXED** — `sequencer/runtime.py:465-488` | `tick()` runs until a sleep/wait step; RPC drained only between ticks | Pause/stop/status unresponsive for the duration of a sleepless scan | Per-tick step/time budget (10 ms / 200 steps), atomic blocks unaffected |
| F6 | High | ✅ **FIXED** — `sequencer/runtime.py`, `sequencer/sequencer.py` | `parallel` unsupported → all cross-device sets sequential | Independent devices serialized; per-point latency scales with device count | Bounded eight-worker parallel dispatch with disjoint-target validation and sequential atomic branches |
| F7 | Med-High | `_driver/runner.py:237-279` | Telemetry + scheduled streams run inline with RPC in one driver thread | Commands wait behind serial telemetry sweeps / trace acquisitions | Prioritize RPC between telemetry calls or defer telemetry while RPC pending |
| F8 | Medium | ✅ **FIXED** — `sequencer/sequencer.py`, `sequencer/runtime.py` | `set_stream_context` blocking retry loop (up to 6 s) + per-context `hdf.streams.expect` RPC | Inline shot-path overhead; sequencer fully blocked during retries | Retry is now tick-driven (`_wait_state`-style non-advancing poll); per-stream RPCs dispatched concurrently on a worker pool |
| F9 | Medium | ✅ **FIXED** — `_manager/lifecycle.py:253-264`, `process_supervision.py:284-293` | Shutdown stops drivers sequentially, 1 s timeout each | Shutdown scales linearly with unresponsive devices | Fan out via lifecycle executor |
| F10 | Medium | ✅ **FIXED** — `federation/hub.py` (`_FederationForwardWorker`, `try_dispatch_device_forward`/`try_dispatch_process_forward`) | Federation forward blocks main loop up to peer timeout; new DEALER per call | Slow peer stalls Manager; per-command connect cost | Per-mirror worker: one dedicated thread + persistent socket + bounded queue per mirrored device/process, off the poll loop |
| F11 | Medium | `processes/influx_writer.py:1038-1235` | One HTTP thread for all destinations; no keep-alive | Slow destination (5 s timeout) delays other destinations' batches | Per-destination worker; reuse connections |
| F12 | Medium | `processes/stream_analysis.py:4788` | Fits run inline in the SUB drain loop | Slow fit → SUB backlog → dropped chunk descriptors | Offload fits to a worker; keep drain hot |
| F13 | Medium | ✅ **FIXED** — `shm/shm_ring.py` | Extra full copy per SHM write; reader lacked post-copy seq re-check | Doubled memory bandwidth on streams; rare torn read on ring overrun | Direct NumPy destination view plus pre/post sequence validation |
| F14 | Medium | `_manager/device_routing.py:61-101` | Manager-side `type:"command"` route blocks main loop (1.5 s) | Latent: any direct internal-RPC command stalls the Manager | Route through lifecycle executor or reject with redirect |
| F15 | Low-Med | ✅ **FIXED** — `sequencer/sequencer.py:2952`, `sequencer/runtime.py` | Fixed 50 ms poll quantizes sleeps and wait_until sampling | `sleep: 0.01` becomes ~50 ms; jitter per shot | Poll timeout now computed from `SequencerRuntime.next_poll_timeout_ms()` (next sleep/wait deadline, capped at 50 ms) |
| F16 | Low-Med | `_driver/runner.py:930`, `drivers/linien_driver.py:30` | Vendor `connect()` runs inline in REP handler with no driver-side timeout | Slow connect (SSH/rpyc) stops heartbeats → spurious demotion; RPC timeout races real success | Document/bound connect; raise per-device connect timeouts |
| F17 | Low | `manager.py:1286` | SUB connects to dead driver PUB endpoints never disconnected | Slow accumulation of reconnecting endpoints over many restarts | Disconnect on driver exit |
| F18 | Info | various | Positive: bounded caches, drop counters, loop-stall events, journal batching | — | Keep |

---

## 3. Detailed findings

### F1 — Manager SUB sockets drained from worker threads (thread-safety violation)

- **Status:** ✅ **FIXED**. See *Resolution* below.
- **Severity:** Critical (correctness/stability, with performance symptoms). **Confidence:** Confirmed from code; runtime impact requires verification.
- **File:** `src/experiment_control/_manager/rpc_calls.py` — `RpcCallsMixin._pump_manager_subscriptions` (L148–154), `_call_device_rpc` (L241–312, pump wired at L261–267); `src/experiment_control/manager.py` — `_run_lifecycle` (L1683–1712), `_run_auto_connect` (L1657–1681).
- **Behavior:** `_call_device_rpc` waits for the driver's reply in `_blocking_call_with_pump`, calling `pump_fn=self._pump_manager_subscriptions` on every 50 ms poll-timeout. The pump drains `self._sub`, `self._process_hb_sub`, `self._process_data_sub`. Lifecycle operations (`device.connect/disconnect/driver.*/recover`) and auto-connect-on-register run `_call_device_rpc` **on lifecycle executor threads** while the main thread's `_pump_once` polls and recvs on the same three SUB sockets. Event *publishing* has an off-thread redirect (`pubsub.py:88–89` checks `_main_thread_id`), but the socket *drain* has no such guard. With two devices connecting concurrently, two worker threads plus the main thread can all touch one SUB socket.
- **Impact:** libzmq sockets are not thread-safe; concurrent recv/poll from multiple threads is undefined behavior — assertion crashes in libzmq, interleaved multipart frames, or silently lost/corrupted heartbeats and telemetry. Failure mode is load-dependent and hard to reproduce, and would present as "random" manager crashes or bogus stale-heartbeat demotions during bulk connects.
- **Unrelated devices forced to wait:** Indirectly — a crash or corrupted heartbeat affects everything.
- **Subsystem:** locking/threading (ZMQ).
- **Root cause:** the pump was designed for the main-thread call path (federation forward, inline command route) and was reused unchanged when lifecycle calls moved to worker threads.
- **Recommendation:** in `_pump_manager_subscriptions`, return immediately when `threading.get_ident() != self._main_thread_id` (mirroring the pubsub guard). Worker threads don't need to pump — the main loop is still running and drains those sockets itself.
- **Risks/assumptions:** none significant; the pump-from-worker adds no value since the main loop polls concurrently.
- **Hardware testing:** not required (reproducible with dummy drivers under concurrent connects).
- **Measurement:** stress test with 20 dummy drivers auto-connecting in parallel while telemetry flows; watch for libzmq assertions, heartbeat gaps, and `manager.process.heartbeat_error` events before/after the guard.

**Resolution (implemented):** `_pump_manager_subscriptions` now returns immediately when called outside the Manager's main thread. Lifecycle workers continue waiting for their device RPC normally while the main poll loop remains solely responsible for draining the three SUB sockets. `tests/test_manager_lifecycle_parallel.py` verifies that worker-thread calls do not poll or receive from any subscription socket and that main-thread calls still drain all three channels.

### F2 — DeviceRouter reply latency quantized to the 50 ms poll

- **Status:** ✅ **FIXED**. See *Resolution* below.
- **Severity:** High. **Confidence:** Confirmed.
- **File:** `src/experiment_control/processes/device_router.py` — `DeviceRouter.run` (L2008–2018), `_drain_replies` (L1660–1698), `_BaseWorker._enqueue_reply` (L274–289); `src/experiment_control/utils/zmq_helpers.py` — `poll_and_drain` (L134–148).
- **Behavior:** the router main loop is `poll_and_drain(poller, 50, …)` then `_drain_replies()`. Worker threads deposit completed replies into a plain `queue.Queue`; nothing wakes the ZMQ poller when a reply arrives. Sequence for a lone client (the sequencer): request arrives → poll wakes → dispatched to the device worker → main loop re-enters `poll(50 ms)` → worker finishes the device RPC in ~1–20 ms → reply sits in the queue until the 50 ms poll expires → reply sent.
- **Impact:** every routed device command pays roughly `50 ms − device_rpc_duration` of pure scheduling delay whenever router traffic is sparse — which is precisely the sequential-sequencer case. A 1 000-step scan of `set`/`call` steps pays +30–50 s. Under concurrent load the effect shrinks (other events wake the poll), which makes it deceptive to benchmark casually.
- **Unrelated devices forced to wait:** No (per-command penalty, all devices equally).
- **Subsystem:** general scheduling / ZMQ.
- **Root cause:** thread→loop handoff via a Python queue invisible to `zmq_poll`.
- **Recommendation:** add an inproc wake-up channel: each worker sends an empty frame on a shared `PUSH`→`PULL` inproc socket after `_enqueue_reply`; register the `PULL` in the poller and call `_drain_replies` when it fires. (Alternative: have workers write replies directly to a per-worker inproc DEALER that the main loop proxies; do *not* share the ROUTER socket across threads.)
- **Risks/assumptions:** minimal; keep the queue as the data channel and use inproc only as a doorbell to avoid message-ordering questions.
- **Hardware testing:** not required.
- **Measurement:** client-side round-trip time of a no-op command (`status` or `get`) through the router, idle system: expect a ~50 ms floor today, ~2–5 ms after the fix. This single number validates the biggest per-shot win.

**Resolution (implemented):** The thread→loop reply handoff now has an inproc doorbell so the poller wakes as soon as a reply is ready, instead of on the next 50 ms poll expiry.

- **Doorbell channel.** `DeviceRouter` owns a unique inproc endpoint (`inproc://router-reply-doorbell-<uuid>`) and binds the `PULL` end on the main thread in `run()` — before any worker is created (workers spawn lazily inside the loop), so a worker's `connect` can never race the `bind` (inproc requires bind-before-connect). The `PULL` is registered in the poller and drained by `_drain_doorbell`.
- **Worker side.** `_BaseWorker._enqueue_reply` now calls `_ring_doorbell()` right after putting the `_ReplyItem` on `_reply_queue`. `_ring_doorbell` lazily builds a `PUSH` socket **on the worker's own thread** (ZMQ sockets are not thread-safe; the socket is created, used, and closed solely there — preserving the F1 invariant) with `LINGER=0`, and sends an empty frame `NOBLOCK`. Every worker constructor (`_DeviceWorker`, `_ProcessWorker`, `_MirroredDeviceWorker`, `_ManagerWorker`) forwards the router's `doorbell_endpoint`; each closes its `PUSH` in its `run()` teardown (`_ManagerWorker.run` gained a `try/finally` to do so).
- **Data path unchanged / no ordering risk.** The doorbell is a pure *signal* — the reply payload still travels on `_reply_queue`, and the main loop still calls `_drain_replies()` every iteration. So the empty-frame send is strictly best-effort: if the `PUSH` HWM is full or the send fails, or the doorbell endpoint is absent, the worst case is simply the old 50 ms-quantized latency for that one reply, never a lost or misordered reply. Multiple rings coalesce — one `_drain_doorbell` clears however many frames arrived, so the loop cannot busy-spin.
- **Result:** for the sequential-sequencer (lone-client) case the ~50 ms per-command scheduling floor collapses to the wake-up latency (sub-millisecond in practice); a 1 000-step `set`/`call` scan sheds ~30–50 s of pure poll quantization.
- **Tests:** `tests/test_device_router_reply_doorbell.py` covers both the doorbell primitive and the production router wiring. The unit cases verify that enqueuing a reply makes the `PULL` readable, the reply remains on `_reply_queue`, doorbell frames drain fully, and a missing endpoint degrades safely. The router-level regression runs the real `DeviceRouter` poll loop with its idle poll stretched to 500 ms, delays a real device worker reply by 20 ms, and requires the external ROUTER reply within 300 ms while confirming `_drain_doorbell` ran. This pins worker endpoint forwarding, PULL binding/registration, handler dispatch, and reply draining together. Full `unittest discover`: 880 passed, 1 skipped.

**Post-fix code review (8-angle) — outcome.** Correctness, altitude, and conventions passes came back clean; the `AGENTS.md` gates were re-checked against the change:

- `ruff check src tests examples` passes; the tests are `TestCase`-based and are picked up by the CI `unittest discover` runner (not just pytest).
- `mypy src/experiment_control`: the changed file `device_router.py` has **zero** type errors — the annotations added by this fix introduce none.
- Wire contracts unchanged: the doorbell is a purely internal inproc signal; the `manager.command` topic, response envelope, and all downstream-importable symbols are untouched.
- **Applied:** removed a redundant `setsockopt(zmq.SNDTIMEO, 0)` on the doorbell `PUSH` — the send already passes `zmq.NOBLOCK`, so the timeout had no effect.
- **Not fixed (consistent with existing house style, no action taken):** the small `_close_*` socket helpers duplicate a pattern already repeated ~6× in this file; per-subclass `_close_doorbell` teardown mirrors the existing per-subclass `_stop_event_publisher` convention.

**Deferred issue surfaced during review (outside F2 scope):** `mypy` baseline has regressed. `AGENTS.md` documents 2 known errors (as of 2026-06-18), but the tree now reports **9** errors in 5 files — `_manager/request_routing.py:67` (`_route_manager_ping` attr-defined), `_manager/rpc_calls.py:341` (`_ingest_chunk_ready` attr-defined), `processes/hdf_writer.py:3972/3975/3979` (`Any | None` assignment/arg-type), `manager.py:2046/2095` (`_telemetry_last_recv_mono` attr-defined), plus the 2 documented baseline errors (`manager_network.py:153`, `manager.py:424`). None are in `device_router.py` (unrelated to F2), but the "do not regress mypy" gate is already red and should be triaged separately.

### F3 — Blocking `manager.command` audit publish on the command reply path

- **Status:** ✅ **FIXED** (branch `fix/f3-async-audit-publish`). See *Resolution* below.
- **Severity:** High. **Confidence:** Confirmed.
- **File:** `src/experiment_control/processes/device_router.py` — `_DeviceWorker.run` (publish at L840/L864 before `_enqueue_reply` L866); `_ProcessWorker.run` (L467 before L468); `src/experiment_control/manager_client.py` — `publish_event` → `self.call(req)` (L197–217), a full DEALER request/reply with the default 1 500 ms timeout.
- **Behavior:** after each device call, the worker publishes the `manager.command` audit event by making a **blocking RPC to the Manager** (`manager.events.publish`), and only then enqueues the client reply.
- **Impact:** (a) adds a Manager round-trip (~1–5 ms healthy) to every command; (b) when the Manager's main loop is stalled (F4, F10, F14, startup, journal hiccups), **every device command on every device** waits up to 1 500 ms for its audit publish — a global coupling through what should be observability; (c) combined with F2, the reply then waits for the next poll expiry too.
- **Unrelated devices forced to wait:** Yes — all devices' replies are serialized behind Manager main-loop availability.
- **Subsystem:** network/scheduling.
- **Root cause:** audit event delivered via RPC on the critical path rather than fire-and-forget.
- **Recommendation:** enqueue the client reply *before* publishing the audit event; better, publish `manager.command` over a PUB socket (the process-data PUB channel already exists in `ManagedProcessBase._publish_process_event`, which is `NOBLOCK`) or batch events on a background thread.
- **Risks/assumptions:** audit ordering vs. reply becomes decoupled; the journal already tolerates async arrival. If strict "journal before ack" is a requirement (operator question), keep ordering but make the transport non-blocking with a bounded local buffer.
- **Hardware testing:** not required.
- **Measurement:** timestamp deltas inside `_DeviceWorker.run` (device recv → publish done → reply enqueued), plus command round-trip percentiles while the Manager is artificially stalled 1 s.

**Resolution (implemented):** The per-command audit publish is now off the critical path.

- Added `_AsyncEventPublisher` (`processes/device_router.py`): a small helper that owns a dedicated daemon thread and a bounded (`_AUDIT_QUEUE_MAX = 8192`), non-blocking queue. Callers hand off `(topic, payload)` and the owned thread performs the blocking `ManagerClient.publish_event` round-trip. The `ManagerClient` (and its DEALER socket) is *built and used solely on that thread*, so the change introduces no cross-thread ZMQ socket sharing (keeps F1's thread-safety invariant intact).
- `_publish_event` on the workers now just does a non-blocking `queue.put_nowait`. On overflow — only reachable when the Manager stalls long enough for thousands of commands to complete — the newest event is dropped and counted (`_AsyncEventPublisher.dropped`) rather than blocking the caller. Audit is observability, so best-effort delivery under sustained backpressure is the correct trade-off (matches the "bounded local buffer" fallback noted in *Risks* above).
- `_DeviceWorker.run` and `_ProcessWorker.run` start the publisher at thread entry (replacing their own inline `ManagerClient` construction) and call `_stop_event_publisher()` at shutdown, which drains queued events best-effort and closes the client under a bounded 2 s join (daemon thread, so an unreachable Manager can't hang shutdown). The shared `_publish_event` / `_start_event_publisher` / `_stop_event_publisher` helpers live on `_BaseWorker`; the duplicated per-worker `_publish_event` bodies were removed.
- **Ordering/ts:** `manager.command` payloads carry a command-completion `ts`; for any event without one, `_AsyncEventPublisher.publish` stamps a capture-time `ts` before enqueue (see follow-up (7) below), so async delivery does not distort event timestamps. Per-worker FIFO order is preserved by the single-thread single-queue design.
- **Scope note:** the router-level `DeviceRouter._publish_manager_event` (interceptor route register/unregister, control-plane only) was left blocking — it is not on any per-command reply path (tracked as follow-up (6) below).
- **Tests:** `tests/test_device_router_async_audit.py` pins non-blocking publish under a stalled Manager, in-order delivery after unblock, counted overflow drops, bounded `close()`, build-failure counting/retry, publish-error counting, and capture-time `ts` stamping. The device-router/journal/interceptor/supervision/federation regression suites (171 tests) pass.

**Post-fix code review (8-angle) — follow-up changes.** A recall-biased review of the F3 fix surfaced 10 findings. Six were fixed in the same branch; four are recorded here as known/deferred.

*Fixed:*

- **(1) Transient audit-client build failure no longer permanently/silently disables audit.** `_AsyncEventPublisher` now builds the client lazily via `_ensure_manager` and retries after a `_AUDIT_CLIENT_RETRY_S = 5 s` cooldown instead of setting `manager=None` forever on the first exception. The failure is surfaced (`client_error`) and every event discarded while there is no client is counted (`dropped`), not swallowed.
- **(2) The federated/mirrored reply path no longer blocks on the Manager.** `_MirroredDeviceWorker.run` now enqueues the client reply *before* the best-effort `_maybe_cache_capabilities` (a blocking `manager.call`), so a stalled Manager cannot delay mirrored command replies. (Residual: the worker's *next* dequeue can still wait on that cache, but only for rare `capabilities` discovery responses, not per-command traffic.)
- **(3) Audit loss is now observable.** `_AsyncEventPublisher` exposes `dropped` / `publish_errors` / `client_error`; `_BaseWorker.audit_stats()` surfaces them per worker in the router's worker-health snapshot (`_worker_snapshot` rows gain an `audit` block). Publish exceptions are counted rather than silently `pass`-ed.
- **(5) Publisher teardown moved into `try/finally`.** `_DeviceWorker.run` (via an extracted `_run_loop`), `_ProcessWorker.run`, and `_MirroredDeviceWorker.run` now tear down the publisher thread + `ManagerClient` socket even if the loop raises — workers are recreated on death (`_ensure_*_worker`), so a leak would otherwise accumulate across crash-recreate cycles.
- **(7) Capture-time `ts` is now guaranteed for all audit events.** `_AsyncEventPublisher.publish` stamps a capture-time `ts` when the caller did not set one (interceptor `…error`/`…modified` events had no `ts`, so async delivery would have recorded delivery-time under backpressure). `manager.command` payloads already carry `ts` and skip the copy.
- **(10) `close()` no longer discards a queued event.** Replaced the get-then-put eviction dance with a single deadline-bounded blocking `put(None, timeout=…)`, which lands the shutdown sentinel without throwing away a real event and stays bounded if the Manager is unreachable.

*Deferred (noted, not changed):*

- **(4) Journal-before-ack durability is relaxed by design.** The reply is now acked before the audit event is durably journaled, so a router crash (or an overflow drop) between ack and drain can leave an executed-and-acknowledged command with no journal record. This is the intended latency/durability trade-off and is the open operator question in §11 ("must the journal entry be durable before ack?"). If strict "journal before ack" is required, keep the async transport but gate the reply on a bounded local-buffer append (not a Manager round-trip).
- **(6) `DeviceRouter._publish_manager_event` remains synchronous** on the router's main dispatch thread (interceptor route register/unregister only). Lower frequency than per-command audit but a *wider* blast radius when it does fire (a stalled Manager freezes all command intake, not one worker). Candidate to route through the same async mechanism if interceptor churn during Manager stalls proves to be a problem in practice.
- **(8) Third inline copy of the bounded-background-worker pattern.** `_AsyncEventPublisher` re-implements the daemon-thread + bounded-queue + drop-with-counter + sentinel-drain lifecycle already present in `processes/influx_writer.py` (`_http_thread_run`) and `processes/hdf_writer.py` (`_bg_queue`/`_BG_SENTINEL`); the drop policy has already diverged (newest-drop here vs. oldest-drop in influx). Worth extracting a shared `BoundedBackgroundWorker` helper in `utils/` so the subtle shutdown/overflow logic lives in one place.
- **(9) Per-worker publisher fan-out.** One publisher thread + one `ManagerClient` DEALER socket per worker means a router fronting many devices runs N+M extra threads/sockets. Because `include_process_id=False`, no per-worker client identity is required, so a single process-wide publisher fed by all workers over one shared queue/socket would collapse this. (Shares a root cause with (8); the extraction would naturally enable a shared instance.)

### F4 — Auto-reconnect blocks the Manager main loop

- **Status:** ✅ **FIXED**. See *Resolution* below.
- **Severity:** Critical (when feature enabled; it is opt-in, `enabled=False` default). **Confidence:** Confirmed.
- **File:** `src/experiment_control/_manager/process_supervision.py` — `_auto_reconnect_attempt` (L1188–1256), `_maybe_auto_reconnect_device` (L1259–1273), `supervise_device_drivers` (L1276–1290); called from `Manager._check_timeouts` (`manager.py:2006–2012`) inside `_pump_once`.
- **Behavior:** when a device's telemetry is stale, the supervision tick performs `disconnect_device` (timeout 1 000 ms) then `connect_device` (timeout `connect_timeout_ms` or the global 1 500 ms) **synchronously on the main poll loop**. With several stale devices past cooldown, attempts run back-to-back in one tick.
- **Impact:** up to ~2.5 s main-loop stall per attempting device: no registry handling, no heartbeat/telemetry ingestion (mitigated only partially by the loop-stall grace logic, which itself exists to paper over this), no internal RPC, delayed lifecycle replies — and via F3, all device commands stall. Exactly when one device is unhealthy, the whole experiment's control path degrades. Note the code comments elsewhere (auto-connect-on-register) explicitly moved the *same* operation off-loop for this reason.
- **Unrelated devices forced to wait:** Yes.
- **Subsystem:** scheduling/network (and serial indirectly, since the driver-side connect is a serial open).
- **Root cause:** reconnect retrofitted into the supervision tick instead of the lifecycle executor.
- **Recommendation:** dispatch `_auto_reconnect_attempt` to `_lifecycle_executor` under the per-device lifecycle lock (identical pattern to `_dispatch_auto_connect`), keeping the should-attempt bookkeeping on the main thread. Note this also removes one caller of the F1 race only if F1's guard is added too.
- **Risks/assumptions:** reconnect may now overlap operator-initiated lifecycle ops — the per-device lock already serializes that correctly.
- **Hardware testing:** recommended (verify reconnect behavior against a genuinely unplugged serial/network device).
- **Measurement:** `manager.loop_stall` event count/duration with a device unplugged and auto-reconnect enabled, before/after.

**Resolution (implemented):** `_auto_reconnect_attempt` (`_manager/process_supervision.py`) is now split into a main-thread bookkeeping half and an executor-dispatched worker half, mirroring the pattern `_dispatch_auto_connect`/`_run_auto_connect` already established for auto-connect-on-register (`manager.py:1646-1681`).

- **Main thread (unchanged cost):** counters (`auto_reconnect_attempts`), the cooldown timestamp (`auto_reconnect_last_attempt_mono`/`_wall`), the `suppressed` flag, and the `manager.device.auto_reconnect.attempt` event are still set/published synchronously inside `_auto_reconnect_attempt`, on the calling thread (the Manager main loop in production). Setting `auto_reconnect_last_attempt_mono` *before* dispatch is what keeps the existing cooldown check in `_auto_reconnect_should_attempt` correct — a second `_check_timeouts` tick during cooldown won't re-dispatch while the first attempt is still in flight on the executor.
- **Worker thread (moved off-loop):** the actual `disconnect_device` RPC (1000 ms timeout) then `connect_device` RPC (`connect_timeout_ms` or the global default) — the ~2.5 s worst case — now run in a new `_run_auto_reconnect(manager, device_id, handle, attempt, age_s)` function submitted to `manager._lifecycle_executor.submit(...)`. `_auto_reconnect_attempt` catches `RuntimeError` around the `submit()` call the same way `_dispatch_auto_connect` does, so a manager tearing down between the staleness check and dispatch doesn't crash the poll loop — it's fire-and-forget, no RPC reply is owed to anyone.
- **Locking (why this is safe by construction):** `_run_auto_reconnect` acquires `manager._lifecycle_device_locks.setdefault(device_id, threading.Lock())` — the *same* per-device lock used by `Manager._run_lifecycle` (operator-initiated device.connect/disconnect/driver.*/recover) and by `_run_auto_connect`. Because all lifecycle-affecting device I/O funnels through this one lock, an auto-reconnect attempt cannot run concurrently with an operator-initiated lifecycle op on the same device — they always serialize, exactly like two operator ops would. Different devices still reconnect fully in parallel (separate locks), so N stale devices no longer cost N × ~2.5 s of anything, on any thread that matters to the poll loop.
- **Events:** `manager.device.auto_reconnect.success` / `.failed` are now published from the worker thread. No special handling was needed — `pubsub.py`'s `_publish_manager_event` already redirects off-main-thread publishes through `_lifecycle_event_queue`, which the main loop drains every `_pump_once` tick (this is the same mechanism `_run_lifecycle` and `_run_auto_connect` already rely on).
- **Tests:** `tests/test_manager_auto_reconnect.py` — the existing three tests (device-spec parsing, disconnect→connect→success outcome, max-attempts suppression, healthy-reset) still pass, adapted only so the disconnect/connect outcome test runs the executor's submitted work inline (`submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)`) to keep asserting the same end-to-end behavior. New `ManagerAutoReconnectOffLoopTests` class adds: (a) `test_maybe_auto_reconnect_device_does_not_call_rpc_inline` — with a bare `Mock()` (no side effect wired), proves `_maybe_auto_reconnect_device` returns without ever calling `_call_device_rpc` itself, and that `_run_auto_reconnect` specifically is what got submitted; (b) `test_dispatch_does_not_block_on_slow_device_io` — with a real `ThreadPoolExecutor` and a fake RPC that sleeps 0.3 s, proves the calling thread returns in well under that time and the success event still arrives asynchronously; (c) `test_reconnect_serialises_with_concurrent_operator_lifecycle_op` — runs a simulated operator lifecycle op and `_run_auto_reconnect` concurrently sharing one real `threading.Lock` in `_lifecycle_device_locks`, with an overlap detector, proving they never execute their device I/O at the same time.
- **Verification:** `uv run ruff check src tests examples` (pass), `uv run python -m unittest discover -s tests -p "test_*.py" -q` (884 tests, 1 skipped — up from the 717/1-skipped baseline only because of new tests added by this and prior fixes), `uv run pytest -q` (893 passed, 1 skipped, 249 subtests), `uv run mypy src/experiment_control` (9 errors — identical set before and after this change; baseline already regressed from the documented 2 to 9 by F2/F3, not by this fix; see F2's *Deferred issue* note).

### F5 — Sequencer `tick()` is unbounded; RPC starved during sleepless scans

- **Status:** ✅ **FIXED**. See *Resolution* below.
- **Severity:** High (responsiveness/safety-adjacent). **Confidence:** Confirmed.
- **File:** `src/experiment_control/sequencer/runtime.py` — `SequencerRuntime.tick` inner loop (L465–488); `src/experiment_control/sequencer/sequencer.py` — `run` (L2949–2956).
- **Behavior:** `tick()` loops `while self._state == "RUNNING"`, executing steps until a handler returns True (sleep / wait_until / pause) or the sequence ends. `set`/`call`/`assign`/`for`/`repeat` handlers return False, so a scan composed purely of sets and calls executes **entirely within one `tick()`**. The process's RPC ROUTER (pause/stop/status) is drained only between ticks in `run()`. `_check_stop_pause` is checked per step, but the flags it reads can only be set by RPC handlers that cannot run.
- **Impact:** during such a scan the sequencer cannot be paused or stopped, status/UI queries time out, and progress events stall. Heartbeats continue (separate thread), so the manager sees it "alive" while it's unresponsive to control. Duration = whole-scan duration (each step is a blocking router RPC, so minutes are realistic).
- **Unrelated devices forced to wait:** N/A (control-plane responsiveness).
- **Subsystem:** scheduling.
- **Root cause:** tick loop designed to batch cheap steps, without a step or time budget.
- **Recommendation:** add a per-tick budget (e.g., break the inner loop after N steps or T ms) *or* call a lightweight RPC drain between steps. A budget of ~10 ms keeps step batching while restoring control responsiveness.
- **Risks/assumptions:** none for correctness; `atomic` blocks already defer stop/pause by design and would be unaffected.
- **Hardware testing:** not required.
- **Measurement:** issue `sequencer.stop` mid-scan (sleepless test sequence) and measure request→state-change latency.

**Resolution (implemented):** `tick()`'s inner step loop now carries a per-tick budget so it returns control to `sequencer.py`'s `run()` regularly, instead of only when a step handler returns `True` or the sequence ends.

- **Budget.** Two module-level constants in `runtime.py`, `_TICK_BUDGET_S = 0.010` (10 ms wall-clock, `time.monotonic()`-based, consistent with the rest of the tick/step timing in this module) and `_TICK_MAX_STEPS = 200` (a step-count cap so a burst of unusually cheap steps — e.g. all hitting exception/error short-circuits — can't spin the wall-clock check indefinitely). The inner `while self._state == "RUNNING"` loop records `tick_deadline = time.monotonic() + _TICK_BUDGET_S` and a `steps_executed` counter at loop entry, and after each step that completes normally (handler returned `False`, i.e. cheap `set`/`call`/`assign`/`for`/`repeat`-style steps), returns from `tick()` once `steps_executed >= _TICK_MAX_STEPS` or the deadline has passed — whichever comes first.
- **Atomic blocks unaffected.** The budget check is gated on `self._atomic_depth == 0`, the exact same guard `_check_stop_pause` already uses to defer stop/pause while inside an `atomic` block. So a running `atomic` block is never split by the new budget, regardless of how many steps or how much wall-clock time it takes — it remains uninterruptible by anything other than its own completion, matching the pre-existing stop/pause semantics exactly. `_execute_atomic_step` still runs on the same per-step path (push a frame, `_atomic_depth += 1`/`-= 1` via `_Frame.on_exit`), so no atomic-specific code needed to change.
- **Why this restores responsiveness.** `sequencer.py`'s `run()` loop is `_poll_and_drain(50)` (services the RPC ROUTER — pause/stop/status) → `_runtime.tick()`, repeated forever. Previously a sleepless scan of pure set/call steps ran to completion inside one `tick()` call, so `_poll_and_drain` never got another turn until the whole scan finished. With the budget, `tick()` now returns roughly every 10 ms (or every 200 steps, whichever is sooner) during such a scan, so `run()` drains RPC between chunks at essentially the same cadence as a scan with sleeps in it — a pending `sequencer.pause`/`sequencer.stop` now takes effect within about one budget window instead of waiting for the entire scan.
- **No behavior change for sleep/wait/pause-bearing sequences.** Those already returned from `tick()` on their own (handler returns `True`), so the budget is inert for them; it only changes behavior for runs of consecutive cheap steps that would otherwise batch unboundedly.
- **Tests:** `tests/test_sequencer_tick_budget.py` — a single `tick()` call over a long sleepless (set/call-only) sequence returns before the sequence completes, both by step-count (`current_step`/env progress) and wall-clock (elapsed time bounded well under running the full sequence); a `sequencer.stop`/`sequencer.pause` request issued between two `tick()` calls (simulating `run()`'s RPC-drain-then-tick interleaving) takes effect on the very next `tick()` rather than only after the whole scan; an `atomic` block — including one larger than `_TICK_MAX_STEPS` — still executes its entire body within a single `tick()` call, proving the budget does not split it; and a short ordinary sequence still completes via the existing `while state == RUNNING: tick()` pattern used throughout the pre-existing suite. The full pre-existing sequencer/runtime test suite (`test_sequencer_loops.py`, `test_sequencer_adaptive.py`, `test_sequencer_context.py`, etc.) passes unmodified.

**Post-fix code review — follow-up changes.** A high-effort review of the initial F5 fix found a confirmed throughput regression plus three plausible cleanups. All four were fixed in the same branch:

- **(1) CONFIRMED — the initial fix reintroduced a version of the exact problem it was fixing.** `tick()` returned plain `None` regardless of *why* it stopped, so `run()` could not tell "budget exhausted, more work queued" apart from "genuinely idle" (sleeping/waiting/paused/stopped/errored). After a budget-triggered early return, `run()` still fell through to `_poll_and_drain(50)`, which blocks up to the full 50 ms ceiling with nothing queued to wake it — so a sleepless scan advanced in ~10 ms compute bursts separated by ~50 ms idle waits, a duty cycle of a few percent and potentially 5–50x slower wall-clock than before the F5 fix. **Fix:** `tick()` now returns `bool` — `True` only when it stopped solely because the step/time budget was hit while more step work is immediately runnable (no sleep/wait/pause/stop/error intervened), `False` for every genuinely-idle exit path. `sequencer.py`'s `run()` tracks this across iterations and sets its next `_poll_and_drain` timeout to `0` (non-blocking) when `True`, `50` (the normal ceiling) otherwise — so a budget-triggered return resumes essentially immediately instead of waiting out the poll ceiling, while RPC responsiveness for a genuinely idle sequencer is unchanged.
- **(2) Interface designed to compose with the F15 dynamic-poll-timeout fix.** F15 (`fix/f15-sequencer-dynamic-poll-timeout`, reviewed concurrently) computes `run()`'s poll timeout from the next sleep/wait deadline via `next_poll_timeout_ms()`, which falls back to its 50 ms ceiling whenever no sleep/wait is pending — exactly the state a budget-triggered return leaves the runtime in. Without coordination, the two fixes stacked would have reintroduced the ceiling wait on every sleepless-scan tick even after this fix. The `tick()` return value added here is the same "is there more work to do right now" signal `next_poll_timeout_ms()` needs; when F15 lands it can special-case a `True` return from `tick()` (or an equivalent `has_pending_step_work` check) to return a near-zero timeout instead of its ceiling, rather than the two mechanisms fighting each other.
- **(3) Shared `_interruptible()` predicate.** The `self._atomic_depth == 0` guard was duplicated three times (twice in `_check_stop_pause`, once in the tick budget check). Factored into a single `_interruptible()` method used by all three call sites, so the "atomic blocks are uninterruptible" invariant has one source of truth instead of three copies that could silently diverge.
- **(4) `_maybe_publish_progress_event` no longer builds a full status snapshot it's about to discard.** Because `tick()` now returns many times per sleepless scan (previously ~once per whole scan), and this method is called once per `run()` iteration, it was building the full `SequencerRuntime.status()` snapshot (progress/ETA calculation, `dict()` copies of `env`/`vars`, adaptive-study snapshots) on every call just to hit the existing rate-limit throttle and discard it. Added a cheap pre-check using the already-available `runtime.state` property: if the state isn't terminal (`STOPPED`/`ERROR`) and the rate-limit period hasn't elapsed, return before calling `status()` at all — this is safe because that branch of the original logic always discarded the result regardless of what the signature would have been.
- **Tests:** `tests/test_sequencer_tick_budget.py` gained `test_tick_return_value_signals_pending_work_vs_genuine_idle` (budget-exhausted-with-pending-work → `True`; blocked on `sleep` → `False`; sequence fully complete → `False`) and `test_run_loop_uses_more_work_signal_to_avoid_polling_the_full_ceiling` — a deterministic regression guard that replays `run()`'s actual poll-timeout decision against a stub poll (recording simulated delay instead of really sleeping) and asserts the fixed (signal-respecting) strategy accumulates roughly 10x+ less simulated poll delay than the naive/pre-fix strategy (ignore the signal, always wait the full ceiling) for an identical sleepless scan.

### F6 — No parallel step: cross-device sets always sequential

- **Status:** ✅ **FIXED**. See *Resolution* below.
- **Severity:** High (by construction). **Confidence:** Confirmed at review time; the original v1 runtime explicitly rejected `parallel`.
- **Behavior/impact:** a scan point that must set, say, a SynthHD frequency, an NKT setpoint, and a Linien parameter performs three sequential blocking round-trips, each paying F2 (+~50 ms) and F3. Per-point overhead scales linearly with device count even though the devices are fully independent (separate processes, separate serial ports/hosts, separate router workers). The infrastructure below the sequencer *already supports* concurrent per-device commands.
- **Unrelated devices forced to wait:** Yes — by sequencing design.
- **Root cause:** v1 scope decision.
- **Recommendation:** implement `parallel` for a restricted case first: a list of `set`/`call` steps targeting *distinct* devices, dispatched concurrently (the router pipelines fine; the client would need concurrent request_ids on its DEALER — already supported by the request_id correlation) and joined with a combined error. Keep same-device ordering strict.
- **Risks/assumptions:** **operator confirmation required** — some sequences may rely on side-effect ordering across devices (e.g., enable RF only after another device is set). Parallel must remain opt-in per step, never inferred.
- **Hardware testing:** required for representative sequences.
- **Measurement:** per-scan-point duration for an N-device set block, sequential vs parallel.

**Resolution (implemented):** `parallel.do` now treats each direct child as an independent branch. Direct `call`/`set` branches and `atomic` branches containing only `call`/`set` operations are supported. Up to eight branches run concurrently through persistent worker-owned DEALER clients, avoiding both the shared `ManagerClient.call()` lock and per-branch connection churn; excess branches queue. Operations inside an atomic branch execute strictly in YAML order, while sibling atomic branches may overlap; nesting `parallel` inside an ordinary atomic block is rejected. Preflight and runtime validation reject sibling branches that resolve to the same device/process or write the same output name. Each branch uses an isolated environment snapshot, branch-local outputs can feed later operations in that atomic branch, and outputs merge only after every branch succeeds. Failures, including external faults, are applied only after all dispatched work joins so cleanup cannot race active branches. Full control-flow branches remain explicitly deferred.

### F7 — Driver: telemetry & scheduled streams block command RPCs (per device)

- **Severity:** Medium-High (device-dependent). **Confidence:** Confirmed.
- **File:** `src/experiment_control/_driver/runner.py` — `run` loop (L237–279): RPC handled at L256–266, `_publish_telemetry` at L275–277 calls `read_telemetry` (L490–566) which executes every telemetry call sequentially; `_publish_scheduled_streams` (L1208–1227) runs stream acquisitions inline.
- **Behavior:** one thread services RPC, heartbeat, telemetry, and scheduled streams. A command arriving while `read_telemetry()` is mid-sweep waits for the whole sweep. E.g., `NKTBasik` telemetry = temperature, power, emission, frequency, frequency_setpoint — five separate serial transactions (each typically tens of ms on these interfaces) at `telemetry_period_s=1.0`; Linien `traces()` unpickles an N_POINTS×3 trace over rpyc.
- **Impact:** worst-case added command latency ≈ full telemetry sweep or stream acquisition (tens–hundreds of ms; seconds for slow devices). During a fast scan this appears as periodic latency spikes at the telemetry cadence. Heartbeat also jitters (visible as `loop_lag_s`, which is already published — good).
- **Unrelated devices forced to wait:** No — strictly per-device (this is the correct isolation boundary).
- **Subsystem:** serial/network, scheduling.
- **Root cause:** single-threaded driver loop with periodic work inline; correct for serial-port ordering, but no RPC prioritization.
- **Recommendation (preserving protocol ordering):** (a) between individual telemetry calls in `read_telemetry`, poll the RPC socket and service pending commands (same thread, same port — ordering preserved, sweep resumes after); or (b) skip/defer a telemetry tick when an RPC arrived within the last X ms. Avoid a telemetry thread unless the vendor library is known thread-safe.
- **Risks/assumptions:** telemetry gaps during heavy command bursts — the interlock/watchdog consume telemetry freshness, so deferral must stay under their `max_age` (operator question 5). Option (a) has no gap.
- **Hardware testing:** required (per driver; vendor libraries differ in transaction cost and reentrancy).
- **Measurement:** add per-call durations in `read_telemetry` (see §9) and driver-side RPC service latency (poll-in → reply-sent).

**Implementation note — interleaving hazards in option (a).** Option (a) ("poll RPC between telemetry calls") is safe *on the wire* by construction: the loop is single-threaded and the RPC socket is `zmq.REP` (`runner.py:174`), each telemetry call is one complete write-query-read transaction, so polling only at the boundary *between* calls (never mid-transaction) leaves the device link quiescent and the wire strictly serialized. But a naive implementation introduces three hazards that must be guarded:

1. **REP recv/send alternation.** The main loop services RPC as a strict `recv_json()`→`send_json()` pair (`runner.py:256-266`); REP forbids two recvs without an intervening send. A mid-sweep "service pending RPC" helper must complete its send before resuming the sweep *and* be exception-safe — a serviced command that raises and unwinds without sending leaves the socket in the wrong state, and the next loop recv throws `Operation cannot be accomplished in current state`.
2. **Reentrancy into telemetry state.** `read_telemetry` resets `self._telemetry_last_call_errors` at entry (`runner.py:497`) and accumulates into it during the sweep (`:555`); `_publish_telemetry` bumps `self._telemetry_seq` (`:1124`). If the serviced RPC itself triggers a telemetry read (an on-demand telemetry action, or a device command that reads the same registers), the nested call clobbers the outer sweep's error map and sequence number. Today this is structurally impossible (telemetry runs only from the one loop site); option (a) makes it reachable, so it needs a re-entry guard (e.g. an `_in_telemetry` flag that suppresses telemetry-triggering RPC while a sweep is active).
3. **Intra-bundle snapshot coherence (minor).** A command serviced mid-sweep can change device state that a later call in the same sweep reads back, so the published bundle is no longer a single-instant snapshot. Usually benign for telemetry.

Because of (1) and (2), **option (b) is the recommended default** — deferring the whole telemetry tick when an RPC arrived within the last X ms never re-enters telemetry mid-sweep and never touches the REP state machine mid-flight, at the cost of a bounded telemetry gap (which must stay under the interlock/watchdog `max_age`; operator question 5). Prefer option (a) only when the no-gap property is required, and only with all three guards above.

### F8 — Sequencer `set_context` path: blocking retries + per-context RPCs

- **Severity:** Medium. **Confidence:** Confirmed.
- **Status:** ✅ **FIXED**. See *Resolution* below.
- **File:** `src/experiment_control/sequencer/sequencer.py` — `_set_stream_context` (L2370–2397, `time.sleep(backoff)` L2396, deadline 6 s), `_expect_streams` (L2399–2412); constants L57–59.
- **Behavior:** each `set_context` step performs one `hdf.streams.expect` process RPC plus one `stream.context.set` device RPC per stream; on transient errors the device RPC is retried in a loop with `time.sleep` (50→500 ms backoff, 6 s deadline) — **blocking the entire sequencer process** (no RPC service, no telemetry drain; heartbeat survives on its thread).
- **Impact:** per-shot overhead of ≥2 router round-trips (each with F2's ~50 ms floor today); on a flaky driver, up to 6 s of total unresponsiveness per stream. Retry layering is bounded and sensible otherwise.
- **Unrelated devices forced to wait:** the whole sequence waits, so effectively yes.
- **Root cause:** synchronous convenience on a shot-critical path.
- **Recommendation:** convert the retry into tick-state (like `_sleep_until`/`_wait_state`) so the loop stays live; issue `hdf.streams.expect` and the context sets for multiple streams concurrently (they are independent endpoints).
- **Risks/assumptions:** context must be set before the triggering `call` step — keep the step non-advancing until all acks arrive.
- **Hardware testing:** not required.
- **Measurement:** per-`set_context` wall time in a representative shot loop.

**Resolution (implemented):** `set_context` no longer blocks the sequencer's tick loop.

- `sequencer/runtime.py`: added a `_set_context_state` tick-state slot alongside `_sleep_until`/`_wait_state`, cleared via a single `_clear_set_context_state()` helper called at every existing reset site (load/start/fail/terminal-unwind/loop-restart/direct-to-STOPPED) — see the post-review fix note below on why routing every site through one helper mattered. `_execute_set_context_step` now begins a dispatch and returns "pending" (mirroring `_execute_sleep_step`/`_start_wait_until`); a new `_step_set_context` (mirroring `_step_wait_until`) polls it every tick via an injected `poll_set_context` callable until every stream's RPC has acked, errored, or a deadline is exhausted — the step stays non-advancing the whole time, exactly as before, just without a blocking `time.sleep`. The new `begin_set_context`/`poll_set_context` callables are optional constructor args; when absent (existing callers/tests that only wire `set_stream_context`/`expect_streams`), the previous synchronous single-tick behavior is preserved unchanged.
- `sequencer/sequencer.py`: `_set_stream_context`/`_expect_streams` keep their exact bounded-retry/backoff logic (including the `time.sleep(backoff)` between attempts) unchanged — but now run on a small `ThreadPoolExecutor` (`_context_executor`) instead of the tick thread. `_begin_set_context` first submits `hdf.streams.expect`; only once that call has completed *successfully* does it submit the per-stream `stream.context.set` calls (from inside that same worker, still off the tick thread) — see the ordering fix below for why this isn't submitted eagerly. `_poll_set_context` is a non-blocking check of `Future.done()`/`Future.result()` across all of them, plus a dispatch-wide deadline (`_SET_CONTEXT_DISPATCH_DEADLINE_S`) and a best-effort `cancel()` for abandoned dispatches. Worst-case per-step latency changes from *sum* over streams (serial, on the tick thread) to *max* over streams (parallel, off the tick thread), still bounded by a deadline. `SequencerProcess.close()` shuts the pool down.
- Tests: `tests/test_sequencer_context.py` (tick-loop non-blocking behavior via `time.perf_counter` bounds on individual `tick()` calls, non-advance-until-all-acks, timeout surfacing, the synchronous fallback path, concurrency via `threading.Barrier`, expect-before-context-set ordering, dispatch-wide deadline, stop-cancels-dispatch) and `tests/test_dealer_request_id_correlation.py` (concurrent multi-thread `ManagerClient.call()` correctness) — 21 new tests total, all passing alongside the pre-existing `_set_stream_context`/`_expect_streams` unit tests unchanged.

**Post-fix code review — follow-up changes.** A high-effort review of the initial fix surfaced 2 confirmed correctness bugs and 2 further hardening items, all addressed in the same branch:

1. **Unsynchronized concurrent RPC socket use.** The initial fix only locked the two set_context-specific call sites, but `ManagerClient`'s single DEALER socket is also touched, unlocked, from the main loop thread (log flushing every loop iteration, RPC handlers) concurrently with the dispatch pool's worker threads — the exact hazard the fix was meant to eliminate, just relocated. Fixed by moving the lock into `ManagerClient.call()` itself (`manager_client.py`, a new `self._rpc_lock` guarding the whole send/recv round trip), so *every* caller is automatically safe rather than requiring each call site to remember to guard it; the sequencer-local lock was removed as redundant.
2. **Lost expect-before-context-set ordering.** `hdf.streams.expect` is registered `strict=True`; submitting it concurrently with the per-stream `stream.context.set` calls (as the initial fix did) meant a device could ack its context switch and start emitting samples before the writer had processed the expect call, and those samples would be silently dropped. Fixed: `_begin_set_context` now submits the per-stream calls only from inside the `expect` worker, after `_expect_streams` returns successfully — still entirely off the tick thread, so no blocking was reintroduced, but the ordering guarantee is restored.
3. **No cancellation of in-flight futures on stop.** A dispatch abandoned mid-retry (operator stops the run) previously kept running unbounded in the background with nothing tracking it. Fixed with a best-effort `_SetContextDispatch.cancel()` (cancels not-yet-started futures; already-running ones can't be forcibly interrupted — `concurrent.futures` has no thread-kill primitive) wired through `_clear_set_context_state()` at every reset site, including a gap where `_check_stop_pause()` transitioned straight to `STOPPED` without going through the terminal-unwind path that used to do the clearing.
4. **No dispatch-level deadline.** Each call bounded its own internal retry deadline, but a call still queued behind a saturated pool accrued wait time nothing else bounded. Fixed with a dispatch-wide `deadline` checked in `_poll_set_context` covering queued-but-not-yet-started calls too.

(A fifth, lower-priority observation — that serializing every RPC through one lock means the per-stream calls still execute one at a time on the wire, so the only wall-clock win is overlapping retry-backoff windows rather than true request multiplexing — was left as a documented tradeoff rather than redesigning the transport.)

### F9 — Sequential shutdown; per-device 1 s stops

- **Status:** ✅ **FIXED** (`fix/f9-parallel-shutdown`, `852b985`, PR #134). See *Resolution* below.
- **Severity:** Medium. **Confidence:** Confirmed.
- **File:** `src/experiment_control/_manager/lifecycle.py` — `_shutdown_cleanup` (L253–264); `_manager/process_supervision.py` — `stop_driver` shutdown RPC `timeout_ms=1000` (L284–293).
- **Behavior:** shutdown stops each driver in turn; a wedged driver costs the full 1 s RPC timeout before the next is attempted; then each managed process is stopped in turn (each stop can include a ≤500 ms process RPC). The lifecycle executor is already shut down at this point, so no parallelism.
- **Impact:** shutdown time ≈ N_unresponsive × 1 s + process stops; with 20 devices in a failed state, ~20 s+. Cleanup of healthy devices is delayed behind dead ones (violates failure isolation for shutdown specifically). Stop-timeout enforcement/kill escalation does exist (`enforce_device_driver_stop_timeout`) but only runs while the loop is pumping, which it isn't during `_shutdown_cleanup`'s sequential walk.
- **Recommendation:** send all `shutdown` RPCs concurrently (short timeout), then walk terminate/kill; or keep the executor alive until after driver stops.
- **Risks/assumptions:** none for independent devices; if any hardware requires ordered power-down (operator question 2), keep an explicit ordered list.
- **Hardware testing:** recommended once (confirm drivers tolerate concurrent shutdown broadcast).
- **Measurement:** wall time of `_shutdown_cleanup` with k simulated-dead drivers.

**Resolution (implemented):** the graceful-stop RPC broadcast in `_shutdown_cleanup` is now fanned out concurrently instead of walked sequentially.

- **Two phases, each fanned out, joined in order.** Drivers are stopped first, over a short-lived `ThreadPoolExecutor` (`max_workers=min(32, n_dev)`, one task per device handle) whose futures are fully `wait()`-ed before the process phase starts the same way for managed processes. The driver phase is not allowed to interleave with the process phase because a driver's own graceful shutdown may depend on a managed process still being reachable.
- **Why concurrent fan-out is safe:** each device/process owns its own REQ/DEALER socket, and `_call_device_rpc`/`_call_process_rpc` hold `handle.rpc_lock` across the whole request/reply cycle, so concurrent stops of *different* handles never share a socket. `_pump_manager_subscriptions` already early-returns off the main thread (the F1 guard), so worker tasks never touch the manager SUB sockets. Off-thread `_publish_driver_event`/`_publish_manager_event` calls land in the lifecycle reply/event queues rather than publishing inline; a second `_drain_lifecycle_replies`/`_drain_lifecycle_events` pass runs on the main thread after the fan-out (before socket teardown) so those queued stop events still go out.
- **Pool choice:** a dedicated short-lived pool is used rather than `_lifecycle_executor`, which is intentionally already shut down earlier in `_shutdown_cleanup` and must stay quiesced.
- **Unchanged semantics:** `stop_driver` is still called without force, and `_process_guard.close()` remains the final guaranteed reaper — the graceful-then-guard shutdown contract is unchanged, only the graceful phase's wall-clock cost.
- **Result:** total shutdown wait is now bounded to roughly one RPC timeout regardless of how many devices are wedged, instead of `N_unresponsive × timeout`.
- **Tests:** `ParallelShutdownCleanupTests` (`tests/test_manager_process_supervision.py`) cover the parallelism itself (wedged drivers no longer serialize), the off-thread event-drain path, and the empty-manager fast path.

### F10 — Federation forward blocks the Manager loop; socket per call

- **Status:** ✅ **FIXED**. See *Resolution* below.
- **Severity:** Medium (only when federation configured). **Confidence:** Confirmed.
- **File:** `src/experiment_control/federation/hub.py` — `_rpc_call` (L1138–1167: `connect_dealer` per call, `_blocking_call_with_pump` on the poll loop), `forward_device_request` (L468).
- **Behavior:** requests for mirrored devices arriving at the Manager are forwarded synchronously on the main loop, waiting up to the peer's `rpc_timeout_ms` (pumping subscriptions meanwhile — safe here, main thread). A fresh DEALER socket is created per call. DNS is properly cached/off-loop (good), and the *router's* mirrored path has per-route worker threads (good) — this Manager-side path is the weaker twin.
- **Impact:** one slow/unreachable peer stalls the Manager loop per forwarded request (compounding F3); per-call TCP connect adds latency.
- **Recommendation:** move Manager-side federation forwards onto per-peer worker threads with persistent sockets (mirror the router's `_MirroredDeviceWorker` design), replying via the lifecycle reply queue.
- **Hardware testing:** no (network testbed suffices).
- **Measurement:** Manager pump-gap while hammering a mirrored device with the peer blackholed.

**Resolution (implemented):** the first attempt at this fix routed mirrored-device forwards through the Manager's shared local-device lifecycle thread pool with one persistent socket per *peer*, guarded by a single per-peer lock. A follow-up code review found this reintroduced the same class of stall: the per-peer lock let a device forward (now on the lifecycle pool) block a process forward still running inline on the poll loop — with no subscription pumping while waiting on the lock itself — and a flood of mirrored-device traffic to one dead peer could exhaust the shared 32-worker lifecycle pool, starving unrelated local `device.connect`/`disconnect` work. Both were concrete regressions of exactly what this fix was meant to remove, just moved to a different contention point.

The corrected design gives each mirrored device *and* each mirrored process its own dedicated `_FederationForwardWorker` (`federation/hub.py`), directly mirroring the router's `_MirroredDeviceWorker` design as originally recommended above: one thread, one persistent DEALER socket, one bounded task queue, fully decoupled from the Manager's lifecycle pool and from every other mirror. `internal_rpc.py`'s `_handle_internal_rpc` now queues a mirrored-device `"command"`/lifecycle-type request, or a mirrored process's `manager.processes.rpc`, directly onto that mirror's worker via `FederationHub.try_dispatch_device_forward`/`try_dispatch_process_forward` — never onto the shared lifecycle pool, never inline on the poll loop. Local-first precedence is preserved explicitly (`device_id not in self._devices` / `process_id not in self._processes` before consulting federation), since local devices are config-time guaranteed not to collide with a mirror but local *processes* can be added at runtime and legitimately share an id with one. Each worker's socket is reconnected (not reused) after any failure/timeout, since a DEALER has no per-call request/reply correlation and a late reply could otherwise be misattributed to a later call — mirroring `_MirroredDeviceWorker`'s own tradeoff. `route_device_request`'s/`route_process_rpc`'s old federation-fallback branches are now unreachable in production (the interception happens earlier) and return an explicit `federation_forward_not_dispatched` error if ever reached directly, instead of calling the removed synchronous `forward_device_request`/`forward_process_request` methods.

Tests: `tests/test_federation_forward_offload.py` (dispatch to the correct per-mirror worker vs. the local lifecycle pool; local-device and local-process precedence over a same-id mirror; ACL/rewrite/capabilities-cache/peer-unavailable/busy-queue behavior of `try_dispatch_device_forward`; persistent-socket reuse and reconnect-after-failure; a slow mirrored device's forward does not delay a different mirrored device sharing the same peer) plus updated `tests/test_federation_hub.py` (per-mirror worker startup/shutdown, no duplicate workers across repeated `activate()` calls, resolve-failure backoff) and `tests/test_process_federation.py`. Full `unittest discover` passes (959 tests, 1 skipped).

### F11 — Influx writer: shared HTTP worker, no connection reuse

- **Severity:** Medium. **Confidence:** Confirmed.
- **File:** `src/experiment_control/processes/influx_writer.py` — `_write_batch_http` (`urlopen` per batch, L1038–1060), `_http_thread_run` (single thread, L1200–1235), queue depth 64 with drop counting (L435–446 — good).
- **Behavior/impact:** all destinations share one worker; a destination timing out at `request_timeout_s` (default 5 s) head-of-line blocks other destinations' batches (monitoring gaps, then drops once the queue fills). Each batch opens a new TCP (and possibly TLS) connection.
- **Unrelated devices forced to wait:** no device impact (well isolated from control path — good).
- **Recommendation:** one worker per destination (they're independent hosts), and connection reuse (e.g., `http.client.HTTPConnection` kept open, or a small requests.Session-equivalent).
- **Measurement:** `dropped_http_batches` and flush duration per destination with one destination blackholed.

### F12 — Stream analysis: fits inline with SUB drain

- **Severity:** Medium. **Confidence:** Likely (needs profiling of real fit costs).
- **File:** `src/experiment_control/processes/stream_analysis.py` — `run` (L4788–4796): poll → `_drain_sub` executes the op-graph (including `fit.curve_1d`) inline.
- **Impact:** a slow nonlinear fit delays the drain; sustained high chunk rates back up the SUB to its HWM and drop descriptors (data loss for *analysis*, not HDF — the writer has its own subscription; good isolation). Also delays its RPC handling.
- **Recommendation:** execute stateful fit nodes on a single worker thread with a latest-wins mailbox per node; keep the drain loop free. CPU-heavy fits in a thread are fine (numpy/scipy release the GIL for the heavy parts); a process pool is overkill.
- **Measurement:** per-node execution time histogram + SUB `rcvhwm` drop counters under representative load.

### F13 — SHM ring: extra copy per write; seqlock gap on read

- **Status:** ✅ **FIXED**. See *Resolution* below.
- **Severity:** Medium (perf) / Low (correctness, rare). **Confidence:** Confirmed.
- **File:** `src/experiment_control/shm/shm_ring.py` — `ShmRingWriter.write` L208 (`arr.tobytes(order="C")` allocates + copies before the memoryview copy); `ShmRingReader.read_event`/`read_events` (L296–342, L356–377) read `seq_begin`/`seq_end` **before** copying the payload and never re-validate after.
- **Impact:** (a) every published frame pays a duplicate full-buffer copy — for large traces this doubles memory bandwidth in the driver's timing-sensitive loop; (b) if the writer laps the ring mid-copy (reader slower than producer for a full ring cycle), the reader returns a torn payload silently attributed to the old seq. Low probability with adequate `ring_slots`, but silent.
- **Recommendation:** write via `np.frombuffer(self._buf, dtype, offset=payload_start, count=…)[…] = arr` (no intermediate bytes); in readers, re-read `seq_begin`/`seq_end` after copying and discard on mismatch.
- **Hardware testing:** no.
- **Measurement:** driver publish-latency per frame (t before/after `publish_stream`) for a large dummy trace stream.

**Resolution (implemented):** SHM writes now copy directly from the source array into a correctly shaped NumPy view over the destination slot, avoiding the intermediate `arr.tobytes()` allocation and copy while preserving multidimensional, scalar-record, and non-contiguous inputs. Object-containing dtypes are rejected because raw cross-process SHM cannot safely carry Python object pointers. Stream configuration also validates that generated record dtypes remain packed rather than aligned; downstream consumers operate on named fields and do not depend on padding bytes. Readers now validate the slot sequence before snapshotting its metadata and payload and re-read both sequence markers afterward; an overwritten slot is discarded instead of returning a torn frame under the old sequence. The public descriptor and payload contracts are unchanged, so HDF recording, live plotting, and stream analysis continue to consume owned `bytes` snapshots and treat an overrun as a dropped sequence rather than corrupted data. `tests/test_shm_ring_consistency.py` covers multidimensional and structured-record round trips plus deterministic overwrite-during-copy races for both `read_event()` and `read_events()`. The focused HDF/plotting/analysis pipeline group passes (125 tests), and full `unittest discover` passes (886 tests, 1 skipped).

**Architecture limitation:** the sequence fields use ordinary Python memory operations and therefore rely on the strong memory ordering of the project's current x86-64 deployments. Formal ARM support would require aligned cross-process acquire/release atomics and a new layout version; the deferred design and rollout constraints are recorded in [`shm_ring_arm_memory_ordering.md`](shm_ring_arm_memory_ordering.md).

End-to-end writer benchmarks used the real SHM layout and complete slot bookkeeping, alternating the old and new implementations across timing rounds. Results are environment-dependent; they show that the setup cost can outweigh the copy saving for tiny payloads, while avoiding the staging allocation becomes material for larger frames:

| Dtype | Shape | Payload | Old staging copy | Direct view | Reduction |
|---|---:|---:|---:|---:|---:|
| `float64` | `(5, 10_000)` | 400 KB | 13.51 µs | 12.94 µs | 4.2% |
| `int16` | `(5, 40_000)` | 400 KB | 9.91 µs | 6.93 µs | 30.0% |
| `int16` | `(5, 80_000)` | 800 KB | 46.73 µs | 14.86 µs | 68.2% |
| `float64` | `(5, 40_000)` | 1.6 MB | 252.36 µs | 59.84 µs | 76.3% |

### F14 — Manager inline `type:"command"` route (latent)

- **Severity:** Low-Medium (latent — standard stack routes commands through the DeviceRouter, since process clients default to `tcp://127.0.0.1:6000`). **Confidence:** Confirmed.
- **File:** `src/experiment_control/_manager/device_routing.py` — `_route_command` (L61–101) → `_call_device_rpc` blocking up to 1 500 ms on the main loop; `_manager/internal_rpc.py` `_LIFECYCLE_TYPES` (L27–34) excludes `"command"`.
- **Impact:** any client that sends `type:"command"` to the Manager's internal RPC (6002) directly stalls the whole Manager per command. The interceptor RPC (≤500 ms each, chained) can stack on top.
- **Recommendation:** either dispatch `"command"` to the lifecycle executor as well (per-device lock preserves ordering) or return a redirect error pointing at the router.

### F15 — Sequencer sleep/wait quantization (50 ms)

- **Status:** ✅ **FIXED**. See *Resolution* below.
- **Severity:** Low-Medium. **Confidence:** Confirmed.
- **File:** `sequencer/sequencer.py` `run` L2952 (`_poll_and_drain(50)` fixed); `runtime.py` `_sleep_until`/`_wait_state` checked only per tick.
- **Impact:** every `sleep` and every `wait_until` sample lands on a 50 ms grid: `sleep: 0.005` costs ~50 ms; `wait_until(every_s: 0.02)` samples at 50 ms. Directly inflates shot period for fast experiments; adds jitter.
- **Recommendation:** compute the poll timeout from `min(next sleep deadline, next wait sample, 50 ms)` — the runtime already knows these.
- **Measurement:** measured vs requested sleep durations in a test sequence.

**Resolution (implemented):** the outer `run()` poll timeout is now computed from the runtime's own pending deadlines instead of a hardcoded constant.

- **`SequencerRuntime.next_poll_timeout_ms(ceiling_ms=50, floor_ms=1)`** (`sequencer/runtime.py`): returns the time (ms) until the next thing the runtime needs to act on — the earliest of a pending `_sleep_until` deadline, a pending `_wait_state` deadline (sample cadence, `timeout_s`, or `stable_for_s`), or a pending `_adaptive_observe_state` recheck time — clamped to `[floor_ms, ceiling_ms]`. When the runtime isn't `RUNNING`, or when none of the above is pending, it returns `ceiling_ms` unchanged, so RPC/control-plane responsiveness (pause/stop/status) for idle or non-sleep/wait ticks is exactly what it was before. `floor_ms` is itself clamped to `>= 1` inside the method (not just via the default argument), so a caller passing `floor_ms=0` (or negative) against an already-elapsed deadline still can't produce a non-blocking/busy-spin poll.
- **`SequencerProcess.run()`** (`sequencer/sequencer.py:2951-2955`): each outer-loop iteration now calls `poll_timeout_ms = self._runtime.next_poll_timeout_ms(ceiling_ms=50)` and passes that to `_poll_and_drain(poll_timeout_ms)` instead of the fixed `_poll_and_drain(50)`. 50 ms remains the *ceiling* (unchanged worst case for RPC latency), not the floor.
- **Scope.** This only changes the poll timeout used *between* ticks; `tick()` itself and its per-call step budget are untouched (that's F5's concern, landing separately).
- **Busy-spin guard.** `_start_wait_until` now clamps a configured `every_s` to a minimum (`_MIN_WAIT_EVERY_S = 5ms`). Without this, a misconfigured `wait_until(every_s: 0)` (previously harmless — the old poll was always 50 ms regardless) would have `next_sample_t` stay ~= now every iteration, flooring `next_poll_timeout_ms` to 1 ms and spinning the outer loop at ~1000 Hz for the whole wait duration. The clamp bounds the worst case to ~200 Hz, matching the intent of a "poll as needed, not busier than necessary" design.
- **Adaptive-observe coverage.** `_AdaptiveObserveState` gained a `next_check_t` field, set whenever a trial is blocked waiting on an `analysis_output` metric (`_collect_adaptive_repeat`) to `now + 20ms`, and included in `next_poll_timeout_ms`'s deadline set. Real wakeups for this path mostly come from the analysis SUB socket firing (independent of this poll timeout), but the fallback poll — which also re-checks the source's own `timeout_s` bookkeeping — now runs at a bounded cadence instead of the full 50 ms ceiling.
- **Tests:** `tests/test_sequencer_poll_timeout.py` (18 tests) — unit coverage of `next_poll_timeout_ms` (no-pending falls back to the ceiling; non-`RUNNING` state falls back to the ceiling even with a pending sleep; a pending sleep/wait reports the remaining time, clamped at both the floor and the ceiling; the earlier of a pending sleep vs. wait deadline wins; `wait_state.timeout_s`/`stable_for_s` deadlines beat a distant sample cadence; a pending adaptive-observe recheck is reported, and its absence keeps the ceiling; `floor_ms=0` or negative still returns >= 1 against an elapsed deadline), plus integration coverage driving `SequencerRuntime` through a harness that mimics `run()`'s poll-then-tick loop: a `sleep: 0.005` step completes in single-digit milliseconds (well under the old 50 ms floor), a `wait_until(every_s: 0.02)` step samples at roughly the requested 20 ms cadence (not 50 ms), a sequence of plain `set`/`call` steps (no sleep/wait ever pending) observes the poll timeout staying at the unchanged 50 ms ceiling on every iteration, `every_s: 0` is clamped at start (and a full run against a 50 ms `timeout_s` stays well under ~20 poll iterations rather than busy-spinning).
- **Post-fix code review.** A follow-up review flagged four issues, all addressed above: (1) the `every_s: 0` busy-spin risk (clamp added), (2) `_adaptive_observe_state` wasn't considered in the deadline set (added `next_check_t`), (3) `wait_state.timeout_s`/`stable_for_s` weren't considered, only `next_sample_t` (added), (4) the no-busy-spin guarantee depended entirely on the default `floor_ms=1` rather than being enforced in the method (added an internal `max(1, floor_ms)` clamp).

### F16 — Driver `connect_device` unbounded; heartbeat outage during connect

- **Severity:** Low-Medium. **Confidence:** Requires runtime verification.
- **File:** `_driver/runner.py` `_rpc_route_connect_device` (L904–942); `drivers/linien_driver.py` `connect` (L30–35, rpyc + optional SSH server autostart); `drivers/nkt_driver.py`/`synthhd_driver.py` (serial open in connect).
- **Behavior/impact:** the vendor connect runs inline in the REP handler with no driver-side timeout; heartbeats stop for its duration (single thread). Linien connect can plausibly exceed `heartbeat_timeout_s=3.0` → the Manager may demote the driver as heartbeat-stale mid-connect (grace windows help only near startup). Meanwhile the Manager-side connect RPC timeout (1 500 ms default; auto-reconnect's `connect_timeout_ms` configurable) can expire while the connect eventually *succeeds* — Manager reports failure, device is actually connected (state divergence; the "already connected" recovery path does handle the retry, at the cost of an extra disconnect/connect cycle).
- **Recommendation:** set per-device `connect` RPC timeouts realistically in specs; consider a driver-side heartbeat "connecting" grace (the manager already defers on `STARTING`); document that `connect()` implementations should bound their own I/O timeouts.
- **Hardware testing:** required (timing is device-specific).

### F17 — SUB endpoint accumulation on driver restarts

- **Severity:** Low / informational. **Confidence:** Confirmed.
- **File:** `manager.py` `_handle_registry` L1286 (`self._sub.connect(reg.pub_endpoint)`); driver PUB ports are random per process (`runner.py:325`).
- **Impact:** each driver restart adds a new endpoint; old ones are never `disconnect()`ed, so libzmq retries reconnection to dead ports indefinitely — a slow accumulation of timers/FD churn over long uptimes with crash-looping drivers.
- **Recommendation:** `self._sub.disconnect(old_pub_endpoint)` when a driver exits or re-registers with a new endpoint.

### F18 — Positive observations (no action needed)

- **DeviceRouter per-device workers** with own REQ sockets, inflight caps, per-bucket overload rejection with `retry_after_ms`, LRU-capped interceptor socket cache — the right shape for failure isolation.
- **HDF writer**: background flush thread, lock-free hot drain path (explicitly reasoned in comments), bounded reservoirs with drop/defer counters and rate-limited operator events, batched appends, state carried across file rotation. `resize((n+1,))`-per-row exists only on low-rate event datasets.
- **Manager caches** all LRU-bounded (telemetry, chunks, RSS 1 s TTL, log history deque); command journal on a background thread with batching and retention.
- **Observability primitives already present**: `loop_lag_s` in driver heartbeats, `manager.loop_stall` events, pump timing, drain-cap-hit events, router queue-depth snapshots (`router.stats`), per-signal telemetry error surfaces.
- **DNS handled correctly** (resolved off I/O threads, negative caching in mirrored workers, TTL cache in federation).
- **UI/monitoring generates no device traffic**: telemetry is pushed by drivers on a fixed cadence and cached at the manager; the TUI/gateway read caches.

---

## 4. Cross-device dependency analysis

**Genuinely independent (safe to overlap in software):**
- Commands to different devices: separate driver processes, separate serial ports / network hosts, separate router worker threads and REQ sockets. Nothing in the transport requires cross-device ordering.
- Driver lifecycle ops across devices (already parallel via the 32-thread lifecycle executor with per-device locks).
- Influx destinations; federation peers; HDF vs. analysis consumption (separate SUB subscriptions + independent SHM readers).

**Intentionally ordered (must be preserved):**
- Same-device command order: REQ/REP + the single driver thread guarantee strict ordering per device — this is protocol-required for serial devices and must not be relaxed.
- `set_context` → `expect_streams` → acquisition `call`: the HDF writer must know the context before frames arrive.
- Interceptor chains before device dispatch (ordered by registration).
- Connect-check (identity verification) between connect and use.

**Serialization that is broader than necessary:**
- Cross-device sequencing remains serial by default; F6's bounded `parallel` step provides explicit overlap for independent targets.
- The `manager.command` audit RPC coupling all devices to the Manager loop (F3).
- Auto-reconnect and federation forwards on the Manager main loop coupling all control traffic to one sick device/peer (F4, F10).
- `connect_all_devices` (minor; startup normally uses parallel auto-connect-on-register). Sequential shutdown (F9) is fixed.

**Where parallelization would be unsafe without operator confirmation:** any `parallel` sequencing across devices that share physical infrastructure (same USB hub/serial mux, RF chains with enable ordering, interlock preconditions). See §11.

---

## 5. Serial communication assessment

Serial devices (NKT Basik via `nkt_basik`, Windfreak SynthHD via `windfreak`) are wrapped thinly and held open for the process lifetime — **no per-command reconnects, no explicit flushing, no polling of hardware beyond the telemetry schedule**. Ordering per port is guaranteed by the single driver thread; there is **no cross-port serialization anywhere** (each port lives in its own OS process). This is the right structure.

Issues: telemetry sweeps interleave with commands on the one thread (F7) — the only real serial-latency coupling found; vendor-library timeouts are not surfaced or bounded by the framework (F16); a hung serial read inside a vendor call wedges that driver until the Manager's heartbeat-stale demotion kills it (~3–9 s) — acceptable isolation, since only that device is affected. `NKTBasik.disconnect()` is a deliberate no-op (documented) — relies on GC to close the port; after an in-process reconnect cycle the old handle may briefly hold the port (possible `connect` failure on recover; requires runtime verification on Windows where COM ports are exclusive).

## 6. Network communication assessment

Network devices (Linien/rpyc) get the same per-process isolation. ZMQ transports use bounded RCVTIMEO/SNDTIMEO everywhere checked; DEALER stale-reply correlation via request_id with lazy pre-send drains is implemented consistently (client, router workers, interceptors) — a well-executed pattern. Remaining items: the blocking audit publish (F3), federation forwards on the Manager loop + socket-per-call (F10), Influx HTTP without keep-alive and with a shared worker (F11), and the router reply quantization (F2), which affects every networked command equally. One unavailable network device cannot stall others *except* through F4 (auto-reconnect stalls the Manager) and, transitively, F3.

## 7. Async, threading, locking, and scheduling assessment

- The FastAPI gateway is correctly async: a pipelined `RouterRpcClient` on a dedicated thread resolves asyncio futures via `call_soon_threadsafe`, with both per-request timeouts and a safety-net `wait_for` — no blocking ZMQ in the event loop. Good.
- Thread models are appropriate per workload (I/O-bound → threads; the one CPU-heavy area, curve fitting, is the exception — F12).
- Locking is fine-grained: per-handle `rpc_lock` (RLock, with a documented bounded close-wait), per-device lifecycle locks, `_h5_lock` for the writer, `_route_lock` in the router. No global device lock exists. The one violation is F1 (unguarded SUB pump from workers).
- Queue → poll-loop handoffs lack wakeups in two places (router replies F2, manager lifecycle replies/events — same pattern, up to 50 ms, lower impact).
- Thread-pool sizing: 32 lifecycle workers is ample; router spawns one thread per device/process/peer (bounded by config size). No starvation risk identified. Worker exceptions are contained per-loop iteration (`_bg_thread_dead` flags with supervisor restart — good).

## 8. Data handling, logging, and HDF5 assessment

The HDF writer is the strongest component reviewed: SUB drain never touches h5py on the hot path; writes are batched to a background thread through a bounded queue with lossless deferral; fsync cadence is decoupled from batching; strict-stream accounting and drop counters are written into the file. Logging in timing-sensitive loops is rate-limited (driver telemetry exceptions, drain-cap events, reconnect events). Metadata (`.attrs`) writes happen at file/measurement boundaries, not per shot. The SHM double-copy and seqlock gap (F13) are fixed; the sequencer/event datasets grow by 1-row resizes (fine at their rates); the `manager.command` event volume itself — every device command produces a manager event + journal row + HDF event-buffer entry — is worth watching at high shot rates (`event_log_mode` already lets operators reduce it).

## 9. Profiling and measurement recommendations (where instrumentation belongs)

Existing signals are good (driver `loop_lag_s`, `manager.loop_stall`, pump timings, router `queue_depths`, HDF drop counters). Gaps, in priority order:

1. **Command-path latency decomposition** — timestamps in `_DeviceWorker.run`: dequeue→interceptors-done→device-reply→publish-done→reply-enqueued, plus router-side enqueue→send. This single instrument distinguishes device latency vs. interceptor cost vs. Manager coupling (F3) vs. poll quantization (F2).
2. **Driver-side RPC service latency and per-telemetry-call durations** in `read_telemetry` (per `plan.method`), exported in the telemetry bundle — quantifies F7 per device and separates hardware latency from framework overhead.
3. **Sequencer per-step-type duration histograms** (it already has a global EWMA) plus time-in-`_call_device` vs. time-in-template-render — separates software overhead from device waits per step kind.
4. **Client-visible round-trip probe**: a periodic no-op command per device from the TUI/gateway, recorded as a trend — this is the number operators feel.
5. **Manager pump-duration/gap percentiles** (only "last" values are kept today) and time spent inside `_check_timeouts` — catches F4/F10-class stalls quantitatively.
6. **End-to-end shot timing**: `set_context` → first chunk seq observed by HDF writer, measurable from existing timestamps (`t0_mono_ns` vs. context-set time) — distinguishes acquisition latency from pipeline delay.

## 10. Recommended order of future fixes

| Rank | Fix | Benefit | Implementation risk | Hardware validation |
|---|---|---|---|---|
| 1 | F2 router reply wake-up — ✅ **DONE** (inproc doorbell) | Very high (−30–50 ms every command) | Low (inproc doorbell) | No |
| 2 | F3 audit publish off critical path — ✅ **DONE** (`fix/f3-async-audit-publish`) | High (decouples devices from Manager health) | Low | No |
| 3 | F1 thread-guard the SUB pump — ✅ **DONE** | High (stability) | Very low | No |
| 4 | F4 auto-reconnect → lifecycle executor — ✅ **DONE** | High when enabled | Low-medium | Yes (unplug tests) |
| 5 | F5 tick budget / RPC drain in tick — ✅ **DONE** (`fix/f5-sequencer-tick-budget`) | High (operability/safety) | Low | No |
| 6 | F15 dynamic sequencer poll timeout — ✅ **DONE** (`fix/f15-sequencer-dynamic-poll-timeout`) | Medium (shot-rate) | Low | No |
| 7 | F8 non-blocking set_context retries — ✅ **DONE** (`fix/f8-set-context-nonblocking`) | Medium | Low-medium | No |
| 8 | F7 driver RPC/telemetry interleaving | Medium-high per device | Medium | **Yes, per driver** |
| 9 | F9 parallel shutdown — ✅ **DONE** | Medium | Low | Once |
| 10 | F6 `parallel` step (restricted form) — ✅ **DONE** | High for multi-device scans | Medium-high | **Yes, representative sequences** |
| 11 | F13 SHM copy + seqlock re-check — ✅ **DONE** (`eb54477`) | Medium (large streams) | Low | No |
| 12 | F11 Influx per-destination workers/keep-alive | Medium (monitoring) | Low | No |
| 13 | F10 federation forwards → per-mirror workers — ✅ **DONE** (`fix/f10-federation-forward-worker`); F14 manager inline command hardening still open | Medium (deployment-dependent) | Medium | No |
| 14 | F12 analysis fit offload | Medium | Medium | No |
| 15 | F16/F17 connect bounding, SUB disconnects | Low | Low | F16 yes |

## 11. Open questions for the experiment operator

1. **Cross-device ordering:** which sequences depend on side-effect order between devices (RF enable after frequency set on *another* device, laser/AOM ordering, interlock preconditions)? Those steps must remain sequential; `parallel` is opt-in only.
2. **Shared physical buses:** do any "independent" devices share a USB hub, serial multiplexer, or power sequencing that would make concurrent I/O unsafe even across processes?
3. **Audit guarantees:** must the `manager.command` journal entry be durable/ordered *before* the client sees the reply (F3), or is best-effort async acceptable?
4. **Auto-reconnect in production:** is `auto_reconnect.enabled` used in real configs (F4 severity depends on this), and what are acceptable reconnect timings?
5. **Interlock telemetry freshness:** what `max_age` do interlock rules assume? This bounds how much telemetry deferral is allowed in the F7 fix.
6. **Target shot rate:** what step/shot period is the goal? If ≥ 200 ms, F2/F15 matter little; if ≤ 50 ms, they dominate and should be fixed first.
7. **Slow-connect devices:** which devices legitimately need > 1.5 s to connect (Linien with `autostart_server`?), so per-device connect timeouts (F16) can be set instead of the global default?
8. **Windows COM-port exclusivity:** has in-process reconnect of the NKT (whose `disconnect()` is a GC-reliant no-op) been observed to fail with "port in use"?
