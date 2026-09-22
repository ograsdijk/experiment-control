# ruff: noqa: E402, SLF001

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import parse_sequence
from experiment_control.sequencer.runtime import SequencerRuntime
from experiment_control.sequencer.sequencer import SequencerProcess


class _FakeManager:
    """Mocks the manager RPCs `_rpc_sequencer_start`'s new precondition
    checks reach through `_require_manager()`/`_call_process()`:
    `manager.processes.rpc` -> hdf_writer's `hdf.status`, and
    `device.list_status`."""

    def __init__(
        self,
        *,
        hdf_status: dict | None = None,
        device_liveness: dict[str, str] | None = None,
    ) -> None:
        self._hdf_status = hdf_status
        self._device_liveness = device_liveness or {}

    def call(self, payload, *, timeout_ms=None):  # noqa: ANN001, ARG002
        if not isinstance(payload, dict):
            return {"ok": False, "error": "bad_payload"}
        if (
            payload.get("type") == "manager.processes.rpc"
            and payload.get("process_id") == "hdf_writer"
            and isinstance(payload.get("request"), dict)
            and payload["request"].get("type") == "hdf.status"
        ):
            if self._hdf_status is None:
                return {"ok": False, "error": "hdf_writer unreachable"}
            return {"ok": True, "result": self._hdf_status}
        if payload.get("type") == "device.list_status":
            result = [
                {"device_id": device_id, "liveness": liveness}
                for device_id, liveness in self._device_liveness.items()
            ]
            return {"ok": True, "result": result}
        return {"ok": False, "error": "unsupported"}


def _build_process(
    *,
    spec,
    manager: _FakeManager,
    call_device=None,
) -> SequencerProcess:
    process = object.__new__(SequencerProcess)
    runtime = SequencerRuntime(
        call_device=call_device or (lambda *a, **k: {"ok": True, "result": None}),
        call_process=lambda *a, **k: {"ok": True, "result": None},
        get_telemetry=lambda *a, **k: None,
        set_stream_context=lambda *a, **k: None,
    )
    runtime.load(spec)
    process._runtime = runtime  # type: ignore[attr-defined]
    process._loaded_sequence_spec = spec  # type: ignore[attr-defined]
    process._manager = manager  # type: ignore[attr-defined]
    process._sequence_library = None  # type: ignore[attr-defined]
    process._last_progress_event_signature = None  # type: ignore[attr-defined]
    process._last_progress_event_mono = 0.0  # type: ignore[attr-defined]
    process._active_sequence_id = None  # type: ignore[attr-defined]
    process._loaded_sequence_source = "test.yaml"  # type: ignore[attr-defined]
    process._context_columns = spec.context_columns  # type: ignore[attr-defined]
    process._publish_lifecycle_event = lambda **kwargs: None  # type: ignore[attr-defined]
    return process


class HdfWritingPreconditionTests(unittest.TestCase):
    def test_start_succeeds_when_writing_active(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "requires": {"hdf_writing": True},
                "steps": [
                    {"call": {"device": "dev", "action": "do_thing", "params": {}}}
                ],
            }
        )
        manager = _FakeManager(
            hdf_status={"writing_active": True}, device_liveness={"dev": "ONLINE"}
        )
        process = _build_process(spec=spec, manager=manager)

        resp = process._rpc_sequencer_start({"params": {}})

        self.assertTrue(resp["ok"])
        self.assertEqual(process._runtime.state, "RUNNING")

    def test_start_fails_when_not_writing(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "requires": {
                    "hdf_writing": True,
                    "watchdog_ids": ["laser_a_watchdog"],
                },
                "steps": [
                    {"call": {"device": "dev", "action": "do_thing", "params": {}}}
                ],
            }
        )
        manager = _FakeManager(hdf_status={"writing_active": False})
        watchdog_calls: list[tuple[str, str, dict]] = []

        def call_process(pid, action, params):
            watchdog_calls.append((pid, action, dict(params)))
            return {"ok": True, "result": None}

        process = _build_process(spec=spec, manager=manager)
        # `requires.watchdog_ids` wraps the body in enable/body/disable
        # (`_apply_watchdog_gate`); reroute the runtime's own call_process
        # so a bug that let ticking proceed after the failed precondition
        # would show up here as a watchdog.enable call.
        process._runtime._call_process = call_process  # type: ignore[attr-defined]

        resp = process._rpc_sequencer_start({"params": {}})

        self.assertFalse(resp["ok"])
        self.assertEqual(resp.get("error", {}).get("code"), "start_failed")
        self.assertEqual(process._runtime.state, "ERROR")
        self.assertEqual(watchdog_calls, [])

    def test_start_fails_when_hdf_status_missing_field(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "requires": {"hdf_writing": True},
                "steps": [],
            }
        )
        manager = _FakeManager(hdf_status={})
        process = _build_process(spec=spec, manager=manager)

        resp = process._rpc_sequencer_start({"params": {}})

        self.assertFalse(resp["ok"])
        self.assertEqual(process._runtime.state, "ERROR")

    def test_start_succeeds_without_hdf_writing_requirement(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {"call": {"device": "dev", "action": "do_thing", "params": {}}}
                ],
            }
        )
        # No hdf_status configured -- the fake manager would fail an
        # hdf.status call, proving it is never made when not required.
        manager = _FakeManager(hdf_status=None, device_liveness={"dev": "ONLINE"})
        process = _build_process(spec=spec, manager=manager)

        resp = process._rpc_sequencer_start({"params": {}})

        self.assertTrue(resp["ok"])
        self.assertEqual(process._runtime.state, "RUNNING")


