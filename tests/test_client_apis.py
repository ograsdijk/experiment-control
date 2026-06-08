# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.client.apis.device import DeviceAPI, DeviceHandle
from experiment_control.client.apis.manager import ManagerAPI
from experiment_control.client.apis.process import ProcessAPI, ProcessHandle
from experiment_control.client.apis.sequencer import SequencerAPI


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def rpc(
        self,
        payload: dict[str, Any],
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
        expect_ok: bool = True,
    ) -> Any:
        self.calls.append(
            {
                "payload": payload,
                "timeout_ms": timeout_ms,
                "retries": retries,
                "expect_ok": expect_ok,
            }
        )
        return {"payload": payload, "expect_ok": expect_ok}


class ClientApiTests(unittest.TestCase):
    def test_base_call_payload_used_by_manager_api(self) -> None:
        client = _FakeClient()
        api = ManagerAPI(client)  # type: ignore[arg-type]
        api.cleanup_orphans(dry_run=False, timeout_s=3.0, timeout_ms=12, retries=2)
        self.assertEqual(
            client.calls[-1],
            {
                "payload": {
                    "type": "manager.control.cleanup_orphans",
                    "params": {"dry_run": False, "stale_only": True, "timeout_s": 3.0},
                },
                "timeout_ms": 12,
                "retries": 2,
                "expect_ok": True,
            },
        )

    def test_device_api_call_payloads(self) -> None:
        client = _FakeClient()
        api = DeviceAPI(client)  # type: ignore[arg-type]
        api.call("dev", "set", {"x": 1}, timeout_ms=10)
        api.call_raw("dev", "set", {"x": 2}, retries=1)
        self.assertEqual(client.calls[0]["payload"]["type"], "command")
        self.assertEqual(client.calls[0]["payload"]["device_id"], "dev")
        self.assertTrue(client.calls[0]["expect_ok"])
        self.assertFalse(client.calls[1]["expect_ok"])

    def test_process_api_call_payloads(self) -> None:
        client = _FakeClient()
        api = ProcessAPI(client)  # type: ignore[arg-type]
        api.call("proc", "process.capabilities", {}, timeout_ms=10)
        self.assertEqual(
            client.calls[-1]["payload"],
            {
                "type": "manager.processes.rpc",
                "process_id": "proc",
                "request": {"type": "process.capabilities", "params": {}},
            },
        )

    def test_process_backed_facade_payload(self) -> None:
        client = _FakeClient()
        api = SequencerAPI(client)  # type: ignore[arg-type]
        api.status(timeout_ms=10)
        self.assertEqual(client.calls[-1]["payload"]["process_id"], "sequencer")
        self.assertEqual(client.calls[-1]["payload"]["request"]["type"], "sequencer.status")

    def test_handles_keep_public_methods_callable(self) -> None:
        client = _FakeClient()
        device = DeviceHandle(DeviceAPI(client), "dev")  # type: ignore[arg-type]
        process = ProcessHandle(ProcessAPI(client), "proc")  # type: ignore[arg-type]
        device.capabilities(refresh=True)
        device.restart(force=True)
        process.capabilities()
        process.restart()
        self.assertEqual(client.calls[0]["payload"]["action"], "refresh_capabilities")
        self.assertEqual(client.calls[1]["payload"]["force"], True)
        self.assertEqual(client.calls[2]["payload"]["process_id"], "proc")
        self.assertEqual(client.calls[3]["payload"]["type"], "manager.processes.restart")


if __name__ == "__main__":
    unittest.main()
