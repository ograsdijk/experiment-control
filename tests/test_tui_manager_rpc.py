from __future__ import annotations

import queue
import threading
import time
import unittest

from experiment_control._tui.app import ManagerTUI
from experiment_control.utils.zmq_helpers import json_dumps


class _BaseFakeSocket:
    def close(self, linger=0) -> None:
        pass


class _StaleThenReplySocket(_BaseFakeSocket):
    def __init__(self) -> None:
        self._pre_send = [json_dumps({"request_id": "stale", "ok": True})]
        self._post_send = [
            json_dumps(
                {"request_id": "tui-1", "ok": True, "result": {"status": "ready"}}
            )
        ]
        self.sent: list[bytes] = []
        self._sent_once = False

    def poll(self, timeout, flags=None) -> int:
        if not self._sent_once:
            return 1 if self._pre_send else 0
        return 1 if self._post_send else 0

    def recv(self, flags=None) -> bytes:
        if not self._sent_once:
            if not self._pre_send:
                raise RuntimeError("unexpected pre-send recv")
            return self._pre_send.pop(0)
        if not self._post_send:
            raise RuntimeError("unexpected post-send recv")
        return self._post_send.pop(0)

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)
        self._sent_once = True


class _TimeoutSocket(_BaseFakeSocket):
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[bytes] = []

    def poll(self, timeout, flags=None) -> int:
        return 0

    def recv(self, flags=None) -> bytes:
        raise RuntimeError("recv should not be called on timeout socket")

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def close(self, linger=0) -> None:
        self.closed = True


class _ReplacementSocket(_BaseFakeSocket):
    def __init__(self) -> None:
        self.closed = False

    def close(self, linger=0) -> None:
        self.closed = True


class ManagerTuiRpcWorkerTests(unittest.TestCase):
    """The single-owner RPC worker owns the socket; UI-thread code submits
    work (fire-and-forget or blocking) and never touches the socket."""

    def _make_worker_app(self) -> ManagerTUI:
        app = object.__new__(ManagerTUI)
        app._rpc_req_q = queue.Queue()
        app._rpc_worker_ident = None
        # call_from_thread runs the callback inline for the test.
        app.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)  # type: ignore[method-assign]
        return app

    def _start_worker(self, app: ManagerTUI) -> threading.Thread:
        t = threading.Thread(target=app._rpc_worker_loop, daemon=True)
        t.start()
        return t

    def test_rpc_submit_delivers_result_to_callback(self) -> None:
        app = self._make_worker_app()
        app._do_rpc = lambda payload: {"ok": True, "echo": payload.get("type")}  # type: ignore[method-assign]
        t = self._start_worker(app)
        try:
            got: list = []
            app._rpc_submit({"type": "device.list_status"}, got.append)
            deadline = 2.0
            start = time.monotonic()
            while not got and (time.monotonic() - start) < deadline:
                time.sleep(0.01)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0], {"ok": True, "echo": "device.list_status"})
        finally:
            app._rpc_req_q.put(None)
            t.join(timeout=1.0)

    def test_rpc_call_blocks_for_result_via_worker(self) -> None:
        app = self._make_worker_app()
        app._do_rpc = lambda payload: {"ok": True}  # type: ignore[method-assign]
        app._rpc_timeout_ms = 500
        t = self._start_worker(app)
        try:
            resp = app._rpc_call({"type": "manager.info.identity"})
            self.assertEqual(resp, {"ok": True})
        finally:
            app._rpc_req_q.put(None)
            t.join(timeout=1.0)

    def test_reset_dispatches_to_worker_when_off_thread(self) -> None:
        app = self._make_worker_app()
        reset_calls: list[int] = []
        app._do_reset_rpc_socket = lambda: reset_calls.append(1)  # type: ignore[method-assign]
        t = self._start_worker(app)
        try:
            app._reset_rpc_socket()
            # Follow with a blocking call to ensure the reset job ran first.
            app._do_rpc = lambda payload: {"ok": True}  # type: ignore[method-assign]
            app._rpc_timeout_ms = 500
            app._rpc_call({"type": "x"})
            self.assertEqual(reset_calls, [1])
        finally:
            app._rpc_req_q.put(None)
            t.join(timeout=1.0)