class DeviceConnectivityPreconditionTests(unittest.TestCase):
    def test_start_succeeds_when_all_devices_online(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {"call": {"device": "dev_a", "action": "do_thing", "params": {}}},
                    {"set": {"device": "dev_b", "name": "x", "value": 1}},
                ],
            }
        )
        manager = _FakeManager(
            device_liveness={"dev_a": "ONLINE", "dev_b": "ONLINE"}
        )
        process = _build_process(spec=spec, manager=manager)

        resp = process._rpc_sequencer_start({"params": {}})

        self.assertTrue(resp["ok"])
        self.assertEqual(process._runtime.state, "RUNNING")

    def test_start_fails_when_device_offline(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {"call": {"device": "dev_a", "action": "do_thing", "params": {}}},
                    {"set": {"device": "dev_b", "name": "x", "value": 1}},
                ],
            }
        )
        manager = _FakeManager(
            device_liveness={"dev_a": "ONLINE", "dev_b": "OFFLINE"}
        )
        process = _build_process(spec=spec, manager=manager)

        resp = process._rpc_sequencer_start({"params": {}})

        self.assertFalse(resp["ok"])
        self.assertEqual(resp.get("error", {}).get("code"), "start_failed")
        self.assertIn("dev_b", resp.get("error", {}).get("message", ""))
        self.assertEqual(process._runtime.state, "ERROR")

    def test_start_fails_when_device_disconnected(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {"call": {"device": "dev_a", "action": "do_thing", "params": {}}},
                ],
            }
        )
        manager = _FakeManager(device_liveness={"dev_a": "DISCONNECTED"})
        process = _build_process(spec=spec, manager=manager)

        resp = process._rpc_sequencer_start({"params": {}})

        self.assertFalse(resp["ok"])
        self.assertIn("dev_a", resp.get("error", {}).get("message", ""))
        self.assertEqual(process._runtime.state, "ERROR")

    def test_templated_device_field_resolved_against_vars_override(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "vars": {"synth_device": "default_dev"},
                "steps": [
                    {
                        "call": {
                            "device": "${synth_device}",
                            "action": "do_thing",
                            "params": {},
                        }
                    }
                ],
            }
        )
        manager = _FakeManager(device_liveness={"laser_synth": "ONLINE"})
        process = _build_process(spec=spec, manager=manager)

        resp = process._rpc_sequencer_start(
            {"params": {"vars_override": {"synth_device": "laser_synth"}}}
        )

        self.assertTrue(resp["ok"])
        self.assertEqual(process._runtime.state, "RUNNING")

    def test_templated_device_field_missing_is_flagged(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "vars": {"synth_device": "default_dev"},
                "steps": [
                    {
                        "call": {
                            "device": "${synth_device}",
                            "action": "do_thing",
                            "params": {},
                        }
                    }
                ],
            }
        )
        manager = _FakeManager(device_liveness={"laser_synth": "STALE"})
        process = _build_process(spec=spec, manager=manager)

        resp = process._rpc_sequencer_start(
            {"params": {"vars_override": {"synth_device": "laser_synth"}}}
        )

        self.assertFalse(resp["ok"])
        self.assertIn("laser_synth", resp.get("error", {}).get("message", ""))
        self.assertEqual(process._runtime.state, "ERROR")


if __name__ == "__main__":
    unittest.main()
