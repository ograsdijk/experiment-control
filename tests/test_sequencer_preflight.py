# ruff: noqa: E402, SLF001

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.sequencer import SequencerProcess


class _FakeManager:
    def __init__(
        self,
        *,
        devices: set[str],
        telemetry_signals_by_device: dict[str, set[str]] | None = None,
        stream_names_by_device: dict[str, set[str]] | None = None,
        capabilities_by_device: dict[str, dict[str, dict]] | None = None,
        capabilities_fail_devices: set[str] | None = None,
    ) -> None:
        self._devices = set(devices)
        self._telemetry = telemetry_signals_by_device or {}
        self._streams = stream_names_by_device or {}
        self._caps = capabilities_by_device or {}
        self._caps_fail = capabilities_fail_devices or set()

    def call(self, payload, *, timeout_ms=None):  # noqa: ANN001, ARG002
        if not isinstance(payload, dict):
            return {"ok": False, "error": "bad_payload"}
        if payload.get("type") == "manager.devices.list":
            # The real manager includes each device's cached capabilities in the
            # devices.list snapshot; preflight reads them from there.
            devices = []
            for device_id in sorted(self._devices):
                entry: dict = {"device_id": device_id}
                if device_id not in self._caps_fail:
                    members = self._caps.get(device_id)
                    if members is not None:
                        entry["capabilities"] = {
                            "version": 1,
                            "members": [
                                {
                                    "name": name,
                                    "kind": spec.get("kind", "method"),
                                    "readable": bool(spec.get("readable", True)),
                                    "settable": bool(spec.get("settable", False)),
                                    "params": spec.get("params", []),
                                }
                                for name, spec in members.items()
                            ],
                        }
                devices.append(entry)
            return {"ok": True, "devices": devices}
        if payload.get("action") == "manager.telemetry.schema.list":
            devices = []
            for device_id in sorted(self._devices):
                devices.append(
                    {
                        "device_id": device_id,
                        "signals": sorted(self._telemetry.get(device_id, set())),
                        "dtypes": [],
                        "units": [],
                    }
                )
            return {
                "ok": True,
                "result": {"schema_version": 1, "generated_ts": {}, "devices": devices},
            }
        if payload.get("type") == "device.config.list":
            result = []
            for device_id in sorted(self._devices):
                outputs = [
                    {"stream": stream_name}
                    for stream_name in sorted(self._streams.get(device_id, set()))
                ]
                result.append(
                    {
                        "device_id": device_id,
                        "stream_calls": [{"method": "stream", "kwargs": {}, "outputs": outputs}],
                    }
                )
            return {"ok": True, "result": result}
        if payload.get("type") == "command" and payload.get("action") == "capabilities":
            device_id = str(payload.get("device_id", "")).strip()
            if device_id in self._caps_fail:
                return {"ok": False, "error": "timeout"}
            members = self._caps.get(device_id)
            if members is None:
                return {"ok": False, "error": "unknown_device"}
            out = []
            for name, spec in members.items():
                out.append(
                    {
                        "name": name,
                        "kind": spec.get("kind", "method"),
                        "readable": bool(spec.get("readable", True)),
                        "settable": bool(spec.get("settable", False)),
                        "params": spec.get("params", []),
                    }
                )
            return {"ok": True, "result": {"version": 1, "members": out}}
        return {"ok": False, "error": "unsupported"}


def _build_proc(fake_manager: _FakeManager) -> SequencerProcess:
    proc = object.__new__(SequencerProcess)
    proc._manager = fake_manager
    proc._sequence_library_path = None
    proc._sequence_library = None
    return proc


def _codes_from(resp: dict) -> set[str]:
    result = resp.get("result", {}) if isinstance(resp, dict) else {}
    diagnostics = result.get("diagnostics", []) if isinstance(result, dict) else []
    out: set[str] = set()
    if isinstance(diagnostics, list):
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            if isinstance(code, str) and code:
                out.add(code)
    return out


class SequencerPreflightTests(unittest.TestCase):
    def test_preflight_reports_unknown_device(self) -> None:
        mgr = _FakeManager(devices={"laser"})
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - call:
      device: missing
      action: set_frequency_hz
      params: {}
