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
4. **A genuine thread-safety violation**: lifecycle worker threads drain the Manager's SUB sockets (`_pump_manager_subscriptions`) concurrently with the main poll loop. ZMQ sockets are not thread-safe; this can corrupt/crash under load.
5. **Cross-device parallelism is absent at the sequencing layer**: `parallel` is explicitly unsupported, so independent devices are always set one at a time, each paying costs (1)+(2).
6. Within one device, **telemetry/scheduled-stream reads share the single driver thread with command RPCs**, so a sequencer command can wait behind a multi-register serial telemetry sweep.

Data handling (HDF writer with background flush thread, Influx with background HTTP thread, SHM rings) is in good shape; findings there are second-order (an extra copy per SHM write, a seqlock gap, HTTP connection reuse).

---

## 2. Prioritized findings table

| # | Sev | Location | Issue | Impact | Recommended action |
|---|-----|----------|-------|--------|--------------------|
| F1 | Critical | `_manager/rpc_calls.py:148`, `manager.py:1657` | SUB sockets drained from lifecycle worker threads concurrently with main loop | Undefined behavior in libzmq; corruption/crashes under load | Thread-guard the pump (no-op off main thread) |
| F2 | High | ✅ **FIXED** — `processes/device_router.py` (run loop + `_BaseWorker`) | Command replies quantized to 50 ms router poll | +30–50 ms on *every* device command; dominates per-shot overhead | inproc PUSH→PULL doorbell wakes the poll loop the instant a reply is enqueued |
| F3 | High | ✅ **FIXED** — `processes/device_router.py:840, 464-473` | Blocking `manager.command` publish RPC on the reply critical path | Couples all device command latency to Manager loop health | Audit publish moved to a bounded background thread (`_AsyncEventPublisher`) |
| F4 | Critical | `_manager/process_supervision.py:1188-1290` | Auto-reconnect does blocking device RPCs on the Manager main loop | ~2.5 s loop stall per attempt; stalls all RPC, HB ingestion, and (via F3) all device commands | Dispatch to the existing lifecycle executor |
| F5 | High | `sequencer/runtime.py:465-488` | `tick()` runs until a sleep/wait step; RPC drained only between ticks | Pause/stop/status unresponsive for the duration of a sleepless scan | Bound steps per tick or drain RPC inside the loop |
| F6 | High | `sequencer/runtime.py:1183` | `parallel` unsupported → all cross-device sets sequential | Independent devices serialized; per-point latency scales with device count | Implement bounded parallel step (needs operator sign-off) |
| F7 | Med-High | `_driver/runner.py:237-279` | Telemetry + scheduled streams run inline with RPC in one driver thread | Commands wait behind serial telemetry sweeps / trace acquisitions | Prioritize RPC between telemetry calls or defer telemetry while RPC pending |
| F8 | Medium | `sequencer/sequencer.py:2370-2412` | `set_stream_context` blocking retry loop (up to 6 s) + per-context `hdf.streams.expect` RPC | Inline shot-path overhead; sequencer fully blocked during retries | Make retry non-blocking (tick-based); pipeline the two RPCs |
| F9 | Medium | `_manager/lifecycle.py:253-264`, `process_supervision.py:284-293` | Shutdown stops drivers sequentially, 1 s timeout each | Shutdown scales linearly with unresponsive devices | Fan out via lifecycle executor |
| F10 | Medium | `federation/hub.py:1138-1167` | Federation forward blocks main loop up to peer timeout; new DEALER per call | Slow peer stalls Manager; per-command connect cost | Per-peer worker (like mirrored routes in the router) |
| F11 | Medium | `processes/influx_writer.py:1038-1235` | One HTTP thread for all destinations; no keep-alive | Slow destination (5 s timeout) delays other destinations' batches | Per-destination worker; reuse connections |
| F12 | Medium | `processes/stream_analysis.py:4788` | Fits run inline in the SUB drain loop | Slow fit → SUB backlog → dropped chunk descriptors | Offload fits to a worker; keep drain hot |
| F13 | Medium | `shm/shm_ring.py:208, 296-342` | Extra full copy per SHM write; reader lacks post-copy seq re-check | Doubled memory bandwidth on streams; rare torn read on ring overrun | Write via numpy view; re-check `seq_end` after copy |
| F14 | Medium | `_manager/device_routing.py:61-101` | Manager-side `type:"command"` route blocks main loop (1.5 s) | Latent: any direct internal-RPC command stalls the Manager | Route through lifecycle executor or reject with redirect |
| F15 | Low-Med | ✅ **FIXED** — `sequencer/sequencer.py:2952`, `sequencer/runtime.py` | Fixed 50 ms poll quantizes sleeps and wait_until sampling | `sleep: 0.01` becomes ~50 ms; jitter per shot | Poll timeout now computed from `SequencerRuntime.next_poll_timeout_ms()` (next sleep/wait deadline, capped at 50 ms) |
| F16 | Low-Med | `_driver/runner.py:930`, `drivers/linien_driver.py:30` | Vendor `connect()` runs inline in REP handler with no driver-side timeout | Slow connect (SSH/rpyc) stops heartbeats → spurious demotion; RPC timeout races real success | Document/bound connect; raise per-device connect timeouts |
| F17 | Low | `manager.py:1286` | SUB connects to dead driver PUB endpoints never disconnected | Slow accumulation of reconnecting endpoints over many restarts | Disconnect on driver exit |
| F18 | Info | various | Positive: bounded caches, drop counters, loop-stall events, journal batching | — | Keep |

