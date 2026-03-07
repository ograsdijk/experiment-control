# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import DeviceHandle, DeviceSpec, Manager


class _HubStub:
    def __init__(self, *, mirrored: set[str] | None = None) -> None:
        self._mirrored = set(mirrored or set())

    def is_mirrored_device(self, device_id: str) -> bool:
        return device_id in self._mirrored


def _build_manager() -> Manager:
    mgr = object.__new__(Manager)
    spec = DeviceSpec(
        device_id="trace1",
        device_class_path="dummy.py",
        device_class_name="DummyDriver",
        device_init_kwargs={},
        telemetry_calls=[],
        stream_calls=[],
        run_meta_calls=[],
        device_metadata={"device_type": "dummy_trace", "location": "bench_a"},
        stream_metadata={"trace": {"gain": 1.0}},
    )
    mgr._devices = {"trace1": DeviceHandle(spec=spec)}  # type: ignore[attr-defined]
    mgr._runtime_device_metadata_overrides = {}  # type: ignore[attr-defined]
    mgr._runtime_stream_metadata_overrides = {}  # type: ignore[attr-defined]
    mgr._runtime_metadata_revision = {}  # type: ignore[attr-defined]
    mgr._federation_hub = _HubStub()  # type: ignore[attr-defined]
    mgr._publish_device_config = mock.Mock()  # type: ignore[attr-defined]
    return mgr  # type: ignore[return-value]


class ManagerRuntimeMetadataTests(unittest.TestCase):
    def test_metadata_get_returns_base_and_effective(self) -> None:
        mgr = _build_manager()
        resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {"type": "device.metadata.get", "device_id": "trace1"},
        )
        self.assertTrue(resp.get("ok"))
        result = resp.get("result", {})
        self.assertEqual(result.get("revision"), 0)
        self.assertEqual(result["base"]["device_metadata"]["location"], "bench_a")
        self.assertEqual(result["overrides"]["device_metadata"], {})
        self.assertEqual(result["effective"]["device_metadata"]["location"], "bench_a")

    def test_metadata_set_merge_updates_effective_and_publishes(self) -> None:
        mgr = _build_manager()
        resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "device.metadata.set",
                "device_id": "trace1",
                "params": {
                    "mode": "merge",
                    "device_metadata": {"location": "bench_b", "operator": "alice"},
                    "stream_metadata": {"trace": {"gain": 2.0}, "aux": {"scale": 10}},
                },
            },
        )
        self.assertTrue(resp.get("ok"))
        result = resp.get("result", {})
        self.assertTrue(result.get("changed"))
        self.assertEqual(result.get("revision"), 1)
        self.assertEqual(
            result["effective"]["device_metadata"]["location"],
            "bench_b",
        )
        self.assertEqual(
            result["effective"]["device_metadata"]["operator"],
            "alice",
        )
        self.assertEqual(result["effective"]["stream_metadata"]["trace"]["gain"], 2.0)
        self.assertEqual(result["effective"]["stream_metadata"]["aux"]["scale"], 10)
        mgr._publish_device_config.assert_called_once()  # type: ignore[attr-defined]

        payload = Manager._device_config_payload(  # type: ignore[arg-type]
            mgr,
            mgr._devices["trace1"],  # type: ignore[attr-defined]
        )
        self.assertEqual(payload.get("metadata_revision"), 1)
        self.assertEqual(payload["device_metadata"]["location"], "bench_b")
        self.assertEqual(payload["stream_metadata"]["trace"]["gain"], 2.0)

    def test_metadata_replace_and_clear_scope(self) -> None:
        mgr = _build_manager()
        _ = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "device.metadata.set",
                "device_id": "trace1",
                "params": {
                    "mode": "merge",
                    "device_metadata": {"location": "bench_b"},
                    "stream_metadata": {"trace": {"gain": 2.0}, "aux": {"scale": 10}},
                },
            },
        )
        resp_replace = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "device.metadata.set",
                "device_id": "trace1",
                "params": {
                    "mode": "replace",
                    "stream_metadata": {"trace": {"gain": 3.0}},
                },
            },
        )
        self.assertTrue(resp_replace.get("ok"))
        replaced = resp_replace.get("result", {})
        self.assertEqual(replaced["effective"]["stream_metadata"]["trace"]["gain"], 3.0)
        self.assertNotIn("aux", replaced["overrides"]["stream_metadata"])
        self.assertEqual(replaced.get("revision"), 2)

        resp_clear = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "device.metadata.clear",
                "device_id": "trace1",
                "params": {"scope": "stream"},
            },
        )
        self.assertTrue(resp_clear.get("ok"))
        cleared = resp_clear.get("result", {})
        self.assertEqual(cleared.get("scope"), "stream")
        self.assertTrue(cleared.get("changed"))
        self.assertEqual(cleared.get("revision"), 3)
        self.assertEqual(cleared["overrides"]["stream_metadata"], {})
        self.assertEqual(cleared["effective"]["stream_metadata"]["trace"]["gain"], 1.0)
        self.assertEqual(cleared["effective"]["device_metadata"]["location"], "bench_b")

    def test_metadata_set_rejects_mirrored_device(self) -> None:
        mgr = _build_manager()
        mgr._federation_hub = _HubStub(mirrored={"lab2.trace1"})  # type: ignore[attr-defined]
        resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "device.metadata.set",
                "device_id": "lab2.trace1",
                "params": {"device_metadata": {"location": "x"}},
            },
        )
        self.assertFalse(resp.get("ok"))
        error = resp.get("error", {})
        self.assertEqual(error.get("code"), "remote_device_unsupported")


if __name__ == "__main__":
    unittest.main()
