# ruff: noqa: E402
"""connect_all_devices must survive a driver whose process has exited.

Regression test for the manager startup crash: when a driver registers
(so it has an rpc_endpoint) but its process then exits, the bulk connect
pass used to let `_require_running_driver`'s RuntimeError propagate out of
`connect_all_devices` -> `startup_sequence` -> `run_stack.main()` (which
only catches TimeoutError), killing the whole manager. A dead driver
should degrade gracefully instead.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import (
    ConnectCheckSpec,
    DeviceHandle,
    DeviceSpec,
    Manager,
)


class _RunningProcess:
    @staticmethod
    def poll() -> None:
        return None


class _ExitedProcess:
    def __init__(self, rc: int) -> None:
        self._rc = rc

    def poll(self) -> int:
        return self._rc


class _FederationStub:
    @staticmethod
    def forward_device_request(_req: dict[str, object]) -> None:
        return None


def _make_handle(device_id: str, process: object) -> DeviceHandle:
    spec = DeviceSpec(
        device_id=device_id,
        device_class_path="dummy.py",
        device_class_name="DummyDriver",
        device_init_kwargs={},
        telemetry_calls=[],
        stream_calls=[],
        run_meta_calls=[],
        connect_check=ConnectCheckSpec(),
    )
    handle = DeviceHandle(spec=spec, process=process)
    handle.rpc_endpoint = f"inproc://test-{device_id}"
    return handle


class ConnectAllDevicesResilientTests(unittest.TestCase):
    def _build_manager(self) -> Manager:
        mgr = object.__new__(Manager)
        # Insertion order: dead device first so a propagated exception would
        # also be observable as the live device never getting connected.
        mgr._devices = {  # type: ignore[attr-defined]
            "pxie5171": _make_handle("pxie5171", _ExitedProcess(2)),
            "laser_1": _make_handle("laser_1", _RunningProcess()),
        }
        mgr._publish_manager_event = mock.Mock()  # type: ignore[attr-defined]
        mgr._call_device_rpc = mock.Mock(  # type: ignore[attr-defined]
            return_value={"status": "OK", "result": {}}
        )
        mgr._federation_hub = _FederationStub()  # type: ignore[attr-defined]
        return mgr

    def test_connect_all_devices_survives_exited_driver(self) -> None:
        mgr = self._build_manager()

        # Must not raise even though pxie5171's process has exited.
        results = mgr.connect_all_devices()

        # Both devices appear in the per-device result dict.
        self.assertEqual(set(results), {"pxie5171", "laser_1"})

        # The live device connected OK.
        self.assertEqual(results["laser_1"].get("status"), "OK")

        # The dead device produced a structured error result, not a crash.
        dead = results["pxie5171"]
        self.assertEqual(dead.get("status"), "ERROR")
        self.assertEqual(dead.get("error_code"), "connect_failed")
        self.assertIn("pxie5171", str(dead.get("error")))

        # A connect_device_failed event was published for the dead device.
        mgr._publish_manager_event.assert_any_call(
            "manager.connect_device_failed",
            {"device_id": "pxie5171", "error": mock.ANY},
        )

    def test_skips_devices_without_rpc_endpoint(self) -> None:
        mgr = self._build_manager()
        # An unregistered device (no rpc_endpoint) is skipped entirely.
        unreg = _make_handle("unregistered", _RunningProcess())
        unreg.rpc_endpoint = None
        mgr._devices["unregistered"] = unreg  # type: ignore[attr-defined]

        results = mgr.connect_all_devices()

        self.assertNotIn("unregistered", results)
        self.assertEqual(set(results), {"pxie5171", "laser_1"})


if __name__ == "__main__":
    unittest.main()