---

## 3. Detailed findings

### F1 — Manager SUB sockets drained from worker threads (thread-safety violation)

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
- **Tests:** `tests/test_device_router_reply_doorbell.py` — enqueuing a reply makes the `PULL` readable well inside the old 50 ms floor (poller wakes instead of timing out); the reply rides `_reply_queue` while the doorbell frame is empty; many rings drain fully to `Again` (no spin); with no doorbell endpoint the reply still enqueues.

**Post-fix code review (8-angle) — outcome.** Correctness, altitude, and conventions passes came back clean; the `AGENTS.md` gates were re-checked against the change:

- `ruff check src tests examples` passes; the new test is `TestCase`-based and is picked up by the CI `unittest discover` runner (not just pytest).
- `mypy src/experiment_control`: the changed file `device_router.py` has **zero** type errors — the annotations added by this fix introduce none.
- Wire contracts unchanged: the doorbell is a purely internal inproc signal; the `manager.command` topic, response envelope, and all downstream-importable symbols are untouched.
- **Applied:** removed a redundant `setsockopt(zmq.SNDTIMEO, 0)` on the doorbell `PUSH` — the send already passes `zmq.NOBLOCK`, so the timeout had no effect.
- **Not fixed (consistent with existing house style, no action taken):** the small `_close_*` socket helpers duplicate a pattern already repeated ~6× in this file; per-subclass `_close_doorbell` teardown mirrors the existing per-subclass `_stop_event_publisher` convention.

**Deferred issue surfaced during review (outside F2 scope):** `mypy` baseline has regressed. `AGENTS.md` documents 2 known errors (as of 2026-06-18), but the tree now reports **9** errors in 5 files — `_manager/request_routing.py:67` (`_route_manager_ping` attr-defined), `_manager/rpc_calls.py:335` (`_ingest_chunk_ready` attr-defined), `processes/hdf_writer.py:3972/3975/3979` (`Any | None` assignment/arg-type), `manager.py:2046/2095` (`_telemetry_last_recv_mono` attr-defined), plus the 2 documented baseline errors (`manager_network.py:153`, `manager.py:424`). None are in `device_router.py` (unrelated to F2), but the "do not regress mypy" gate is already red and should be triaged separately.

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

### F5 — Sequencer `tick()` is unbounded; RPC starved during sleepless scans

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

### F6 — No parallel step: cross-device sets always sequential

