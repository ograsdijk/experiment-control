# ruff: noqa: E402

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import SequenceSpec, SetContextStep
from experiment_control.sequencer.runtime import SequencerRuntime
from experiment_control.sequencer.sequencer import SequencerProcess


def _build_runtime(
    calls: list[tuple[str, str, int, dict[str, object]]],
    *,
    set_stream_context_impl: object | None = None,
    expect_streams_impl: object | None = None,
) -> SequencerRuntime:
    def call_device(device_id: str, action: str, params: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "result": None}

    def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
        return None

    def set_stream_context(
        device_id: str,
        stream: str,
        context_id: int,
        fields: dict[str, object],
    ) -> None:
        if callable(set_stream_context_impl):
            set_stream_context_impl(device_id, stream, context_id, fields)
            return
        calls.append((device_id, stream, context_id, dict(fields)))

    def expect_streams(streams: list[tuple[str, str]], context_id: int) -> None:
        if callable(expect_streams_impl):
            expect_streams_impl(streams, context_id)

    return SequencerRuntime(
        call_device=call_device,
        get_telemetry=get_telemetry,
        set_stream_context=set_stream_context,
        expect_streams=expect_streams,
    )


class SequencerContextIdTests(unittest.TestCase):
    def test_context_id_keeps_incrementing_across_start(self) -> None:
        calls: list[tuple[str, str, int, dict[str, object]]] = []
        runtime = _build_runtime(calls)
        spec = SequenceSpec(
            version=1,
            meta={},
            vars={},
            steps=[
                SetContextStep(
                    streams=[{"device": "scope", "stream": "trace"}],
                    fields={"freq_hz": 1.0},
                )
            ],
            context_columns=None,
        )
        runtime.load(spec)

        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        context_ids = [item[2] for item in calls]
        self.assertEqual(context_ids, [0, 1])

    def test_status_exposes_context_counters(self) -> None:
        calls: list[tuple[str, str, int, dict[str, object]]] = []
        runtime = _build_runtime(calls)
        spec = SequenceSpec(
            version=1,
            meta={},
            vars={},
            steps=[
                SetContextStep(
                    streams=[{"device": "scope", "stream": "trace"}],
                    fields={"freq_hz": 1.0},
                )
            ],
            context_columns=None,
        )
        runtime.load(spec)

        initial = runtime.status()
        self.assertEqual(initial.get("last_context_id"), -1)
        self.assertEqual(initial.get("next_context_id"), 0)

        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        after = runtime.status()
        self.assertEqual(after.get("last_context_id"), 0)
        self.assertEqual(after.get("next_context_id"), 1)

    def test_set_context_failure_marks_runtime_error(self) -> None:
        calls: list[tuple[str, str, int, dict[str, object]]] = []

        def fail_set_context(
            device_id: str, stream: str, context_id: int, fields: dict[str, object]
        ) -> None:
            del device_id, stream, context_id, fields
            raise RuntimeError("device restarting")

        runtime = _build_runtime(calls, set_stream_context_impl=fail_set_context)
        spec = SequenceSpec(
            version=1,
            meta={},
            vars={},
            steps=[
                SetContextStep(
                    streams=[{"device": "scope", "stream": "trace"}],
                    fields={"freq_hz": 1.0},
                )
            ],
            context_columns=None,
        )
        runtime.load(spec)
        runtime.start()
        runtime.tick()

        status = runtime.status()
        self.assertEqual(status.get("state"), "ERROR")
        self.assertIn("set_context failed", str(status.get("error")))

    def test_set_context_registers_stream_expectations_before_device_context(self) -> None:
        calls: list[tuple[str, str, int, dict[str, object]]] = []
        order: list[str] = []

        def expect_streams(streams: list[tuple[str, str]], context_id: int) -> None:
            order.append(f"expect:{context_id}:{streams[0][0]}.{streams[0][1]}")

        def set_stream_context(
            device_id: str, stream: str, context_id: int, fields: dict[str, object]
        ) -> None:
            del fields
            order.append(f"context:{context_id}:{device_id}.{stream}")
            calls.append((device_id, stream, context_id, {}))

        runtime = _build_runtime(
            calls,
            set_stream_context_impl=set_stream_context,
            expect_streams_impl=expect_streams,
        )
        spec = SequenceSpec(
            version=1,
            meta={},
            vars={},
            steps=[
                SetContextStep(
                    streams=[{"device": "scope", "stream": "trace"}],
                    fields={"freq_hz": 1.0},
                )
            ],
            context_columns=None,
        )
        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        self.assertEqual(order, ["expect:0:scope.trace", "context:0:scope.trace"])


