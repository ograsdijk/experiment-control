from __future__ import annotations

import unittest

from experiment_control.tui_manager import ManagerTUI
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

    def test_rpc_call_drops_stale_reply_and_returns_current_response(self) -> None:
        app = self._build_app()
        try:
            app._rpc.close(0)
            fake = _StaleThenReplySocket()
            app._rpc = fake

            resp = app._rpc_call({"type": "manager.identity"})

            self.assertIsInstance(resp, dict)
            assert resp is not None
            self.assertTrue(resp.get("ok"))
            self.assertEqual(len(fake.sent), 1)
        finally:
            app._sub.close(0)
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

            resp = app._rpc_call({"type": "manager.identity"})

            self.assertIsNone(resp)
            self.assertTrue(timeout_sock.closed)
            self.assertIs(app._rpc, replacement)
        finally:
            app._sub.close(0)
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
                    "manager.identity",
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
                app._sub.close(0)
            except Exception:
                pass

    def test_reconnect_backend_reports_failure_without_refresh(self) -> None:
        app = self._build_app()
        try:
            app._rpc.close(0)
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
                app._sub.close(0)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