- **Severity:** High (by construction). **Confidence:** Confirmed — and explicitly intentional in v1 (`_execute_parallel_step`: "parallel not supported in v1", `runtime.py:1183–1185`).
- **Behavior/impact:** a scan point that must set, say, a SynthHD frequency, an NKT setpoint, and a Linien parameter performs three sequential blocking round-trips, each paying F2 (+~50 ms) and F3. Per-point overhead scales linearly with device count even though the devices are fully independent (separate processes, separate serial ports/hosts, separate router workers). The infrastructure below the sequencer *already supports* concurrent per-device commands.
- **Unrelated devices forced to wait:** Yes — by sequencing design.
- **Root cause:** v1 scope decision.
- **Recommendation:** implement `parallel` for a restricted case first: a list of `set`/`call` steps targeting *distinct* devices, dispatched concurrently (the router pipelines fine; the client would need concurrent request_ids on its DEALER — already supported by the request_id correlation) and joined with a combined error. Keep same-device ordering strict.
- **Risks/assumptions:** **operator confirmation required** — some sequences may rely on side-effect ordering across devices (e.g., enable RF only after another device is set). Parallel must remain opt-in per step, never inferred.
- **Hardware testing:** required for representative sequences.
- **Measurement:** per-scan-point duration for an N-device set block, sequential vs parallel.

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

### F8 — Sequencer `set_context` path: blocking retries + per-context RPCs