class SequencerSetContextRetryTests(unittest.TestCase):
    def test_start_lifecycle_payload_includes_context_columns(self) -> None:
        class FakeRuntime:
            def start(self, **kwargs: object) -> None:
                self.start_kwargs = kwargs

            def status(self) -> dict[str, object]:
                return {"run_id": "run-1"}

        process = object.__new__(SequencerProcess)
        process._runtime = FakeRuntime()
        process._sequence_library = None
        process._active_sequence_id = "scan"
        process._loaded_sequence_source = "scan.yaml"
        process._context_columns = {"freq_step_index": "int64"}
        published: list[dict[str, object]] = []

        def capture_lifecycle(**kwargs: object) -> None:
            published.append(kwargs)

        process._publish_lifecycle_event = capture_lifecycle  # type: ignore[method-assign]

        response = process._rpc_sequencer_start({"params": {}})

        self.assertTrue(response["ok"])
        self.assertEqual(published[0]["event"], "start")
        payload = published[0]["payload"]
        self.assertIsInstance(payload, dict)
        self.assertEqual(
            payload.get("context_columns"),  # type: ignore[union-attr]
            {"freq_step_index": "int64"},
        )

    def test_set_stream_context_retries_transient_error(self) -> None:
        process = object.__new__(SequencerProcess)
        calls: list[tuple[str, str, dict[str, object]]] = []
        responses = iter(
            [
                {"ok": False, "error": "Resource temporarily unavailable"},
                {"ok": True, "result": None},
            ]
        )

        def fake_call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            calls.append((device_id, action, params))
            return next(responses)

        process._call_device = fake_call_device  # type: ignore[attr-defined]
        with mock.patch("experiment_control.sequencer.sequencer.time.sleep", return_value=None):
            process._set_stream_context("trace1", "trace", 4, {"trial": 1})  # type: ignore[misc]

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "stream.context.set")

    def test_set_stream_context_raises_on_non_transient_error(self) -> None:
        process = object.__new__(SequencerProcess)
        calls: list[tuple[str, str, dict[str, object]]] = []

        def fake_call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            calls.append((device_id, action, params))
            return {"ok": False, "error": "Unknown stream 'trace'"}

        process._call_device = fake_call_device  # type: ignore[attr-defined]
        with self.assertRaises(RuntimeError):
            process._set_stream_context("trace1", "trace", 4, {"trial": 1})  # type: ignore[misc]
        self.assertEqual(len(calls), 1)

    def test_expect_streams_raises_when_hdf_rejects(self) -> None:
        process = object.__new__(SequencerProcess)

        def fake_call_process(
            process_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            self.assertEqual(process_id, "hdf_writer")
            self.assertEqual(action, "hdf.streams.expect")
            self.assertEqual(params["context_id"], 2)
            return {
                "ok": False,
                "error": {
                    "code": "hdf_not_writing",
                    "message": "HDF writer is not writing",
                },
            }

        process._call_process = fake_call_process  # type: ignore[attr-defined]
        with self.assertRaisesRegex(RuntimeError, "hdf.streams.expect failed"):
            process._expect_streams([("scope", "trace")], 2)  # type: ignore[misc]


def _single_set_context_spec() -> SequenceSpec:
    return SequenceSpec(
        version=1,
        meta={},
        vars={},
        steps=[
            SetContextStep(
                streams=[{"device": "scope", "stream": "trace"}],
                fields={"freq_hz": 1.0},
            )
        ],
        context_columns=None,
    )


class SequencerSetContextTickBasedRetryTests(unittest.TestCase):
    """F8: set_context must not block the tick loop while a retry/backoff
    is pending, and must not advance until every stream's RPC has acked."""

    def test_set_context_step_does_not_block_tick_loop_while_pending(self) -> None:
        # Simulates a device.stream.context.set retry/backoff that would
        # previously have been a blocking `time.sleep` on the sequencer's
        # own thread. `poll_set_context` reports "still pending" until a
        # deadline elapses; the fix under test is that `runtime.tick()`
        # itself must return promptly on every call instead of blocking
        # until that deadline -- i.e. no sleep happens inside tick().
        ready_at: dict[str, float] = {}

        def begin_set_context(streams, context_id, fields):
            del streams, fields
            ready_at["t"] = time.monotonic() + 0.25
            return {"context_id": context_id}

        def poll_set_context(state, now):
            del state
            if now < ready_at["t"]:
                return False, None
            return True, None

        runtime = SequencerRuntime(
            call_device=lambda *a, **k: {"ok": True, "result": None},
            get_telemetry=lambda *a, **k: None,
            set_stream_context=lambda *a, **k: None,
            expect_streams=lambda *a, **k: None,
            begin_set_context=begin_set_context,
            poll_set_context=poll_set_context,
        )
        runtime.load(_single_set_context_spec())
        runtime.start()

        tick_durations: list[float] = []
        deadline = time.monotonic() + 3.0
        while runtime.state == "RUNNING" and time.monotonic() < deadline:
            t0 = time.perf_counter()
            runtime.tick()
            tick_durations.append(time.perf_counter() - t0)
            # Mimic the sequencer process's own tick cadence -- the point
            # under test is that `tick()` itself never blocks, not that the
            # calling loop is instantaneous.
            time.sleep(0.005)

        self.assertEqual(runtime.state, "STOPPED")
        self.assertGreater(len(tick_durations), 5)
        # The regression this guards against: a blocking `time.sleep(backoff)`
        # inside the RPC-retry path would make some tick() call take close to
        # the full 0.25s pending window. Every call must stay well under it.
        self.assertLess(max(tick_durations), 0.05)

    def test_set_context_step_does_not_advance_until_all_acks_received(self) -> None:
        poll_log: list[int] = []

        def begin_set_context(streams, context_id, fields):
            del streams, context_id, fields
            return {"n": 0}

        def poll_set_context(state, now):
            del now
            state["n"] += 1
            poll_log.append(state["n"])
            if state["n"] < 3:
                return False, None
            return True, None

        runtime = SequencerRuntime(
            call_device=lambda *a, **k: {"ok": True, "result": None},
            get_telemetry=lambda *a, **k: None,
            set_stream_context=lambda *a, **k: None,
            expect_streams=lambda *a, **k: None,
            begin_set_context=begin_set_context,
            poll_set_context=poll_set_context,
        )
        runtime.load(_single_set_context_spec())
        runtime.start()

        # First tick begins the step's dispatch (nothing to poll yet).
        runtime.tick()
        self.assertEqual(runtime.state, "RUNNING")
        self.assertEqual(poll_log, [])

        # Each subsequent tick polls once; the step must not advance/complete
        # the sequence until poll_set_context reports finished.
        runtime.tick()
        self.assertEqual(runtime.state, "RUNNING")
        self.assertEqual(poll_log, [1])

        runtime.tick()
        self.assertEqual(runtime.state, "RUNNING")
        self.assertEqual(poll_log, [1, 2])

        # Third poll reports all acks received -> step (and sequence) finish.
        runtime.tick()
        self.assertEqual(runtime.state, "STOPPED")
        self.assertEqual(poll_log, [1, 2, 3])

    def test_set_context_step_surfaces_timeout_error_non_blockingly(self) -> None:
        def begin_set_context(streams, context_id, fields):
            del streams, context_id, fields
            return object()

        def poll_set_context(state, now):
            del state, now
            return True, (
                "stream.context.set failed for scope/trace after 4 attempts: timeout"
            )

        runtime = SequencerRuntime(
            call_device=lambda *a, **k: {"ok": True, "result": None},
            get_telemetry=lambda *a, **k: None,
            set_stream_context=lambda *a, **k: None,
            expect_streams=lambda *a, **k: None,
            begin_set_context=begin_set_context,
            poll_set_context=poll_set_context,
        )
        runtime.load(_single_set_context_spec())
        runtime.start()
        runtime.tick()  # begins the dispatch
        runtime.tick()  # polls it and observes the error

        status = runtime.status()
        self.assertEqual(status.get("state"), "ERROR")
        self.assertIn("set_context failed", str(status.get("error")))
        self.assertIn("timeout", str(status.get("error")))

    def test_stop_requested_while_pending_cancels_the_dispatch(self) -> None:
        # F8 review fix #3: an outstanding set_context dispatch must get a
        # best-effort cancel when the step is abandoned (operator stops the
        # run mid-retry), not be silently orphaned in `_set_context_state`
        # forever (which would also leak its worker threads' results).
        cancelled: list[bool] = []

        class _FakeDispatch:
            def cancel(self) -> None:
                cancelled.append(True)

        def begin_set_context(streams, context_id, fields):
            del streams, context_id, fields
            return _FakeDispatch()

        def poll_set_context(state, now):
            del state, now
            return False, None  # never resolves on its own

        runtime = SequencerRuntime(
            call_device=lambda *a, **k: {"ok": True, "result": None},
            get_telemetry=lambda *a, **k: None,
            set_stream_context=lambda *a, **k: None,
            expect_streams=lambda *a, **k: None,
            begin_set_context=begin_set_context,
            poll_set_context=poll_set_context,
        )
        runtime.load(_single_set_context_spec())
        runtime.start()
        runtime.tick()  # begins the dispatch
        self.assertEqual(runtime.state, "RUNNING")
        self.assertEqual(cancelled, [])

        runtime.request_stop()
        runtime.tick()  # observes the stop request; must cancel the dispatch

        self.assertEqual(cancelled, [True])
        self.assertEqual(runtime.state, "STOPPED")
        self.assertIsNone(runtime._set_context_state)  # noqa: SLF001

    def test_falls_back_to_synchronous_path_when_dispatch_not_wired(self) -> None:
        # Callers/tests that only wire `set_stream_context`/`expect_streams`
        # (no `begin_set_context`/`poll_set_context`) must keep working
        # exactly as before -- single-tick synchronous completion.
        calls: list[tuple[str, str, int]] = []

        def set_stream_context(device_id, stream, context_id, fields):
            del fields
            calls.append((device_id, stream, context_id))

        runtime = SequencerRuntime(
            call_device=lambda *a, **k: {"ok": True, "result": None},
            get_telemetry=lambda *a, **k: None,
            set_stream_context=set_stream_context,
            expect_streams=lambda *a, **k: None,
        )
        runtime.load(_single_set_context_spec())
        runtime.start()
        runtime.tick()

        self.assertEqual(runtime.state, "STOPPED")
        self.assertEqual(calls, [("scope", "trace", 0)])


class SequencerProcessSetContextDispatchTests(unittest.TestCase):
    """Exercises the real `SequencerProcess._begin_set_context` /
    `_poll_set_context` dispatch pool (F8)."""

    def _process_with_executor(self) -> SequencerProcess:
        process = object.__new__(SequencerProcess)
        process._context_executor = ThreadPoolExecutor(  # type: ignore[attr-defined]
            max_workers=8, thread_name_prefix="test-set-context"
        )
        return process

    def _drain(self, process: SequencerProcess, dispatch, *, deadline_s: float = 3.0):
        finished = False
        error: str | None = None
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            finished, error = process._poll_set_context(  # type: ignore[misc]
                dispatch, time.monotonic()
            )
            if finished:
                break
            time.sleep(0.01)
        return finished, error

    def test_context_set_calls_dispatch_concurrently_with_each_other(self) -> None:
        # The per-stream `stream.context.set` calls (for streams that don't
        # depend on each other) must run concurrently rather than one at a
        # time. `expect` is intentionally NOT part of this barrier: it must
        # complete strictly before any context_set call starts (see the next
        # test), so including it here would deadlock by construction.
        process = self._process_with_executor()
        streams = [("dev1", "s1"), ("dev2", "s2"), ("dev3", "s3")]
        barrier = threading.Barrier(len(streams))

        process._expect_streams = lambda streams_arg, context_id: None  # type: ignore[attr-defined]

        def fake_set_stream_context(device_id, stream, context_id, fields) -> None:
            del device_id, stream, context_id, fields
            # Only returns once all N calls are simultaneously in flight --
            # a serial (one-at-a-time) dispatch would deadlock here and raise
            # BrokenBarrierError on the timeout instead.
            barrier.wait(timeout=2.0)

        process._set_stream_context = fake_set_stream_context  # type: ignore[attr-defined]

        try:
            dispatch = process._begin_set_context(streams, 0, {})  # type: ignore[misc]
            finished, error = self._drain(process, dispatch)
        finally:
            process._context_executor.shutdown(wait=True)  # type: ignore[attr-defined]

        self.assertTrue(finished)
        self.assertIsNone(error)

    def test_context_set_not_dispatched_until_expect_completes(self) -> None:
        # F8 correctness fix: hdf.streams.expect is registered strict=True,
        # so a device must not be allowed to switch context (and start
        # emitting samples under the new context) before the writer has
        # processed the expect call, or those samples are silently dropped.
        process = self._process_with_executor()
        expect_done = threading.Event()
        order: list[str] = []
        lock = threading.Lock()

        def fake_expect_streams(streams_arg, context_id) -> None:
            del streams_arg, context_id
            time.sleep(0.1)
            with lock:
                order.append("expect")
            expect_done.set()

        def fake_set_stream_context(device_id, stream, context_id, fields) -> None:
            del context_id, fields
            # If this ever runs before expect_done is set, the ordering
            # guarantee is broken -- record it so the assertion below fails
            # with a clear message instead of silently racing.
            with lock:
                order.append(f"context:{device_id}/{stream}:expect_done={expect_done.is_set()}")

        process._expect_streams = fake_expect_streams  # type: ignore[attr-defined]
        process._set_stream_context = fake_set_stream_context  # type: ignore[attr-defined]

        try:
            dispatch = process._begin_set_context(  # type: ignore[misc]
                [("dev1", "s1"), ("dev2", "s2")], 0, {}
            )
            finished, error = self._drain(process, dispatch)
        finally:
            process._context_executor.shutdown(wait=True)  # type: ignore[attr-defined]

        self.assertTrue(finished)
        self.assertIsNone(error)
        self.assertEqual(order[0], "expect")
        for entry in order[1:]:
            self.assertIn("expect_done=True", entry)

    def test_expect_failure_prevents_any_context_set_dispatch(self) -> None:
        process = self._process_with_executor()
        context_set_calls: list[tuple[str, str]] = []

        def failing_expect_streams(streams_arg, context_id) -> None:
            del streams_arg, context_id
            raise RuntimeError("hdf.streams.expect failed: hdf_not_writing")

        def fake_set_stream_context(device_id, stream, context_id, fields) -> None:
            del context_id, fields
            context_set_calls.append((device_id, stream))

        process._expect_streams = failing_expect_streams  # type: ignore[attr-defined]
        process._set_stream_context = fake_set_stream_context  # type: ignore[attr-defined]

        try:
            dispatch = process._begin_set_context(  # type: ignore[misc]
                [("dev1", "s1")], 0, {}
            )
            finished, error = self._drain(process, dispatch)
        finally:
            process._context_executor.shutdown(wait=True)  # type: ignore[attr-defined]

        self.assertTrue(finished)
        self.assertIsNotNone(error)
        self.assertIn("hdf_not_writing", str(error))
        # The per-stream context_set call must never have been dispatched.
        self.assertEqual(context_set_calls, [])

    def test_poll_reports_pending_until_every_call_completes(self) -> None:
        process = self._process_with_executor()
        release = threading.Event()

        def slow_set_stream_context(device_id, stream, context_id, fields) -> None:
            del device_id, stream, context_id, fields
            release.wait(timeout=2.0)

        process._expect_streams = lambda streams, context_id: None  # type: ignore[attr-defined]
        process._set_stream_context = slow_set_stream_context  # type: ignore[attr-defined]

        try:
            dispatch = process._begin_set_context(  # type: ignore[misc]
                [("dev1", "s1")], 0, {}
            )
            finished, error = process._poll_set_context(  # type: ignore[misc]
                dispatch, time.monotonic()
            )
            self.assertFalse(finished)
            self.assertIsNone(error)

            release.set()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not finished:
                finished, error = process._poll_set_context(  # type: ignore[misc]
                    dispatch, time.monotonic()
                )
                if not finished:
                    time.sleep(0.01)
        finally:
            process._context_executor.shutdown(wait=True)  # type: ignore[attr-defined]

        self.assertTrue(finished)
        self.assertIsNone(error)

    def test_poll_surfaces_error_from_any_failed_stream(self) -> None:
        process = self._process_with_executor()

        def failing_set_stream_context(device_id, stream, context_id, fields) -> None:
            del context_id, fields
            raise RuntimeError(f"stream.context.set failed for {device_id}/{stream}: boom")

        process._expect_streams = lambda streams, context_id: None  # type: ignore[attr-defined]
        process._set_stream_context = failing_set_stream_context  # type: ignore[attr-defined]

        try:
            dispatch = process._begin_set_context(  # type: ignore[misc]
                [("dev1", "s1")], 0, {}
            )
            finished = False
            error: str | None = None
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                finished, error = process._poll_set_context(  # type: ignore[misc]
                    dispatch, time.monotonic()
                )
                if finished:
                    break
                time.sleep(0.01)
        finally:
            process._context_executor.shutdown(wait=True)  # type: ignore[attr-defined]

        self.assertTrue(finished)
        self.assertIsNotNone(error)
        self.assertIn("boom", str(error))

    def test_dispatch_wide_deadline_fails_step_even_if_call_still_queued(self) -> None:
        # F8 review fix #4: `_poll_set_context` must not wait purely on
        # `future.done()` forever -- a call still queued behind a saturated
        # pool (or a driver that never returns) needs a dispatch-wide safety
        # net independent of any single call's own internal retry deadline.
        process = self._process_with_executor()
        release = threading.Event()

        def blocking_set_stream_context(device_id, stream, context_id, fields) -> None:
            del device_id, stream, context_id, fields
            release.wait(timeout=5.0)

        process._expect_streams = lambda streams, context_id: None  # type: ignore[attr-defined]
        process._set_stream_context = blocking_set_stream_context  # type: ignore[attr-defined]

        try:
            dispatch = process._begin_set_context(  # type: ignore[misc]
                [("dev1", "s1")], 0, {}
            )
            # Simulate the dispatch-wide deadline having already elapsed
            # while the call is still running/queued, instead of waiting out
            # the real multi-second deadline in a unit test.
            dispatch.deadline = time.monotonic() - 0.01  # type: ignore[attr-defined]
            finished, error = process._poll_set_context(  # type: ignore[misc]
                dispatch, time.monotonic()
            )
        finally:
            release.set()
            process._context_executor.shutdown(wait=True)  # type: ignore[attr-defined]

        self.assertTrue(finished)
        self.assertIsNotNone(error)
        self.assertIn("timed out", str(error))


if __name__ == "__main__":
    unittest.main()
