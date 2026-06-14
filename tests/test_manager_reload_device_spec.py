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

from _temp_utils import repo_temp_dir
from experiment_control._manager.device_routing import route_device_request
from experiment_control._manager.process_supervision import restart_driver
from experiment_control.manager import DeviceHandle, Manager, device_spec_from_yaml


def _write_driver(base: Path) -> Path:
    driver_path = base / "dummy_driver.py"
    driver_path.write_text(
        textwrap.dedent(
            """
            class DummyDriver:
                def __init__(self, port=None):
                    self.port = port
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return driver_path


def _write_device_yaml(base: Path, *, device_id: str, port: str) -> Path:
    driver_path = _write_driver(base)
    device_yaml = base / "device.yaml"
    device_yaml.write_text(
        textwrap.dedent(
            f"""
            device_id: {device_id}
            driver:
              file: {driver_path.as_posix()}
              class_name: DummyDriver
            init_kwargs:
              port: {port}
            telemetry_calls: []
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return device_yaml


class ManagerReloadDeviceSpecTests(unittest.TestCase):
    def test_device_spec_from_yaml_records_config_path(self) -> None:
        with repo_temp_dir("reload-device-spec") as base:
            device_yaml = _write_device_yaml(base, device_id="dev1", port="COM1")

            spec = device_spec_from_yaml(device_yaml)

        self.assertEqual(spec.config_path, device_yaml.resolve())

    def test_device_spec_from_yaml_resolves_relative_config_path(self) -> None:
        with repo_temp_dir("reload-device-spec") as base:
            device_yaml = _write_device_yaml(base, device_id="dev1", port="COM1")
            relative = device_yaml.relative_to(ROOT)

            spec = device_spec_from_yaml(relative)

        self.assertEqual(spec.config_path, device_yaml.resolve())

    def test_reload_device_spec_updates_spec_from_disk(self) -> None:
        with repo_temp_dir("reload-device-spec") as base:
            device_yaml = _write_device_yaml(base, device_id="dev1", port="COM1")
            spec = device_spec_from_yaml(device_yaml)
            mgr = object.__new__(Manager)
            handle = DeviceHandle(spec=spec)
            handle.config_published = True
            mgr._devices = {"dev1": handle}  # type: ignore[attr-defined]

            _write_device_yaml(base, device_id="dev1", port="COM2")
            reloaded = Manager.reload_device_spec(mgr, "dev1")

        self.assertIs(handle.spec, reloaded)
        self.assertEqual(handle.spec.device_init_kwargs["port"], "COM2")
        self.assertFalse(handle.config_published)

    def test_reload_device_spec_rejects_changed_device_id(self) -> None:
        with repo_temp_dir("reload-device-spec") as base:
            device_yaml = _write_device_yaml(base, device_id="dev1", port="COM1")
            spec = device_spec_from_yaml(device_yaml)
            mgr = object.__new__(Manager)
            handle = DeviceHandle(spec=spec)
            mgr._devices = {"dev1": handle}  # type: ignore[attr-defined]

            _write_device_yaml(base, device_id="dev2", port="COM2")
            with self.assertRaisesRegex(ValueError, "does not match"):
                Manager.reload_device_spec(mgr, "dev1")

        self.assertEqual(handle.spec.device_id, "dev1")
        self.assertEqual(handle.spec.device_init_kwargs["port"], "COM1")

    def test_restart_with_invalid_reload_does_not_stop_driver(self) -> None:
        with repo_temp_dir("reload-device-spec") as base:
            device_yaml = _write_device_yaml(base, device_id="dev1", port="COM1")
            spec = device_spec_from_yaml(device_yaml)
            manager = mock.Mock()
            manager._devices = {"dev1": DeviceHandle(spec=spec)}
            manager.load_device_spec_from_disk.side_effect = ValueError("bad yaml")

            with self.assertRaisesRegex(ValueError, "bad yaml"):
                restart_driver(manager, "dev1", reload_config=True)

        manager.disconnect_device.assert_not_called()
        manager.stop_driver.assert_not_called()

    def test_device_restart_route_passes_reload_config(self) -> None:
        manager = mock.Mock()
        manager._devices = {"dev1": object()}

        resp = route_device_request(
            manager,
            "device.driver.restart",
            {"device_id": "dev1", "reload_config": True},
        )

        self.assertEqual(resp, {"ok": True, "result": {"device_id": "dev1"}})
        manager.restart_driver.assert_called_once_with(
            "dev1",
            force=False,
            reload_config=True,
        )


if __name__ == "__main__":
    unittest.main()
