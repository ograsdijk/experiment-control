# ruff: noqa: E402

import sys
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.client.apis.device import DeviceAPI, DeviceHandle
from experiment_control.client.apis.hdf import HdfAPI
from experiment_control.client.apis.manager import ManagerAPI
from experiment_control.client.apis.process import ProcessAPI, ProcessHandle
from experiment_control.client.apis.sequencer import SequencerAPI
from experiment_control.client.errors import RpcResponseError


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


class _ScriptedHdfClient:
    """Replies to hdf RPCs from per-action scripts (last entry repeats)."""

    def __init__(self, replies: dict[str, list[Any]]) -> None:
        self.replies = replies
        self.actions: list[str] = []

    def rpc(self, payload: dict[str, Any], **_kwargs: Any) -> Any:
        action = payload["request"]["type"]
        self.actions.append(action)
        script = self.replies[action]
        return script.pop(0) if len(script) > 1 else script[0]


class HdfApiWaitTests(unittest.TestCase):
    def test_writing_stop_without_wait_returns_accepted_reply(self) -> None:
        client = _ScriptedHdfClient({"hdf.writing.stop": [{"accepted": True}]})
        api = HdfAPI(client)  # type: ignore[arg-type]
        self.assertEqual(api.writing_stop(), {"accepted": True})
        self.assertEqual(client.actions, ["hdf.writing.stop"])

    def test_writing_stop_wait_polls_until_file_op_done(self) -> None:
        outcome = {"op": "stop", "ok": True, "file": "a.h5", "duration_s": 3.0}
        client = _ScriptedHdfClient(
            {
                "hdf.writing.stop": [{"accepted": True, "old_file": "a.h5"}],
                "hdf.status": [
                    {"file_state": "closing", "file_op": {"op": "stop"}},
                    {"file_state": "idle", "file_op": None, "last_file_op": outcome},
                ],
            }
        )
        api = HdfAPI(client)  # type: ignore[arg-type]
        with unittest.mock.patch("time.sleep"):
            result = api.writing_stop(wait=True)
        self.assertEqual(result["file_op"], outcome)
        self.assertEqual(result["old_file"], "a.h5")
        self.assertEqual(
            client.actions, ["hdf.writing.stop", "hdf.status", "hdf.status"]
        )

    def test_rotate_wait_raises_when_op_failed(self) -> None:
        client = _ScriptedHdfClient(
            {
                "hdf.rotate": [{"accepted": True}],
                "hdf.status": [
                    {
                        "file_op": None,
                        "last_file_op": {
                            "op": "rotate",
                            "ok": False,
                            "error": {"code": "rotate_failed", "message": "boom"},
                        },
                    }
                ],
            }
        )
        api = HdfAPI(client)  # type: ignore[arg-type]
        with self.assertRaises(RpcResponseError) as ctx:
            api.rotate(filename="b.h5", wait=True)
        self.assertEqual(ctx.exception.code, "rotate_failed")

    def test_wait_times_out(self) -> None:
        client = _ScriptedHdfClient(
            {"hdf.status": [{"file_state": "closing", "file_op": {"op": "stop"}}]}
        )
        api = HdfAPI(client)  # type: ignore[arg-type]
        with self.assertRaises(TimeoutError):
            api.wait_for_file_op(timeout_s=0.0)

    def test_synchronous_reply_skips_wait(self) -> None:
        # Older writers (or no bg thread) finish inside the RPC: no `accepted`.
        client = _ScriptedHdfClient({"hdf.writing.stop": [{"old_file": "a.h5"}]})
        api = HdfAPI(client)  # type: ignore[arg-type]
        self.assertEqual(api.writing_stop(wait=True), {"old_file": "a.h5"})
        self.assertEqual(client.actions, ["hdf.writing.stop"])


if __name__ == "__main__":
    unittest.main()