class ManagerTuiRpcTests(unittest.TestCase):
    def _build_app(self, *, rpc_timeout_ms: int = 50) -> ManagerTUI:
        app = ManagerTUI(rpc_timeout_ms=rpc_timeout_ms)
        return app

    def test_apply_snapshot_accepts_null_driver_restart_count(self) -> None:
        # _refresh_snapshot now runs the two status RPCs on the RPC worker and
        # applies the results via _apply_snapshot on the UI thread; drive the
        # apply step directly with the responses the worker would deliver.
        app = object.__new__(ManagerTUI)
        app._device_status = {}
        app._heartbeat_cache = {}
        app._telemetry_cache = {}
        app._cap_cache = {}
        app._cap_cache_mono = {}
        app._members_last = {}
        app._members_rendered_fingerprint = {}
        app._process_status_map = {}
        app._processes = []
        app._proc_cap_cache = {}
        app._proc_members_last = {}
        app._proc_cap_retry_next_mono = {}
        app._proc_cap_retry_delay_s = {}
        app._proc_cap_render_attempt_mono = {}
        app._dev_cap_render_attempt_mono = {}

        app._render_devices_table = lambda: None  # type: ignore[method-assign]
        app._render_processes_table = lambda: None  # type: ignore[method-assign]
        app._mark_inspector_dirty = lambda: None  # type: ignore[method-assign]
        app._render_inspector_if_needed = lambda force=False: None  # type: ignore[method-assign]

        resp = {
            "ok": True,
            "result": [
                {
                    "device_id": "hornet_eql",
                    "registered": True,
                    "driver_process": {
                        "state": "FEDERATED",
                        "pid": None,
                        "restart_count": None,
                    },
                    "source_kind": "federated",
                }
            ],
        }
        proc_resp = {"ok": True, "result": []}

        app._apply_snapshot(resp, proc_resp)

        self.assertEqual(app._device_status["hornet_eql"].driver_restart_count, 0)
        self.assertTrue(app._device_status["hornet_eql"].is_remote)

    def test_do_rpc_drops_stale_reply_and_returns_current_response(self) -> None:
        # The blocking round-trip now lives in _do_rpc (run on the RPC worker);
        # _rpc_call/_rpc_submit are thin queue wrappers around it.
        app = self._build_app()
        try:
            app._rpc.close(0)
            fake = _StaleThenReplySocket()
            app._rpc = fake

            resp = app._do_rpc({"type": "manager.info.identity"})

            self.assertIsInstance(resp, dict)
            assert resp is not None
            self.assertTrue(resp.get("ok"))
            self.assertEqual(len(fake.sent), 1)
        finally:
            try:
                if app._sub is not None:
                    app._sub.close(0)
            except Exception:
                pass
            try:
                app._rpc.close(0)
            except Exception:
                pass

    def test_do_rpc_resets_socket_after_timeout(self) -> None:
        app = self._build_app(rpc_timeout_ms=1)
        try:
            app._rpc.close(0)
            timeout_sock = _TimeoutSocket()
            replacement = _ReplacementSocket()
            app._rpc = timeout_sock
            app._new_rpc_socket = lambda: replacement  # type: ignore[method-assign]

            resp = app._do_rpc({"type": "manager.info.identity"})

            self.assertIsNone(resp)
            self.assertTrue(timeout_sock.closed)
            self.assertIs(app._rpc, replacement)
        finally:
            try:
                if app._sub is not None:
                    app._sub.close(0)
            except Exception:
                pass
            try:
                app._rpc.close(0)
            except Exception:
                pass

    def test_reconnect_backend_replaces_sockets_and_refreshes(self) -> None:
        # Reconnect is now non-blocking: the socket reset is dispatched onto the
        # RPC worker and the identity probe is submitted; the result is applied
        # on the UI thread. Stub the reset + submit to drive the flow inline.
        app = self._build_app()
        try:
            old_rpc = _ReplacementSocket()
            new_rpc = _ReplacementSocket()
            calls: list[str] = []
            sub_reconnect_requested: list[bool] = []

            app._rpc.close(0)
            if app._sub is not None:
                app._sub.close(0)
            app._rpc = old_rpc
            app._sub = _ReplacementSocket()

            def fake_reset() -> None:
                old_rpc.close(0)
                app._rpc = new_rpc

            app._reset_rpc_socket = fake_reset  # type: ignore[method-assign]
            app._request_sub_reconnect = lambda: sub_reconnect_requested.append(True)  # type: ignore[method-assign]

            def fake_submit(payload, on_result=None):
                t = str(payload.get("type"))
                calls.append(t)
                if t == "manager.info.identity" and on_result is not None:
                    on_result({"ok": True})

            app._rpc_submit = fake_submit  # type: ignore[method-assign]
            app._refresh_snapshot = lambda: calls.append("refresh")  # type: ignore[method-assign]
            app._log_action_result = lambda message: calls.append(message)  # type: ignore[method-assign]
            app.notify = lambda *a, **k: None  # type: ignore[method-assign]

            app._reconnect_backend()

            self.assertTrue(old_rpc.closed)
            self.assertIs(app._rpc, new_rpc)
            self.assertEqual(sub_reconnect_requested, [True])
            self.assertEqual(
                calls,
                [
                    "Reconnecting backend...",
                    "manager.info.identity",
                    "manager.logs.tail",
                    "refresh",
                    "Backend reconnected",
                ],
            )
            self.assertEqual(app._backend_status_text, "Backend: connected")
        finally:
            try:
                app._rpc.close(0)
            except Exception:
                pass
            try:
                if app._sub is not None:
                    app._sub.close(0)
            except Exception:
                pass

    def test_reconnect_backend_reports_failure_without_refresh(self) -> None:
        app = self._build_app()
        try:
            app._rpc.close(0)
            if app._sub is not None:
                app._sub.close(0)
            app._rpc = _ReplacementSocket()
            app._sub = _ReplacementSocket()
            calls: list[str] = []
            sub_reconnect_requested: list[bool] = []

            app._reset_rpc_socket = lambda: None  # type: ignore[method-assign]
            app._request_sub_reconnect = lambda: sub_reconnect_requested.append(True)  # type: ignore[method-assign]

            def fake_submit(payload, on_result=None):
                t = str(payload.get("type"))
                calls.append(t)
                if t == "manager.info.identity" and on_result is not None:
                    on_result(None)

            app._rpc_submit = fake_submit  # type: ignore[method-assign]
            app._refresh_snapshot = lambda: calls.append("refresh")  # type: ignore[method-assign]
            app._log_action_result = lambda message: calls.append(message)  # type: ignore[method-assign]
            app.notify = lambda *a, **k: None  # type: ignore[method-assign]

            app._reconnect_backend()

            self.assertEqual(sub_reconnect_requested, [True])
            self.assertEqual(
                calls,
                [
                    "Reconnecting backend...",
                    "manager.info.identity",
                    "Backend reconnect failed",
                ],
            )
            self.assertEqual(app._backend_status_text, "Backend: unavailable")
        finally:
            try:
                app._rpc.close(0)
            except Exception:
                pass
            try:
                if app._sub is not None:
                    app._sub.close(0)
            except Exception:
                pass

    def test_default_topic_visibility_hides_high_rate_topics(self) -> None:
        app = self._build_app()
        try:
            self.assertFalse(app._default_topic_visibility("manager.telemetry_update"))
            self.assertFalse(app._default_topic_visibility("manager.heartbeat"))
            self.assertFalse(app._default_topic_visibility("manager.chunk_ready"))
            self.assertFalse(
                app._default_topic_visibility("manager.process_telemetry_update")
            )
            self.assertFalse(app._default_topic_visibility("manager.process.heartbeat"))
            # Edge-triggered / low-volume topics stay visible by default.
            self.assertTrue(app._default_topic_visibility("manager.log"))
            self.assertTrue(app._default_topic_visibility("manager.liveness"))
        finally:
            try:
                if app._sub is not None:
                    app._sub.close(0)
            except Exception:
                pass
            try:
                app._rpc.close(0)
            except Exception:
                pass

    def test_topic_enabled_for_event_log_applies_manager_log_min_severity(self) -> None:
        app = self._build_app()
        try:
            app._topic_visible["manager.log"] = True
            self.assertFalse(
                app._topic_enabled_for_event_log(
                    "manager.log", {"severity": "info", "message": "hello"}
                )
            )
            self.assertTrue(
                app._topic_enabled_for_event_log(
                    "manager.log", {"severity": "warning", "message": "warn"}
                )
            )
        finally:
            try:
                if app._sub is not None:
                    app._sub.close(0)
            except Exception:
                pass
            try:
                app._rpc.close(0)
            except Exception:
                pass

    def test_enqueue_pub_message_drop_newest_discards_incoming(self) -> None:
        app = self._build_app()
        try:
            app._pub_queue = queue.Queue(maxsize=1)
            app._pub_queue_overflow_policy = "drop_newest"
            app._enqueue_pub_message("topic.one", {"value": 1})
            app._enqueue_pub_message("topic.two", {"value": 2})

            self.assertEqual(app._dropped_pub_messages, 1)
            queued_topic, _queued_payload = app._pub_queue.get_nowait()
            self.assertEqual(queued_topic, "topic.one")
        finally:
            try:
                if app._sub is not None:
                    app._sub.close(0)
            except Exception:
                pass
            try:
                app._rpc.close(0)
            except Exception:
                pass

    def test_enqueue_pub_message_drop_oldest_replaces_oldest(self) -> None:
        app = self._build_app()
        try:
            app._pub_queue = queue.Queue(maxsize=1)
            app._pub_queue_overflow_policy = "drop_oldest"
            app._enqueue_pub_message("topic.one", {"value": 1})
            app._enqueue_pub_message("topic.two", {"value": 2})

            self.assertEqual(app._dropped_pub_messages, 1)
            queued_topic, _queued_payload = app._pub_queue.get_nowait()
            self.assertEqual(queued_topic, "topic.two")
        finally:
            try:
                if app._sub is not None:
                    app._sub.close(0)
            except Exception:
                pass
            try:
                app._rpc.close(0)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()