"""
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        self.assertTrue(resp.get("ok"))
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("unknown_device", _codes_from(resp))

    def test_preflight_reports_unknown_action(self) -> None:
        mgr = _FakeManager(
            devices={"laser"},
            capabilities_by_device={
                "laser": {
                    "status": {"kind": "method", "readable": True, "settable": False},
                }
            },
        )
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - call:
      device: laser
      action: set_frequency_hz
      params: {}
"""
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        self.assertTrue(resp.get("ok"))
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("unknown_action", _codes_from(resp))

    def test_preflight_warns_instead_of_erroring_when_capabilities_empty(self) -> None:
        # A device that is registered but not connected reports zero members.
        # Preflight must warn ("could not verify"), not hard-error the action.
        mgr = _FakeManager(devices={"laser"}, capabilities_by_device={"laser": {}})
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - call:
      device: laser
      action: pass_qswitches
      params: {}
"""
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        self.assertTrue(resp.get("ok"))
        codes = _codes_from(resp)
        self.assertIn("capabilities_unavailable", codes)
        self.assertNotIn("unknown_action", codes)
        self.assertTrue(bool(resp.get("result", {}).get("valid")))

    def test_preflight_warns_for_templated_call_target(self) -> None:
        mgr = _FakeManager(devices={"laser"})
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
vars:
  target_device: laser
  target_action: pass_qswitches
steps:
  - call:
      device: ${target_device}
      action: ${target_action}
      params: {}
"""
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        self.assertTrue(resp.get("ok"))
        codes = _codes_from(resp)
        self.assertIn("dynamic_call_ref_unchecked", codes)
        self.assertNotIn("unknown_device", codes)
        self.assertNotIn("unknown_action", codes)
        self.assertTrue(bool(resp.get("result", {}).get("valid")))

    def test_preflight_diagnostics_carry_source_line(self) -> None:
        mgr = _FakeManager(
            devices={"laser"},
            capabilities_by_device={
                "laser": {
                    "status": {"kind": "method", "readable": True, "settable": False},
                }
            },
        )
        proc = _build_proc(mgr)
        # `- call:` is on line 3 (1-based) of this text.
        yaml_text = "version: 1\nsteps:\n  - call:\n      device: laser\n      action: bogus\n      params: {}\n"
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        diagnostics = resp.get("result", {}).get("diagnostics", [])
        unknown = [d for d in diagnostics if d.get("code") == "unknown_action"]
        self.assertTrue(unknown)
        self.assertEqual(unknown[0].get("line"), 3)

    def test_preflight_reports_missing_template_variable(self) -> None:
        mgr = _FakeManager(
            devices={"laser"},
            capabilities_by_device={
                "laser": {
                    "set_frequency_hz": {
                        "kind": "method",
                        "readable": True,
                        "settable": False,
                    },
                }
            },
        )
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - for:
      bind: {value: freq_hz}
      in:
        gen:
          kind: linspace
          start: 1.0
          stop: 2.0
          count: 2
      do:
        - call:
            device: laser
            action: set_frequency_hz
            params:
              frequency_hz: ${freq_hz2}
"""
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        self.assertTrue(resp.get("ok"))
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("template_unresolved", _codes_from(resp))

    def test_preflight_reports_member_not_settable(self) -> None:
        mgr = _FakeManager(
            devices={"laser"},
            capabilities_by_device={
                "laser": {
                    "frequency_hz": {
                        "kind": "property",
                        "readable": True,
                        "settable": False,
                    },
                }
            },
        )
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - set:
      device: laser
      name: frequency_hz
      value: 100.0
"""
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        self.assertTrue(resp.get("ok"))
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("member_not_settable", _codes_from(resp))

    def test_preflight_reports_unknown_stream(self) -> None:
        mgr = _FakeManager(
            devices={"laser"},
            stream_names_by_device={"laser": {"trace"}},
            capabilities_by_device={"laser": {}},
        )
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - set_context:
      streams:
        - laser.bad_stream
      fields:
        mode: test
"""
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        self.assertTrue(resp.get("ok"))
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("unknown_stream", _codes_from(resp))

    def test_preflight_reports_library_not_configured_for_use(self) -> None:
        mgr = _FakeManager(devices={"laser"})
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - use: fragment_a
"""
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        self.assertTrue(resp.get("ok"))
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("library_not_configured", _codes_from(resp))

    def test_preflight_can_succeed_for_known_references(self) -> None:
        mgr = _FakeManager(
            devices={"laser"},
            telemetry_signals_by_device={"laser": {"frequency_hz"}},
            stream_names_by_device={"laser": {"trace"}},
            capabilities_by_device={
                "laser": {
                    "set_frequency_hz": {
                        "kind": "method",
                        "readable": True,
                        "settable": False,
                    },
                    "frequency_hz": {
                        "kind": "property",
                        "readable": True,
                        "settable": True,
                    },
                }
            },
        )
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - call:
      device: laser
      action: set_frequency_hz
      params: {hz: 200.0}
  - set:
      device: laser
      name: frequency_hz
      value: 210.0
  - wait_until:
      timeout_s: 0.1
      sample:
        telemetry:
          device: laser
          signal: frequency_hz
      condition: {gt: ["${sample}", 0.0]}
  - set_context:
      streams:
        - laser.trace
      fields: {kind: test}
