from __future__ import annotations

import queue
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


class ManagerTuiRpcTests(unittest.TestCase):
    def _build_app(self, *, rpc_timeout_ms: int = 50) -> ManagerTUI:
        app = ManagerTUI(rpc_timeout_ms=rpc_timeout_ms)
        return app

    def test_refresh_snapshot_accepts_null_driver_restart_count(self) -> None:
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

        def fake_rpc_call(req):
            if req.get("type") == "device.list_status":
                return {
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
            if req.get("type") == "manager.processes.list":
                return {"ok": True, "result": []}
            return None

        app._rpc_call = fake_rpc_call  # type: ignore[method-assign]
        app._render_devices_table = lambda: None  # type: ignore[method-assign]
        app._render_processes_table = lambda: None  # type: ignore[method-assign]
        app._mark_inspector_dirty = lambda: None  # type: ignore[method-assign]
        app._render_inspector_if_needed = lambda force=False: None  # type: ignore[method-assign]

        app._refresh_snapshot()

        self.assertEqual(app._device_status["hornet_eql"].driver_restart_count, 0)
        self.assertTrue(app._device_status["hornet_eql"].is_remote)

    def test_rpc_call_drops_stale_reply_and_returns_current_response(self) -> None:
        app = self._build_app()
        try:
            app._rpc.close(0)
            fake = _StaleThenReplySocket()
            app._rpc = fake

            resp = app._rpc_call({"type": "manager.info.identity"})

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

    def test_rpc_call_resets_socket_after_timeout(self) -> None:
        app = self._build_app(rpc_timeout_ms=1)
        try:
            app._rpc.close(0)
            timeout_sock = _TimeoutSocket()
            replacement = _ReplacementSocket()
            app._rpc = timeout_sock
            app._new_rpc_socket = lambda: replacement  # type: ignore[method-assign]

            resp = app._rpc_call({"type": "manager.info.identity"})

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
            app._new_rpc_socket = lambda: new_rpc  # type: ignore[method-assign]
            app._request_sub_reconnect = lambda: sub_reconnect_requested.append(True)  # type: ignore[method-assign]

            def fake_rpc_call(payload):
                calls.append(str(payload.get("type")))
                return {"ok": True}

            app._rpc_call = fake_rpc_call  # type: ignore[method-assign]
            app._load_manager_log_tail_bootstrap = lambda: calls.append("log_tail")  # type: ignore[method-assign]
            app._refresh_snapshot = lambda: calls.append("refresh")  # type: ignore[method-assign]
            app._log_action_result = lambda message: calls.append(message)  # type: ignore[method-assign]

            ok = app._reconnect_backend()

            self.assertTrue(ok)
            self.assertTrue(old_rpc.closed)
            self.assertIs(app._rpc, new_rpc)
            self.assertEqual(sub_reconnect_requested, [True])
            self.assertEqual(
                calls,
                [
                    "Reconnecting backend...",
                    "manager.info.identity",
                    "log_tail",
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
            app._new_rpc_socket = lambda: _ReplacementSocket()  # type: ignore[method-assign]
            calls: list[str] = []
            sub_reconnect_requested: list[bool] = []

            app._rpc_call = lambda payload: None  # type: ignore[method-assign]
            app._request_sub_reconnect = lambda: sub_reconnect_requested.append(True)  # type: ignore[method-assign]
            app._load_manager_log_tail_bootstrap = lambda: calls.append("log_tail")  # type: ignore[method-assign]
            app._refresh_snapshot = lambda: calls.append("refresh")  # type: ignore[method-assign]
            app._log_action_result = lambda message: calls.append(message)  # type: ignore[method-assign]

            ok = app._reconnect_backend()

            self.assertFalse(ok)
            self.assertEqual(sub_reconnect_requested, [True])
            self.assertEqual(
                calls,
                [
                    "Reconnecting backend...",
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

