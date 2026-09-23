"""Async hdf.writing.stop / hdf.rotate: the op runs on the bg thread while the
main loop stays responsive (hold mode), see `_FileOpRequest`."""

import json
import shutil
import sys
import threading
import time
import unittest
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

import h5py
import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.hdf_writer import HdfWriter  # noqa: E402


class _FakeSub:
    """Stands in for the writer's SUB socket: yields queued frames, then Again."""

    def __init__(self) -> None:
        self.frames: deque[tuple[bytes, bytes]] = deque()

    def push_log(self, message: str) -> None:
        payload = {
            "severity": "error",
            "message": message,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        self.frames.append((b"manager.log", json.dumps(payload).encode("utf-8")))

    def recv_multipart(self, flags: int = 0) -> tuple[bytes, bytes]:
        if not self.frames:
            raise zmq.Again()
        return self.frames.popleft()


def _wait_for(predicate: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _event_messages(path: str) -> list[str]:
    with h5py.File(path, "r") as h5:
        rows = h5["events/data"][...]
    out = []
    for row in rows:
        raw = row["message"]
        out.append(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
    return out


def _log_event(message: str) -> tuple[str, dict[str, Any]]:
    return (
        "manager.log",
        {
            "severity": "error",
            "message": message,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        },
    )


class HdfWriterAsyncFileOpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / f"tmp_{uuid.uuid4().hex}"
        self.tmp.mkdir(parents=True)
        self.writer = HdfWriter(
            out_dir=str(self.tmp),
            filename=None,
            manager_rpc="tcp://127.0.0.1:65571",
            manager_pub="tcp://127.0.0.1:65572",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
            bg_join_timeout_s=5.0,
        )
        w = self.writer
        w._buf = deque(maxlen=1000)  # noqa: SLF001
        w._event_buf = deque(maxlen=1000)  # noqa: SLF001
        self.sub = _FakeSub()
        w._sub = self.sub  # type: ignore[assignment]  # noqa: SLF001
        self.first_file = w._start_writing_file(filename="first.h5")  # noqa: SLF001
        # Gate the op mid-close so tests can observe the in-flight state.
        self.gate = threading.Event()
        self.op_entered = threading.Event()
        original = w._finalize_strict_streams_locked  # noqa: SLF001

        def _gated_finalize() -> None:
            self.op_entered.set()
            self.assertTrue(self.gate.wait(timeout=10.0))
            original()

        w._finalize_strict_streams_locked = _gated_finalize  # type: ignore[method-assign]  # noqa: SLF001
        w._start_bg_thread()  # noqa: SLF001

    def tearDown(self) -> None:
        self.gate.set()
        w = self.writer
        w._sub = None  # noqa: SLF001
        w.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rpc(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.writer._handle_rpc(  # noqa: SLF001
            {"request_id": uuid.uuid4().hex, "type": action, "params": params or {}}
        )

    def _run_main_loop_until_idle(self) -> None:
        """Drive the hold-mode part of the main loop until the op finishes."""
        w = self.writer

        def _step() -> bool:
            w._hold_socket_messages()  # noqa: SLF001
            w._service_file_op()  # noqa: SLF001
            return w._file_op is None  # noqa: SLF001

        self.assertTrue(_wait_for(_step))

    def test_stop_replies_before_close_and_finishes_in_background(self) -> None:
        w = self.writer
        t0 = time.monotonic()
        resp = self._rpc("hdf.writing.stop")
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertTrue(resp["ok"])
        result = resp["result"]
        self.assertTrue(result["accepted"])
        self.assertEqual(result["old_file"], self.first_file)
        self.assertEqual(result["file_state"], "closing")

        self.assertTrue(self.op_entered.wait(timeout=5.0))
        # Interlocks gate reconfig on writing_active: it must stay true until
        # the file is really closed.
        self.assertTrue(w._writing_active)  # noqa: SLF001
        status = self._rpc("hdf.status")["result"]
        self.assertEqual(status["file_state"], "closing")
        self.assertEqual(status["file_op"]["op"], "stop")
        self.assertTrue(status["writing_active"])

        self.gate.set()
        self._run_main_loop_until_idle()
        self.assertIsNone(w._h5)  # noqa: SLF001
        self.assertFalse(w._writing_active)  # noqa: SLF001
        status = self._rpc("hdf.status")["result"]
        self.assertEqual(status["file_state"], "idle")
        last = status["last_file_op"]
        self.assertTrue(last["ok"])
        self.assertEqual(last["op"], "stop")
        self.assertEqual(last["file"], self.first_file)
        for phase in ("drain", "strict_streams", "flush", "close"):
            self.assertIn(phase, last["phase_timings_s"])
        # The file is closed and readable.
        with h5py.File(self.first_file, "r") as h5:
            self.assertIn("events", h5)

    def test_rpcs_during_stop_are_gated(self) -> None:
        self._rpc("hdf.writing.stop")
        self.assertTrue(self.op_entered.wait(timeout=5.0))

        again = self._rpc("hdf.writing.stop")
        self.assertTrue(again["ok"])
        self.assertTrue(again["result"]["accepted"])

        for action, params in (
            ("hdf.writing.start", {"filename": "next.h5"}),
            ("hdf.rotate", {"filename": "next.h5"}),
            ("hdf.rotate_file", {"filename": "next.h5"}),  # alias
            ("hdf.devices.disable", {"device_ids": ["d0"]}),
            ("hdf.measurement.note", {"message": "x"}),
        ):
            resp = self._rpc(action, params)
            self.assertFalse(resp["ok"], action)
            self.assertEqual(resp["error"]["code"], "file_op_in_progress", action)
            self.assertEqual(resp["error"]["retry_after_ms"], 1000)

        self.assertTrue(self._rpc("hdf.devices.get")["ok"])
        self.assertTrue(self._rpc("hdf.processes.get")["ok"])

        self.gate.set()
        self._run_main_loop_until_idle()
        start = self._rpc("hdf.writing.start", {"filename": "next.h5"})
        self.assertTrue(start["ok"], start)

    def test_stop_writes_data_received_before_it_but_not_after(self) -> None:
        w = self.writer
        topic, msg = _log_event("before-stop")
        w._buffer_event(topic=topic, msg=msg)  # noqa: SLF001
        self._rpc("hdf.writing.stop")
        self.assertTrue(self.op_entered.wait(timeout=5.0))
        self.sub.push_log("during-stop")
        w._hold_socket_messages()  # noqa: SLF001
        self.assertEqual(len(w._held_messages), 1)  # noqa: SLF001
        self.assertEqual(self._rpc("hdf.status")["result"]["held_messages"], 1)

        self.gate.set()
        self._run_main_loop_until_idle()
        messages = _event_messages(self.first_file)
        self.assertIn("before-stop", messages)
        self.assertNotIn("during-stop", messages)
        # Replayed to the (now idle) writer like any message arriving late.
        self.assertEqual(len(w._held_messages), 0)  # noqa: SLF001
        self.assertEqual(w._last_file_op["held_messages"], 1)  # noqa: SLF001

    def test_rotate_lands_gap_messages_in_new_file(self) -> None:
        w = self.writer
        topic, msg = _log_event("before-rotate")
        w._buffer_event(topic=topic, msg=msg)  # noqa: SLF001
        resp = self._rpc("hdf.rotate", {"filename": "second.h5"})
        self.assertTrue(resp["ok"], resp)
        result = resp["result"]
        self.assertTrue(result["accepted"])
        self.assertEqual(result["old_file"], self.first_file)
        self.assertEqual(Path(result["new_file"]).name, "second.h5")
        self.assertTrue(result["measurement_id"])
        self.assertEqual(result["file_state"], "rotating")

        self.assertTrue(self.op_entered.wait(timeout=5.0))
        self.sub.push_log("during-rotate")
        w._hold_socket_messages()  # noqa: SLF001
        self.gate.set()
        self._run_main_loop_until_idle()

        last = w._last_file_op  # noqa: SLF001
        assert last is not None
        self.assertTrue(last["ok"], last)
        self.assertEqual(last["op"], "rotate")
        self.assertIn("configure_new", last["phase_timings_s"])
        self.assertIn("close_old", last["phase_timings_s"])
        self.assertTrue(w._writing_active)  # noqa: SLF001
        assert w._h5 is not None  # noqa: SLF001
        new_file = str(w._h5.filename)  # noqa: SLF001
        self.assertEqual(Path(new_file).name, "second.h5")
        self.assertEqual(w._measurement_id, result["measurement_id"])  # noqa: SLF001

        self.assertEqual(_event_messages(self.first_file), ["before-rotate"])
        # The replayed gap message goes to the new file on the next write.
        with w._h5_lock:  # noqa: SLF001
            w._drain_pending_to_file()  # noqa: SLF001
        stop = self._rpc("hdf.writing.stop")
        self.assertTrue(stop["ok"])
        self._run_main_loop_until_idle()
        self.assertEqual(_event_messages(new_file), ["during-rotate"])

    def test_rotate_rejects_existing_filename_in_reply(self) -> None:
        (self.tmp / "taken.h5").write_bytes(b"already here")
        resp = self._rpc("hdf.rotate", {"filename": "taken.h5"})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "file_exists")
        self.assertIsNone(self.writer._file_op)  # noqa: SLF001

    def test_rotate_failure_keeps_old_file_active(self) -> None:
        w = self.writer

        def _boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("configure exploded")

        w._configure_active_file = _boom  # type: ignore[method-assign]  # noqa: SLF001
        resp = self._rpc("hdf.rotate", {"filename": "second.h5"})
        self.assertTrue(resp["ok"])
        self.gate.set()
        self._run_main_loop_until_idle()

        last = w._last_file_op  # noqa: SLF001
        assert last is not None
        self.assertFalse(last["ok"])
        self.assertEqual(last["error"]["code"], "rotate_failed")
        self.assertIn("configure exploded", last["error"]["message"])
        assert w._h5 is not None  # noqa: SLF001
        self.assertEqual(str(w._h5.filename), self.first_file)  # noqa: SLF001
        self.assertFalse((self.tmp / "second.h5").exists())
        self.assertEqual(self._rpc("hdf.status")["result"]["file_state"], "writing")

    def test_held_message_overflow_is_counted(self) -> None:
        w = self.writer
        w._held_messages = deque(maxlen=2)  # noqa: SLF001
        self._rpc("hdf.writing.stop")
        self.assertTrue(self.op_entered.wait(timeout=5.0))
        for i in range(5):
            self.sub.push_log(f"m{i}")
        w._hold_socket_messages()  # noqa: SLF001
        self.assertEqual(len(w._held_messages), 2)  # noqa: SLF001
        self.assertEqual(w._held_dropped, 3)  # noqa: SLF001
        self.gate.set()
        self._run_main_loop_until_idle()
        self.assertEqual(w._last_file_op["held_dropped"], 3)  # noqa: SLF001

    def test_close_during_stop_waits_for_the_op(self) -> None:
        w = self.writer
        self._rpc("hdf.writing.stop")
        self.assertTrue(self.op_entered.wait(timeout=5.0))
        threading.Timer(0.3, self.gate.set).start()
        w._stop_evt.set()  # noqa: SLF001  (process.stop)
        w._sub = None  # noqa: SLF001
        w.close()
        self.assertIsNone(w._h5)  # noqa: SLF001
        with h5py.File(self.first_file, "r") as h5:
            self.assertIn("events", h5)

    def test_without_bg_thread_stop_stays_synchronous(self) -> None:
        w = self.writer
        w._stop_evt.set()  # noqa: SLF001
        assert w._bg_thread is not None  # noqa: SLF001
        self.assertTrue(_wait_for(lambda: not w._bg_thread.is_alive()))  # noqa: SLF001
        self.gate.set()
        resp = self._rpc("hdf.writing.stop")
        self.assertTrue(resp["ok"])
        self.assertNotIn("accepted", resp["result"])
        self.assertIsNone(w._h5)  # noqa: SLF001
        self.assertIn("close", resp["result"]["phase_timings_s"])


class HdfWriterAsyncFileOpRunLoopTests(unittest.TestCase):
    """End-to-end through the real `run()` loop: a real SUB socket fed by a
    PUB, with RPCs injected onto the loop thread (as `_drain_rpc` would)."""

    def test_run_loop_stays_responsive_while_stop_closes(self) -> None:
        tmp = ROOT / ".tmp_tests" / f"tmp_{uuid.uuid4().hex}"
        tmp.mkdir(parents=True)
        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        port = pub.bind_to_random_port("tcp://127.0.0.1")
        writer = HdfWriter(
            out_dir=str(tmp),
            filename="e2e.h5",
            manager_rpc="tcp://127.0.0.1:65573",
            manager_pub=f"tcp://127.0.0.1:{port}",
            rpc_timeout_ms=500,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=0.1,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=0.2,
            disabled_devices=[],
            event_log_mode="all",
            autostart_writing=False,
            bg_join_timeout_s=5.0,
        )
        # run() only builds the SUB socket + loop for a managed process. There
        # is no manager here, so keep file setup off the manager RPCs.
        writer._process_id = "hdf_writer"  # noqa: SLF001
        original_configure = writer._configure_active_file  # noqa: SLF001

        def _configure_without_manager(h5: Any, **kwargs: Any) -> None:
            kwargs["load_manager_state"] = False
            original_configure(h5, **kwargs)

        writer._configure_active_file = _configure_without_manager  # type: ignore[method-assign]  # noqa: SLF001
        calls: deque[tuple[Callable[[], Any], list[Any], threading.Event]] = deque()
        original_poll = writer._poll_and_drain  # noqa: SLF001

        def _poll_with_injected_rpcs(timeout_ms: int) -> Any:
            while calls:
                fn, out, done = calls.popleft()
                out.append(fn())
                done.set()
            return original_poll(timeout_ms)

        writer._poll_and_drain = _poll_with_injected_rpcs  # type: ignore[method-assign]  # noqa: SLF001

        def on_loop(fn: Callable[[], Any]) -> Any:
            out: list[Any] = []
            done = threading.Event()
            calls.append((fn, out, done))
            self.assertTrue(done.wait(timeout=2.0), "main loop is not servicing RPCs")
            return out[0]

        def rpc(action: str) -> dict[str, Any]:
            return on_loop(
                lambda: writer._handle_rpc(  # noqa: SLF001
                    {"request_id": uuid.uuid4().hex, "type": action, "params": {}}
                )
            )

        seen: list[str] = []
        log_handler = writer._topic_handlers["manager.log"]  # noqa: SLF001

        def _spy(msg: dict[str, Any]) -> None:
            seen.append(str(msg.get("message")))
            log_handler(msg)

        writer._topic_handlers["manager.log"] = _spy  # noqa: SLF001

        gate = threading.Event()
        op_entered = threading.Event()
        original_finalize = writer._finalize_strict_streams_locked  # noqa: SLF001

        def _gated_finalize() -> None:
            if writer._file_op is not None:  # noqa: SLF001
                op_entered.set()
                gate.wait(timeout=10.0)
            original_finalize()

        writer._finalize_strict_streams_locked = _gated_finalize  # type: ignore[method-assign]  # noqa: SLF001

        def publish(message: str) -> None:
            payload = {
                "severity": "error",
                "message": message,
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            }
            pub.send_multipart([b"manager.log", json.dumps(payload).encode("utf-8")])

        thread = threading.Thread(target=writer.run, daemon=True)
        thread.start()
        try:
            # PUB/SUB slow joiner: publish until the writer sees one.
            def _joined() -> bool:
                publish("before-stop")
                time.sleep(0.02)
                return "before-stop" in seen

            self.assertTrue(_wait_for(_joined))
            start = rpc("hdf.writing.start")
            self.assertTrue(start["ok"], start)
            file_path = start["result"]["new_file"]
            seen.clear()
            self.assertTrue(_wait_for(_joined))

            stop = rpc("hdf.writing.stop")
            self.assertTrue(stop["result"]["accepted"], stop)
            self.assertTrue(op_entered.wait(timeout=5.0))

            # Close is blocked on the bg thread; the loop still answers and
            # keeps draining the SUB socket into the hold buffer.
            publish("during-stop")
            self.assertTrue(
                _wait_for(lambda: rpc("hdf.status")["result"]["held_messages"] >= 1)
            )
            status = rpc("hdf.status")["result"]
            self.assertEqual(status["file_state"], "closing")
            self.assertTrue(status["writing_active"])
            self.assertNotIn("during-stop", seen)

            gate.set()
            self.assertTrue(
                _wait_for(lambda: rpc("hdf.status")["result"]["file_state"] == "idle")
            )
            status = rpc("hdf.status")["result"]
            self.assertTrue(status["last_file_op"]["ok"], status["last_file_op"])
            self.assertIn("during-stop", seen)  # replayed after the op
            messages = _event_messages(file_path)
            self.assertIn("before-stop", messages)
            self.assertNotIn("during-stop", messages)
        finally:
            gate.set()
            writer._stop_evt.set()  # noqa: SLF001
            thread.join(timeout=10.0)
            pub.close(0)
            ctx.term()
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
