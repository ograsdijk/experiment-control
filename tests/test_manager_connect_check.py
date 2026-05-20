# ruff: noqa: E402

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import (
    ConnectCheckSpec,
    DeviceHandle,
    DeviceSpec,
    Manager,
    device_spec_from_yaml,
)
from _temp_utils import repo_temp_dir


class _RunningProcess:
    @staticmethod
    def poll() -> None:
        return None


class _FederationStub:
    @staticmethod
    def forward_device_request(_req: dict[str, object]) -> None:
        return None


def _build_manager_with_device(
    *,
    connect_check: ConnectCheckSpec,
) -> tuple[Manager, DeviceHandle]:
    mgr = object.__new__(Manager)
    spec = DeviceSpec(
        device_id="laser_1",
        device_class_path="dummy.py",
        device_class_name="DummyDriver",
        device_init_kwargs={},
        telemetry_calls=[],
        stream_calls=[],
        run_meta_calls=[],
        connect_check=connect_check,
    )
    handle = DeviceHandle(spec=spec, process=_RunningProcess())
    mgr._devices = {"laser_1": handle}  # type: ignore[attr-defined]
    mgr._publish_manager_event = mock.Mock()  # type: ignore[attr-defined]
    mgr._call_device_rpc = mock.Mock()  # type: ignore[attr-defined]
    mgr._federation_hub = _FederationStub()  # type: ignore[attr-defined]
    return mgr, handle


