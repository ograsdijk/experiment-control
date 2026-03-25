# ruff: noqa: E402

import sys
import textwrap
import unittest
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.client import ProcessRpcNotReadyError, StackClient


class PythonClientSdkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        (ROOT / ".tmp_tests").mkdir(exist_ok=True)

    def test_from_stack_yaml_uses_local_connect_endpoints(self) -> None:
        yaml_text = textwrap.dedent(
            """
            version: 1
            instance_id: test
            manager:
              bind_host: 0.0.0.0
              external:
                rpc_port: 7000
                pub_port: 7001
            """
        ).strip()
        path = ROOT / ".tmp_tests" / f"stack_{uuid.uuid4().hex}.yaml"
        try:
            path.write_text(yaml_text, encoding="utf-8")
            client = StackClient.from_stack_yaml(path, auto_open=False)
            self.assertEqual(client.router_rpc, "tcp://127.0.0.1:7000")
            self.assertEqual(client.manager_pub, "tcp://127.0.0.1:7001")
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_device_handle_call_builds_command_payload(self) -> None:
        client = StackClient.from_endpoints(
            router_rpc="tcp://127.0.0.1:6000",
            manager_pub="tcp://127.0.0.1:6001",
            auto_open=False,
        )
        captured: dict[str, Any] = {}

        def fake_request(payload, *, timeout_ms=None, retries=None):  # type: ignore[no-untyped-def]
            captured.update(payload)
            return {"status": "OK", "result": {"done": True}}

        client.transport.request = fake_request  # type: ignore[method-assign]
        result = client.device("freq1").call(
            "set_frequency_hz", {"frequency_hz": 8.0e6}
        )
        self.assertEqual(result, {"done": True})
        self.assertEqual(captured.get("type"), "command")
        self.assertEqual(captured.get("device_id"), "freq1")
        self.assertEqual(captured.get("action"), "set_frequency_hz")
        self.assertEqual(
            captured.get("params"),
            {"frequency_hz": 8.0e6},
        )

    def test_process_handle_call_builds_process_rpc_payload(self) -> None:
        client = StackClient.from_endpoints(
            router_rpc="tcp://127.0.0.1:6000",
            manager_pub="tcp://127.0.0.1:6001",
            auto_open=False,
        )
        captured: dict[str, Any] = {}

        def fake_request(payload, *, timeout_ms=None, retries=None):  # type: ignore[no-untyped-def]
            captured.update(payload)
            return {"ok": True, "result": {"state": "RUNNING"}}

        client.transport.request = fake_request  # type: ignore[method-assign]
        result = client.process("sequencer").call("sequencer.start", {})
        self.assertEqual(result, {"state": "RUNNING"})
        self.assertEqual(captured.get("type"), "manager.processes.rpc")
        self.assertEqual(captured.get("process_id"), "sequencer")
        request = captured.get("request")
        self.assertIsInstance(request, dict)
        assert isinstance(request, dict)
        self.assertEqual(request.get("type"), "sequencer.start")
        self.assertEqual(request.get("params"), {})

    def test_wait_process_rpc_ready_polls_not_ready(self) -> None:
        client = StackClient.from_endpoints(
            router_rpc="tcp://127.0.0.1:6000",
            manager_pub="tcp://127.0.0.1:6001",
            auto_open=False,
        )
        calls = {"count": 0}

        def fake_call(process_id, action, params, timeout_ms=None, retries=None):  # type: ignore[no-untyped-def]
            calls["count"] += 1
            if calls["count"] < 3:
                raise ProcessRpcNotReadyError(
                    code="process_rpc_not_ready",
                    message="not ready",
                    response={"ok": False, "error": {"code": "process_rpc_not_ready"}},
                    request=None,
                )
            return {"ok": True}

        client.processes.call = fake_call  # type: ignore[method-assign]
        ok = client.wait.process_rpc_ready(
            "sequencer",
            probe_action="sequencer.status",
            timeout_s=1.0,
            poll_s=0.001,
        )
        self.assertTrue(ok)
        self.assertEqual(calls["count"], 3)


if __name__ == "__main__":
    unittest.main()
