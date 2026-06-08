# ruff: noqa: E402

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import (
    DeviceHandle,
    DeviceSpec,
    ManagedProcessState,
    Manager,
    device_spec_from_yaml,
)
from experiment_control._manager.process_supervision import _maybe_auto_reconnect_device
from experiment_control.types import Timestamp


class ManagerAutoReconnectTests(unittest.TestCase):
    def test_device_spec_parses_auto_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "device.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    version: 1
                    device_id: pt415
                    driver:
                      file: devices/drivers/cpa1110_driver.py
                      class_name: CPA1110Device
                    init_kwargs: {}
                    auto_reconnect:
                      enabled: true
                      on_telemetry_stale_s: 5.0
                      cooldown_s: 30.0
                      max_attempts: 3
                      reset_attempts_after_ok_s: 120.0
                      disconnect_timeout_ms: 1000
                    telemetry_calls: []
                    """
                ),
                encoding="utf-8",
            )

            spec = device_spec_from_yaml(path)

        self.assertTrue(spec.auto_reconnect.enabled)
        self.assertEqual(spec.auto_reconnect.on_telemetry_stale_s, 5.0)
        self.assertEqual(spec.auto_reconnect.max_attempts, 3)
        self.assertEqual(spec.auto_reconnect.disconnect_timeout_ms, 1000)

    def test_auto_reconnect_attempts_disconnect_then_connect_on_stale_telemetry(self) -> None:
        spec = DeviceSpec(
            device_id="pt415",
            device_class_path="driver.py",
            device_class_name="Driver",
            device_init_kwargs={},
            telemetry_calls=[],
            auto_reconnect=device_spec_from_yaml(
                self._write_auto_reconnect_device_yaml()
            ).auto_reconnect,
        )
        handle = DeviceHandle(spec=spec)
        handle.driver_process_state = ManagedProcessState.RUNNING
        handle.rpc_endpoint = "tcp://127.0.0.1:1"
        manager = mock.Mock()
        manager._devices = {"pt415": handle}
        manager._telemetry_last_bundle_ts = {"pt415": Timestamp(t_wall=1.0, t_mono=10.0)}
        manager._device_rpc_timeout_ms = 2000
        manager._call_device_rpc.return_value = {"status": "OK"}
        manager._device_rpc_status_ok = Manager._device_rpc_status_ok
        manager._device_rpc_error_text = Manager._device_rpc_error_text

        _maybe_auto_reconnect_device(manager, "pt415", handle, 20.0)

        self.assertEqual(handle.auto_reconnect_attempts, 1)
        self.assertEqual(
            [call.kwargs["action"] for call in manager._call_device_rpc.call_args_list],
            ["disconnect_device", "connect_device"],
        )
        manager._publish_manager_event.assert_any_call(
            "manager.device.auto_reconnect.attempt",
            mock.ANY,
        )
        manager._publish_manager_event.assert_any_call(
            "manager.device.auto_reconnect.success",
            mock.ANY,
        )

    def test_auto_reconnect_suppresses_after_max_attempts(self) -> None:
        spec = DeviceSpec(
            device_id="pt415",
            device_class_path="driver.py",
            device_class_name="Driver",
            device_init_kwargs={},
            telemetry_calls=[],
            auto_reconnect=device_spec_from_yaml(
                self._write_auto_reconnect_device_yaml()
            ).auto_reconnect,
        )
        handle = DeviceHandle(spec=spec)
        handle.driver_process_state = ManagedProcessState.RUNNING
        handle.rpc_endpoint = "tcp://127.0.0.1:1"
        handle.auto_reconnect_attempts = 3
        manager = mock.Mock()
        manager._telemetry_last_bundle_ts = {"pt415": Timestamp(t_wall=1.0, t_mono=10.0)}

        _maybe_auto_reconnect_device(manager, "pt415", handle, 20.0)

        manager._call_device_rpc.assert_not_called()
        self.assertTrue(handle.auto_reconnect_suppressed)
        manager._publish_manager_event.assert_called_once()
        self.assertEqual(
            manager._publish_manager_event.call_args.args[0],
            "manager.device.auto_reconnect.suppressed",
        )

    def test_auto_reconnect_resets_after_healthy_period(self) -> None:
        spec = DeviceSpec(
            device_id="pt415",
            device_class_path="driver.py",
            device_class_name="Driver",
            device_init_kwargs={},
            telemetry_calls=[],
            auto_reconnect=device_spec_from_yaml(
                self._write_auto_reconnect_device_yaml()
            ).auto_reconnect,
        )
        handle = DeviceHandle(spec=spec)
        handle.auto_reconnect_attempts = 2
        handle.auto_reconnect_healthy_since_mono = 100.0
        handle.auto_reconnect_suppressed = True
        handle.auto_reconnect_last_error = "failed"
        manager = mock.Mock()
        manager._telemetry_last_bundle_ts = {"pt415": Timestamp(t_wall=1.0, t_mono=219.0)}

        _maybe_auto_reconnect_device(manager, "pt415", handle, 220.0)

        self.assertEqual(handle.auto_reconnect_attempts, 0)
        self.assertFalse(handle.auto_reconnect_suppressed)
        self.assertIsNone(handle.auto_reconnect_last_error)
        manager._publish_manager_event.assert_called_once()
        self.assertEqual(
            manager._publish_manager_event.call_args.args[0],
            "manager.device.auto_reconnect.reset",
        )

    @staticmethod
    def _write_auto_reconnect_device_yaml() -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        path = Path(tmp.name)
        with tmp:
            tmp.write(
                textwrap.dedent(
                    """
                    version: 1
                    device_id: pt415
                    driver:
                      file: driver.py
                      class_name: Driver
                    init_kwargs: {}
                    auto_reconnect:
                      enabled: true
                      on_telemetry_stale_s: 5.0
                      cooldown_s: 30.0
                      max_attempts: 3
                      reset_attempts_after_ok_s: 120.0
                      disconnect_timeout_ms: 1000
                    telemetry_calls: []
                    """
                )
            )
        return path


if __name__ == "__main__":
    unittest.main()
