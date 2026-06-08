import importlib
import unittest


class PublicImportContractsTests(unittest.TestCase):
    def test_root_manager_imports(self) -> None:
        module = importlib.import_module("experiment_control.manager")
        for name in [
            "Manager",
            "DeviceSpec",
            "ProcessSpec",
            "DeviceHandle",
            "ProcessHandle",
            "RestartPolicy",
            "ManagedProcessState",
            "device_spec_from_yaml",
            "process_spec_from_yaml",
        ]:
            self.assertTrue(hasattr(module, name), name)

    def test_root_driver_imports(self) -> None:
        module = importlib.import_module("experiment_control.driver")
        for name in [
            "DeviceRunner",
            "discover_device_members",
            "discover_capabilities",
            "TelemetryCall",
            "TelemetryOut",
            "StreamCall",
            "StreamOut",
        ]:
            self.assertTrue(hasattr(module, name), name)

    def test_moved_module_shims(self) -> None:
        modules = [
            "experiment_control._driver.stream_wrappers",
            "experiment_control._tui.app",
            "experiment_control._tui.models",
            "experiment_control._tui.screens",
            "experiment_control._manager.config",
            "experiment_control._manager.models",
            "experiment_control._manager.process_supervision",
            "experiment_control._manager.route_handlers",
            "experiment_control._manager.driver_pub",
        ]
        for name in modules:
            with self.subTest(name=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