class ManagerConnectCheckTests(unittest.TestCase):
    def test_device_spec_from_yaml_parses_connect_check_defaults_disconnect(self) -> None:
        with repo_temp_dir("connect-check-config") as base:
            driver_path = base / "dummy_driver.py"
            driver_path.write_text(
                textwrap.dedent(
                    """
                    class DummyDriver:
                        def __init__(self):
                            pass
                        def connect(self):
                            return None
                        def disconnect(self):
                            return None
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            device_yaml = base / "device.yaml"
            device_yaml.write_text(
                textwrap.dedent(
                    f"""
                    device_id: laser_1
                    driver:
                      file: {driver_path.as_posix()}
                      class_name: DummyDriver
                    connect_check:
                      enabled: true
                      identity:
                        serial: ABC12345
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = device_spec_from_yaml(device_yaml)
            self.assertTrue(spec.connect_check.enabled)
            self.assertEqual(spec.connect_check.identity, {"serial": "ABC12345"})
            self.assertEqual(spec.connect_check.on_fail, "disconnect")

    def test_device_spec_from_yaml_rejects_enabled_connect_check_without_identity(
        self,
    ) -> None:
        with repo_temp_dir("connect-check-config") as base:
            driver_path = base / "dummy_driver.py"
            driver_path.write_text(
                textwrap.dedent(
                    """
                    class DummyDriver:
                        def __init__(self):
                            pass
                        def connect(self):
                            return None
                        def disconnect(self):
                            return None
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            device_yaml = base / "device.yaml"
            device_yaml.write_text(
                textwrap.dedent(
                    f"""
                    device_id: laser_1
                    driver:
                      file: {driver_path.as_posix()}
                      class_name: DummyDriver
                    connect_check:
                      enabled: true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(TypeError, "connect_check.identity"):
                _ = device_spec_from_yaml(device_yaml)

    def test_connect_device_already_connected_is_idempotent_success(self) -> None:
        mgr, handle = _build_manager_with_device(connect_check=ConnectCheckSpec(enabled=False))
        mgr._call_device_rpc.return_value = {  # type: ignore[attr-defined]
            "status": "ERROR",
            "error": "Device is already connected (OK)",
            "error_code": "already_connected",
        }

        resp = Manager.connect_device(mgr, "laser_1")

        self.assertEqual(resp.get("status"), "OK")
        self.assertTrue(resp.get("already_connected"))
        self.assertIsNone(handle.connect_check_last)

    def test_connect_device_already_connected_runs_enabled_identity_check(self) -> None:
        mgr, handle = _build_manager_with_device(
            connect_check=ConnectCheckSpec(enabled=True, identity={"serial": "ABC12345"})
        )
        mgr._call_device_rpc.side_effect = [  # type: ignore[attr-defined]
            {"status": "ERROR", "error": "Device is already connected (OK)"},
            {"status": "OK", "result": {"serial": "ABC12345"}},
        ]

        resp = Manager.connect_device(mgr, "laser_1")

        self.assertEqual(resp.get("status"), "OK")
        self.assertTrue(resp.get("already_connected"))
        calls = mgr._call_device_rpc.call_args_list  # type: ignore[attr-defined]
        self.assertEqual([str(call.kwargs.get("action")) for call in calls], ["connect_device", "identity"])
        self.assertIsNotNone(handle.connect_check_last)
        self.assertEqual(handle.connect_check_last.get("ok"), True)

    def test_connect_device_already_connected_identity_mismatch_fails(self) -> None:
        mgr, handle = _build_manager_with_device(
            connect_check=ConnectCheckSpec(enabled=True, identity={"serial": "ABC12345"})
        )
        mgr._call_device_rpc.side_effect = [  # type: ignore[attr-defined]
            {"status": "ERROR", "error": "Device is already connected (OK)"},
            {"status": "OK", "result": {"serial": "XYZ999"}},
            {"status": "OK", "result": None},
        ]

        resp = Manager.connect_device(mgr, "laser_1")

        self.assertEqual(resp.get("status"), "ERROR")
        self.assertEqual(resp.get("error_code"), "connect_check_failed")
        calls = mgr._call_device_rpc.call_args_list  # type: ignore[attr-defined]
        self.assertEqual([str(call.kwargs.get("action")) for call in calls], ["connect_device", "identity", "disconnect_device"])
        self.assertIsNotNone(handle.connect_check_last)
        self.assertEqual(handle.connect_check_last.get("ok"), False)

    def test_connect_device_identity_check_passes(self) -> None:
        mgr, handle = _build_manager_with_device(
            connect_check=ConnectCheckSpec(
                enabled=True,
                identity={"serial": "ABC12345"},
            )
        )
        mgr._call_device_rpc.side_effect = [  # type: ignore[attr-defined]
            {"status": "OK", "result": None},
            {"status": "OK", "result": {"serial": "ABC12345", "model": "X1"}},
        ]

        resp = Manager.connect_device(mgr, "laser_1")
        self.assertEqual(resp.get("status"), "OK")
        calls = mgr._call_device_rpc.call_args_list  # type: ignore[attr-defined]
        self.assertEqual(str(calls[0].kwargs.get("action")), "connect_device")
        self.assertEqual(str(calls[1].kwargs.get("action")), "identity")
        self.assertIsNotNone(handle.connect_check_last)
        self.assertEqual(handle.connect_check_last.get("ok"), True)

    def test_connect_device_identity_mismatch_disconnects_by_default(self) -> None:
        mgr, handle = _build_manager_with_device(
            connect_check=ConnectCheckSpec(
                enabled=True,
                identity={"serial": "ABC12345"},
            )
        )
        mgr._call_device_rpc.side_effect = [  # type: ignore[attr-defined]
            {"status": "OK", "result": None},
            {"status": "OK", "result": {"serial": "XYZ999"}},
            {"status": "OK", "result": None},
        ]

        resp = Manager.connect_device(mgr, "laser_1")
        self.assertEqual(resp.get("status"), "ERROR")
        self.assertEqual(resp.get("error_code"), "connect_check_failed")
        calls = mgr._call_device_rpc.call_args_list  # type: ignore[attr-defined]
        actions = [str(call.kwargs.get("action")) for call in calls]
        self.assertEqual(actions, ["connect_device", "identity", "disconnect_device"])
        self.assertIsNotNone(handle.connect_check_last)
        self.assertEqual(handle.connect_check_last.get("ok"), False)

    def test_connect_device_identity_mismatch_keep_connected_policy(self) -> None:
        mgr, _handle = _build_manager_with_device(
            connect_check=ConnectCheckSpec(
                enabled=True,
                identity={"serial": "ABC12345"},
                on_fail="keep_connected",
            )
        )
        mgr._call_device_rpc.side_effect = [  # type: ignore[attr-defined]
            {"status": "OK", "result": None},
            {"status": "OK", "result": {"serial": "XYZ999"}},
        ]

        _ = Manager.connect_device(mgr, "laser_1")
        calls = mgr._call_device_rpc.call_args_list  # type: ignore[attr-defined]
        actions = [str(call.kwargs.get("action")) for call in calls]
        self.assertEqual(actions, ["connect_device", "identity"])

    def test_device_connect_route_propagates_connect_check_failure(self) -> None:
        mgr, _handle = _build_manager_with_device(
            connect_check=ConnectCheckSpec(enabled=False)
        )
        mgr.connect_device = mock.Mock(  # type: ignore[attr-defined]
            return_value={
                "status": "ERROR",
                "error": "connect_check failed for laser_1",
                "error_code": "connect_check_failed",
                "error_details": {"field": "serial"},
            }
        )

        resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {"type": "device.connect", "device_id": "laser_1"},
        )
        self.assertFalse(resp.get("ok"))
        error = resp.get("error", {})
        self.assertEqual(error.get("code"), "connect_check_failed")
        self.assertIn("connect_check failed", str(error.get("message", "")))


if __name__ == "__main__":
    unittest.main()