- **Severity:** Medium. **Confidence:** Confirmed.
- **File:** `src/experiment_control/sequencer/sequencer.py` — `_set_stream_context` (L2370–2397, `time.sleep(backoff)` L2396, deadline 6 s), `_expect_streams` (L2399–2412); constants L57–59.
- **Behavior:** each `set_context` step performs one `hdf.streams.expect` process RPC plus one `stream.context.set` device RPC per stream; on transient errors the device RPC is retried in a loop with `time.sleep` (50→500 ms backoff, 6 s deadline) — **blocking the entire sequencer process** (no RPC service, no telemetry drain; heartbeat survives on its thread).
- **Impact:** per-shot overhead of ≥2 router round-trips (each with F2's ~50 ms floor today); on a flaky driver, up to 6 s of total unresponsiveness per stream. Retry layering is bounded and sensible otherwise.
- **Unrelated devices forced to wait:** the whole sequence waits, so effectively yes.
- **Root cause:** synchronous convenience on a shot-critical path.
- **Recommendation:** convert the retry into tick-state (like `_sleep_until`/`_wait_state`) so the loop stays live; issue `hdf.streams.expect` and the context sets for multiple streams concurrently (they are independent endpoints).
- **Risks/assumptions:** context must be set before the triggering `call` step — keep the step non-advancing until all acks arrive.
- **Hardware testing:** not required.
- **Measurement:** per-`set_context` wall time in a representative shot loop.

### F9 — Sequential shutdown; per-device 1 s stops

- **Severity:** Medium. **Confidence:** Confirmed.
- **File:** `src/experiment_control/_manager/lifecycle.py` — `_shutdown_cleanup` (L253–264); `_manager/process_supervision.py` — `stop_driver` shutdown RPC `timeout_ms=1000` (L284–293).
- **Behavior:** shutdown stops each driver in turn; a wedged driver costs the full 1 s RPC timeout before the next is attempted; then each managed process is stopped in turn (each stop can include a ≤500 ms process RPC). The lifecycle executor is already shut down at this point, so no parallelism.
- **Impact:** shutdown time ≈ N_unresponsive × 1 s + process stops; with 20 devices in a failed state, ~20 s+. Cleanup of healthy devices is delayed behind dead ones (violates failure isolation for shutdown specifically). Stop-timeout enforcement/kill escalation does exist (`enforce_device_driver_stop_timeout`) but only runs while the loop is pumping, which it isn't during `_shutdown_cleanup`'s sequential walk.
- **Recommendation:** send all `shutdown` RPCs concurrently (short timeout), then walk terminate/kill; or keep the executor alive until after driver stops.
- **Risks/assumptions:** none for independent devices; if any hardware requires ordered power-down (operator question 2), keep an explicit ordered list.
- **Hardware testing:** recommended once (confirm drivers tolerate concurrent shutdown broadcast).
- **Measurement:** wall time of `_shutdown_cleanup` with k simulated-dead drivers.

### F10 — Federation forward blocks the Manager loop; socket per call

- **Severity:** Medium (only when federation configured). **Confidence:** Confirmed.
- **File:** `src/experiment_control/federation/hub.py` — `_rpc_call` (L1138–1167: `connect_dealer` per call, `_blocking_call_with_pump` on the poll loop), `forward_device_request` (L468).
- **Behavior:** requests for mirrored devices arriving at the Manager are forwarded synchronously on the main loop, waiting up to the peer's `rpc_timeout_ms` (pumping subscriptions meanwhile — safe here, main thread). A fresh DEALER socket is created per call. DNS is properly cached/off-loop (good), and the *router's* mirrored path has per-route worker threads (good) — this Manager-side path is the weaker twin.
- **Impact:** one slow/unreachable peer stalls the Manager loop per forwarded request (compounding F3); per-call TCP connect adds latency.
- **Recommendation:** move Manager-side federation forwards onto per-peer worker threads with persistent sockets (mirror the router's `_MirroredDeviceWorker` design), replying via the lifecycle reply queue.
- **Hardware testing:** no (network testbed suffices).
- **Measurement:** Manager pump-gap while hammering a mirrored device with the peer blackholed.

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

- **Severity:** Medium (perf) / Low (correctness, rare). **Confidence:** Confirmed.
- **File:** `src/experiment_control/shm/shm_ring.py` — `ShmRingWriter.write` L208 (`arr.tobytes(order="C")` allocates + copies before the memoryview copy); `ShmRingReader.read_event`/`read_events` (L296–342, L356–377) read `seq_begin`/`seq_end` **before** copying the payload and never re-validate after.
- **Impact:** (a) every published frame pays a duplicate full-buffer copy — for large traces this doubles memory bandwidth in the driver's timing-sensitive loop; (b) if the writer laps the ring mid-copy (reader slower than producer for a full ring cycle), the reader returns a torn payload silently attributed to the old seq. Low probability with adequate `ring_slots`, but silent.
- **Recommendation:** write via `np.frombuffer(self._buf, dtype, offset=payload_start, count=…)[…] = arr` (no intermediate bytes); in readers, re-read `seq_begin`/`seq_end` after copying and discard on mismatch.
- **Hardware testing:** no.
- **Measurement:** driver publish-latency per frame (t before/after `publish_stream`) for a large dummy trace stream.

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

- **`SequencerRuntime.next_poll_timeout_ms(ceiling_ms=50, floor_ms=1)`** (`sequencer/runtime.py`): returns the time (ms) until the next thing the runtime needs to act on — the earliest of a pending `_sleep_until` deadline or a pending `_wait_state.next_sample_t` sample time — clamped to `[floor_ms, ceiling_ms]`. When the runtime isn't `RUNNING`, or when neither a sleep nor a wait is pending, it returns `ceiling_ms` unchanged, so RPC/control-plane responsiveness (pause/stop/status) for idle or non-sleep/wait ticks is exactly what it was before.
- **`SequencerProcess.run()`** (`sequencer/sequencer.py:2951-2955`): each outer-loop iteration now calls `poll_timeout_ms = self._runtime.next_poll_timeout_ms(ceiling_ms=50)` and passes that to `_poll_and_drain(poll_timeout_ms)` instead of the fixed `_poll_and_drain(50)`. 50 ms remains the *ceiling* (unchanged worst case for RPC latency), not the floor.
- **Scope.** This only changes the poll timeout used *between* ticks; `tick()` itself and its per-call step budget are untouched (that's F5's concern, landing separately).
- **Tests:** `tests/test_sequencer_poll_timeout.py` — unit coverage of `next_poll_timeout_ms` (no-pending falls back to the ceiling; non-`RUNNING` state falls back to the ceiling even with a pending sleep; a pending sleep/wait reports the remaining time, clamped at both the floor and the ceiling; the earlier of a pending sleep vs. wait deadline wins), plus integration coverage driving `SequencerRuntime` through a harness that mimics `run()`'s poll-then-tick loop: a `sleep: 0.005` step completes in single-digit milliseconds (well under the old 50 ms floor), a `wait_until(every_s: 0.02)` step samples at roughly the requested 20 ms cadence (not 50 ms), and a sequence of plain `set`/`call` steps (no sleep/wait ever pending) observes the poll timeout staying at the unchanged 50 ms ceiling on every iteration.

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
- The sequencer's step-at-a-time execution across independent devices (F6).
- The `manager.command` audit RPC coupling all devices to the Manager loop (F3).
- Auto-reconnect and federation forwards on the Manager main loop coupling all control traffic to one sick device/peer (F4, F10).
- Sequential shutdown (F9) and `connect_all_devices` (minor; startup normally uses parallel auto-connect-on-register).

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

The HDF writer is the strongest component reviewed: SUB drain never touches h5py on the hot path; writes are batched to a background thread through a bounded queue with lossless deferral; fsync cadence is decoupled from batching; strict-stream accounting and drop counters are written into the file. Logging in timing-sensitive loops is rate-limited (driver telemetry exceptions, drain-cap events, reconnect events). Metadata (`.attrs`) writes happen at file/measurement boundaries, not per shot. Remaining items: SHM double-copy and seqlock gap (F13); the sequencer/event datasets grow by 1-row resizes (fine at their rates); the `manager.command` event volume itself — every device command produces a manager event + journal row + HDF event-buffer entry — is worth watching at high shot rates (`event_log_mode` already lets operators reduce it).

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
| 3 | F1 thread-guard the SUB pump | High (stability) | Very low | No |
| 4 | F4 auto-reconnect → lifecycle executor | High when enabled | Low-medium | Yes (unplug tests) |
| 5 | F5 tick budget / RPC drain in tick | High (operability/safety) | Low | No |
| 6 | F15 dynamic sequencer poll timeout | Medium (shot-rate) | Low | No |
| 7 | F8 non-blocking set_context retries | Medium | Low-medium | No |
| 8 | F7 driver RPC/telemetry interleaving | Medium-high per device | Medium | **Yes, per driver** |
| 9 | F9 parallel shutdown | Medium | Low | Once |
| 10 | F6 `parallel` step (restricted form) | High for multi-device scans | Medium-high | **Yes + operator sign-off** |
| 11 | F13 SHM copy + seqlock re-check | Medium (large streams) | Low | No |
| 12 | F11 Influx per-destination workers/keep-alive | Medium (monitoring) | Low | No |
| 13 | F10/F14 federation & manager inline command hardening | Medium (deployment-dependent) | Medium | No |
| 14 | F12 analysis fit offload | Medium | Medium | No |
| 15 | F16/F17 connect bounding, SUB disconnects | Low | Low | F16 yes |

## 11. Open questions for the experiment operator

1. **Cross-device ordering:** are there sequences that depend on side-effect order between devices (RF enable after frequency set on *another* device, laser/AOM ordering, interlock preconditions)? Required before implementing `parallel` (F6) or parallel shutdown (F9).
2. **Shared physical buses:** do any "independent" devices share a USB hub, serial multiplexer, or power sequencing that would make concurrent I/O unsafe even across processes?
3. **Audit guarantees:** must the `manager.command` journal entry be durable/ordered *before* the client sees the reply (F3), or is best-effort async acceptable?
4. **Auto-reconnect in production:** is `auto_reconnect.enabled` used in real configs (F4 severity depends on this), and what are acceptable reconnect timings?
5. **Interlock telemetry freshness:** what `max_age` do interlock rules assume? This bounds how much telemetry deferral is allowed in the F7 fix.
6. **Target shot rate:** what step/shot period is the goal? If ≥ 200 ms, F2/F15 matter little; if ≤ 50 ms, they dominate and should be fixed first.
7. **Slow-connect devices:** which devices legitimately need > 1.5 s to connect (Linien with `autostart_server`?), so per-device connect timeouts (F16) can be set instead of the global default?
8. **Windows COM-port exclusivity:** has in-process reconnect of the NKT (whose `disconnect()` is a GC-reliant no-op) been observed to fail with "port in use"?
