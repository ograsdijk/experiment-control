import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.process_base import ManagedProcessBase
from experiment_control.processes.state_machine_base import StateMachineProcessBase
from experiment_control.processes.device_router import DeviceRouter
from experiment_control.capabilities import method
from experiment_control.types import MemberSpec


class _DummyStateMachine(StateMachineProcessBase):
    def __init__(self) -> None:
        super().__init__(
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            process_id="dummy_state_machine",
            rpc_namespace="dummy",
            rpc_timeout_ms=50,
            heartbeat_endpoint=None,
            heartbeat_period_s=1.0,
            tick_s=0.1,
            initial_state="IDLE",
            allowed_transitions={
                "IDLE": {"RUNNING"},
                "RUNNING": {"STOPPED"},
                "STOPPED": {"IDLE"},
            },
            subscribe_telemetry=False,
        )
        self.entered: list[str] = []
        self.exited: list[str] = []

    def _tick_state(self, now_mono: float) -> None:
        return

    def _on_exit_idle(
        self,
        *,
        to_state: str,
        reason: str | None,
        metadata: dict | None,
    ) -> None:
        self.exited.append(f"IDLE->{to_state}:{reason}")

    def _on_enter_running(
        self,
        *,
        from_state: str,
        reason: str | None,
        metadata: dict | None,
    ) -> None:
        self.entered.append(f"{from_state}->RUNNING:{reason}")

    def _handle_rpc(self, req: dict) -> dict:
        base = self._handle_state_machine_rpc(req)
        if base is not None:
            return base
        return self._rpc_unknown(req)


class ManagedProcessBaseRpcHelperTests(unittest.TestCase):
    def test_rpc_ok_shape(self) -> None:
        req = {"request_id": "abc"}
        out = ManagedProcessBase._rpc_ok(req, result={"k": 1})
        self.assertEqual(out["request_id"], "abc")
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"], {"k": 1})

    def test_rpc_err_shape(self) -> None:
        req = {"request_id": 7}
        out = ManagedProcessBase._rpc_err(
            req,
            code="bad_thing",
            message="bad",
            extra={"detail": 42},
        )
        self.assertEqual(out["request_id"], 7)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "bad_thing")
        self.assertEqual(out["error"]["message"], "bad")
        self.assertEqual(out["error"]["detail"], 42)

    def test_with_common_capabilities_uses_memberspec_and_deduplicates(self) -> None:
        class _DummyProcess(ManagedProcessBase):
            def __init__(self) -> None:
                super().__init__(process_id=None, heartbeat_endpoint=None)

        proc = _DummyProcess()
        try:
            members = [
                method("dummy.status", params=None, doc="status"),
                method("process.stop", params=None, doc="already present"),
            ]
            merged = proc._with_common_capabilities(members)
            self.assertTrue(all(isinstance(item, MemberSpec) for item in merged))
            names = [item.name for item in merged]
            self.assertEqual(names.count("dummy.status"), 1)
            self.assertEqual(names.count("process.stop"), 1)
        finally:
            proc.close()

    def test_with_common_capabilities_rejects_dict_members(self) -> None:
        class _DummyProcess(ManagedProcessBase):
            def __init__(self) -> None:
                super().__init__(process_id=None, heartbeat_endpoint=None)

        proc = _DummyProcess()
        try:
            with self.assertRaises(TypeError):
                proc._with_common_capabilities([{"name": "legacy.dict"}])  # type: ignore[list-item]
        finally:
            proc.close()


class StateMachineBaseTests(unittest.TestCase):
    def test_transition_and_hooks(self) -> None:
        proc = _DummyStateMachine()
        try:
            self.assertEqual(proc.state, "IDLE")
            ok = proc.transition("RUNNING", reason="test")
            self.assertTrue(ok)
            self.assertEqual(proc.state, "RUNNING")
            self.assertEqual(proc.exited, ["IDLE->RUNNING:test"])
            self.assertEqual(proc.entered, ["IDLE->RUNNING:test"])
            self.assertIn("STOPPED", proc.allowed_next_states())
        finally:
            proc.close()

    def test_invalid_transition(self) -> None:
        proc = _DummyStateMachine()
        try:
            self.assertFalse(proc.transition("STOPPED", reason="invalid"))
            self.assertEqual(proc.state, "IDLE")
        finally:
            proc.close()

    def test_base_rpc_transition(self) -> None:
        proc = _DummyStateMachine()
        try:
            req = {
                "request_id": "r1",
                "type": "dummy.transition",
                "params": {"target_state": "RUNNING", "reason": "rpc"},
            }
            resp = proc._handle_rpc(req)
            self.assertTrue(resp.get("ok"))
            self.assertEqual(resp["result"]["state"], "RUNNING")
        finally:
            proc.close()


class DeviceRouterCapabilityTests(unittest.TestCase):
    def test_process_capabilities_payload_shape(self) -> None:
        router = DeviceRouter(
            external_rpc_bind="tcp://127.0.0.1:*",
            process_id="device_router",
            heartbeat_endpoint=None,
        )
        try:
            req = {"request_id": "caps1", "type": "process.capabilities", "params": {}}
            resp = router._handle_rpc(req)
            self.assertTrue(resp.get("ok"))
            result = resp.get("result")
            self.assertIsInstance(result, dict)
            assert isinstance(result, dict)
            self.assertEqual(result.get("version"), 1)
            members = result.get("members")
            self.assertIsInstance(members, list)
            assert isinstance(members, list)
            names = [m.get("name") for m in members if isinstance(m, dict)]
            self.assertIn("router.stats", names)
            self.assertIn("process.stop", names)
        finally:
            router.close()


if __name__ == "__main__":
    unittest.main()