"""
        resp = proc._handle_rpc({"type": "sequencer.preflight", "params": {"text": yaml_text}})
        self.assertTrue(resp.get("ok"))
        result = resp.get("result", {})
        self.assertTrue(bool(result.get("valid")))
        summary = result.get("summary", {})
        self.assertEqual(int(summary.get("errors", -1)), 0)

    def test_preflight_rejects_parallel_target_conflict(self) -> None:
        mgr = _FakeManager(devices={"shared"}, capabilities_by_device={"shared": {}})
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - parallel:
      do:
        - call: {device: shared, action: one}
        - set: {device: shared, name: value, value: 1}
"""
        resp = proc._handle_rpc(
            {"type": "sequencer.preflight", "params": {"text": yaml_text}}
        )
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("parallel_target_conflict", _codes_from(resp))

    def test_preflight_rejects_parallel_output_conflict(self) -> None:
        mgr = _FakeManager(
            devices={"a", "b"},
            capabilities_by_device={"a": {}, "b": {}},
        )
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - parallel:
      do:
        - call: {device: a, action: one}
          save_as: result
        - call: {device: b, action: two}
          save_as: result
"""
        resp = proc._handle_rpc(
            {"type": "sequencer.preflight", "params": {"text": yaml_text}}
        )
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("parallel_output_conflict", _codes_from(resp))

    def test_preflight_rejects_unsupported_parallel_branch(self) -> None:
        mgr = _FakeManager(devices={"a"}, capabilities_by_device={"a": {}})
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - parallel:
      do:
        - atomic:
            do:
              - call: {device: a, action: one}
              - sleep: 0.1
"""
        resp = proc._handle_rpc(
            {"type": "sequencer.preflight", "params": {"text": yaml_text}}
        )
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("parallel_unsupported_branch", _codes_from(resp))

    def test_preflight_accepts_distinct_parallel_repeat_branches(self) -> None:
        mgr = _FakeManager(
            devices={"a", "b"}, capabilities_by_device={"a": {}, "b": {}}
        )
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
vars: {n: 3}
steps:
  - parallel:
      do:
        - repeat:
            times: "${n}"
            do:
              - call: {device: a, action: read}
        - repeat:
            times: "${n}"
            do:
              - call: {device: b, action: read}
"""
        response = proc._handle_rpc(
            {"type": "sequencer.preflight", "params": {"text": yaml_text}}
        )
        self.assertTrue(bool(response.get("result", {}).get("valid")))

    def test_preflight_rejects_repeat_target_conflict(self) -> None:
        mgr = _FakeManager(devices={"same"}, capabilities_by_device={"same": {}})
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - parallel:
      do:
        - repeat:
            times: 2
            do:
              - call: {device: same, action: read}
        - call: {device: same, action: other}
"""
        response = proc._handle_rpc(
            {"type": "sequencer.preflight", "params": {"text": yaml_text}}
        )
        self.assertFalse(bool(response.get("result", {}).get("valid")))
        self.assertIn("parallel_target_conflict", _codes_from(response))

    def test_preflight_rejects_parallel_nested_inside_atomic(self) -> None:
        mgr = _FakeManager(
            devices={"a", "b"},
            capabilities_by_device={"a": {}, "b": {}},
        )
        proc = _build_proc(mgr)
        yaml_text = """
version: 1
steps:
  - atomic:
      do:
        - repeat:
            times: 1
            do:
              - parallel:
                  do:
                    - call: {device: a, action: one}
                    - call: {device: b, action: one}
"""
        resp = proc._handle_rpc(
            {"type": "sequencer.preflight", "params": {"text": yaml_text}}
        )
        self.assertFalse(bool(resp.get("result", {}).get("valid")))
        self.assertIn("atomic_parallel_unsupported", _codes_from(resp))


if __name__ == "__main__":
    unittest.main()
