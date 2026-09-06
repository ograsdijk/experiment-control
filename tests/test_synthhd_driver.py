# ruff: noqa: E402

import math
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class _Channel:
    def __init__(self) -> None:
        self.frequency = 0.0
        self.power = 0.0
        self.enable = False
        self.phase = 0.0
        self.temp_compensation_mode = "10 sec"
        self.lock_status = False


class _BaseSynthHD:
    def __init__(self, port: str | None = None) -> None:
        self.connected_port = port
        self.channels = [_Channel(), _Channel()]
        self.closed = False
        self.reference_mode = "internal 27mhz"
        self.reference_frequency = 27.0e6
        self._sweep_cont = False
        self.trigger_count = 0

    def __getitem__(self, channel: int) -> _Channel:
        return self.channels[int(channel)]

    @property
    def sweep_cont(self) -> bool:
        return self._sweep_cont

    @sweep_cont.setter
    def sweep_cont(self, value: bool) -> None:
        self._sweep_cont = bool(value)

    # Read-only diagnostics, mirroring the real windfreak class.
    @property
    def temperature(self) -> float:
        return 42.5

    @property
    def serial_number(self) -> str:
        return "SN-0001"

    def trigger(self) -> None:
        self.trigger_count += 1

    def init(self) -> None:
        for channel in self.channels:
            channel.frequency = 0.0
            channel.power = 0.0

    def open(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def read(self, _attribute: str) -> object:
        return None

    def write(self, _attribute: str, *_args: object) -> None:
        pass

    def save(self) -> None:
        pass


windfreak = types.ModuleType("windfreak")
windfreak.SynthHD = _BaseSynthHD
sys.modules.setdefault("windfreak", windfreak)

from experiment_control._driver.runner import DeviceRunner
from experiment_control.driver import discover_device_members
from experiment_control.drivers.synthhd_driver import SynthHD
from experiment_control.types import DeviceState


class SynthHDDriverTests(unittest.TestCase):
    def test_parameterized_channel_methods(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()
        self.assertEqual(driver.connected_port, "COM1")

        driver.set_frequency(2, 10.5)
        driver.set_power(2, -3.0)
        driver.set_enable(2, True)
        driver.set_phase(2, 90.0)
        driver.set_temp_compensation_mode(2, "on set")

        self.assertEqual(driver.get_frequency(2), 10.5)
        self.assertEqual(driver.get_power(2), -3.0)
        self.assertIs(driver.get_enable(2), True)
        self.assertEqual(driver.get_phase(2), 90.0)
        self.assertEqual(driver.get_temp_compensation_mode(2), "on set")

        driver.disconnect()
        self.assertTrue(driver.closed)

    def test_driver_index_maps_to_physical_channels(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()

        # Seed booleans opposite the target values so both writes are observable.
        driver.channels[0].enable = True
        driver.channels[1].enable = False

        driver.set_frequency(1, 1.0)
        driver.set_frequency(2, 2.0)
        driver.set_power(1, -1.0)
        driver.set_power(2, -2.0)
        driver.set_enable(1, False)
        driver.set_enable(2, True)
        driver.set_phase(1, 10.0)
        driver.set_phase(2, 20.0)
        driver.set_temp_compensation_mode(1, "none")
        driver.set_temp_compensation_mode(2, "on set")

        self.assertEqual(driver.channels[0].frequency, 1.0)  # CH1/A
        self.assertEqual(driver.channels[1].frequency, 2.0)  # CH2/B
        self.assertEqual(driver.channels[0].power, -1.0)
        self.assertEqual(driver.channels[1].power, -2.0)
        self.assertIs(driver.channels[0].enable, False)
        self.assertIs(driver.channels[1].enable, True)
        self.assertEqual(driver.channels[0].phase, 10.0)
        self.assertEqual(driver.channels[1].phase, 20.0)
        self.assertEqual(driver.channels[0].temp_compensation_mode, "none")
        self.assertEqual(driver.channels[1].temp_compensation_mode, "on set")

        self.assertEqual(driver.get_frequency(1), 1.0)
        self.assertEqual(driver.get_frequency(2), 2.0)
        self.assertEqual(driver.get_power(1), -1.0)
        self.assertEqual(driver.get_power(2), -2.0)
        self.assertIs(driver.get_enable(1), False)
        self.assertIs(driver.get_enable(2), True)
        self.assertEqual(driver.get_phase(1), 10.0)
        self.assertEqual(driver.get_phase(2), 20.0)
        self.assertEqual(driver.get_temp_compensation_mode(1), "none")
        self.assertEqual(driver.get_temp_compensation_mode(2), "on set")

    def test_invalid_channel_index_is_rejected_without_coercion(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()

        for invalid in (0, 3, -1, True, False, 1.0, 2.0, 0.9, 1.9, "1", "2", "ch1", ""):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    ValueError, r"integer 1 \(CH1/A\).*2 \(CH2/B\)"
                ):
                    driver.get_frequency(invalid)  # type: ignore[arg-type]

    def test_non_finite_numeric_setpoints_are_rejected(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()

        setters = (
            (lambda value: driver.set_frequency(1, value), "frequency"),
            (lambda value: driver.set_power(1, value), "power"),
            (lambda value: driver.set_phase(1, value), "phase"),
            (driver.set_reference_frequency, "reference frequency"),
        )
        for invalid in (math.nan, math.inf, -math.inf):
            for setter, label in setters:
                with self.subTest(invalid=invalid, setter=label):
                    with self.assertRaisesRegex(ValueError, rf"{label} must be finite"):
                        setter(invalid)

        self.assertEqual(driver.channels[0].frequency, 0.0)
        self.assertEqual(driver.channels[0].power, 0.0)
        self.assertEqual(driver.channels[0].phase, 0.0)
        self.assertEqual(driver.reference_frequency, 27.0e6)

    def test_runner_preserves_raw_channel_type_for_driver_validation(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()

        runner = object.__new__(DeviceRunner)
        runner._device = driver  # type: ignore[attr-defined]
        runner._stream_rpc = {}  # type: ignore[attr-defined]
        runner._members_cache = {  # type: ignore[attr-defined]
            member.name: member for member in discover_device_members(driver)
        }
        runner._device_state = DeviceState.OK  # type: ignore[attr-defined]
        runner._device_reachable = True  # type: ignore[attr-defined]
        runner._last_error = None  # type: ignore[attr-defined]
        runner._action_failed_since_last_ok = False  # type: ignore[attr-defined]

        valid = runner._rpc_dispatch_device_command(
            {
                "id": "valid",
                "action": "set_frequency",
                "params": {"channel": 1, "freq_hz": 12.5},
            }
        )
        self.assertEqual(valid["status"], "OK")
        self.assertEqual(driver.channels[0].frequency, 12.5)

        for invalid in (True, False, 0, 3, 1.0, 2.0, 0.9, 1.9, "1", "2", "ch1"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(TypeError, r"channel must be one of \(1, 2\)"):
                    runner._rpc_dispatch_device_command(
                        {
                            "id": "invalid",
                            "action": "set_frequency",
                            "params": {"channel": invalid, "freq_hz": 99.0},
                        }
                    )
                self.assertTrue(runner._device_reachable)
        self.assertEqual(driver.channels[0].frequency, 12.5)
        self.assertEqual(driver.channels[1].frequency, 0.0)

    def test_zero_indexed_per_channel_aliases_are_removed(self) -> None:
        driver = SynthHD("COM1")
        legacy_names = [
            f"{prefix}_channel_{channel}"
            for prefix in (
                "set_frequency",
                "get_frequency",
                "set_power",
                "get_power",
                "set_enable",
                "get_enable",
                "set_phase",
                "get_phase",
                "set_temp_compensation_mode",
                "get_temp_compensation_mode",
                "get_lock_status",
            )
            for channel in (0, 1)
        ]
        for name in legacy_names:
            with self.subTest(name=name):
                self.assertFalse(hasattr(driver, name), name)

    def test_wrapper_does_not_restrict_its_rpc_surface(self) -> None:
        """The driver declares no hidden-member list.

        #154 hid every public member outside a hand-maintained allowlist. That
        was never a safety boundary -- an action with no matching interceptor
        route passes straight through -- and it silently deleted
        ``set_reference_mode`` and ``set_reference_frequency`` from the RPC
        surface by omitting them from the allowlist. The restriction is gone;
        keep it gone.
        """
        driver = SynthHD("COM1")
        driver.connect()

        self.assertFalse(hasattr(driver, "__experiment_control_rpc_hidden__"))
        self.assertFalse(hasattr(driver, "_RPC_EXPOSED_MEMBERS"))

        advertised = {member.name for member in discover_device_members(driver)}

        # The wrapper's own deliberate API.
        for name in (
            "set_frequency",
            "get_frequency",
            "set_power",
            "get_power",
            "set_enable",
            "get_enable",
            "set_phase",
            "get_phase",
            "set_temp_compensation_mode",
            "get_temp_compensation_mode",
            "get_lock_status",
            "get_reference_mode",
            "get_reference_frequency",
        ):
            with self.subTest(wrapper_method=name):
                self.assertIn(name, advertised)

        # Regression: #154 removed these two by omission.
        for name in ("set_reference_mode", "set_reference_frequency"):
            with self.subTest(restored=name):
                self.assertIn(name, advertised)

        # Inherited upstream members are reachable again, by design.
        for name in ("temperature", "serial_number", "sweep_cont"):
            with self.subTest(upstream=name):
                self.assertIn(name, advertised)

    def test_reference_setters_dispatch_via_rpc(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()

        runner = object.__new__(DeviceRunner)
        runner._device = driver  # type: ignore[attr-defined]
        runner._stream_rpc = {}  # type: ignore[attr-defined]
        runner._members_cache = {  # type: ignore[attr-defined]
            member.name: member for member in discover_device_members(driver)
        }
        runner._device_state = DeviceState.OK  # type: ignore[attr-defined]

        runner.handle_command("set_reference_mode", {"mode": "external"})
        self.assertEqual(driver.get_reference_mode(), "external")

        runner.handle_command("set_reference_frequency", {"freq_hz": 10.0e6})
        self.assertEqual(driver.get_reference_frequency(), 10.0e6)

    def test_reference_frequency_rejects_non_finite(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()

        for bad in (math.inf, -math.inf, math.nan):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    driver.set_reference_frequency(bad)
        self.assertEqual(driver.reference_frequency, 27.0e6)

    def test_lock_status_methods(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()

        driver.channels[0].lock_status = True
        driver.channels[1].lock_status = False

        self.assertIs(driver.get_lock_status(1), True)
        self.assertIs(driver.get_lock_status(2), False)

    def test_reference_methods(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()

        self.assertEqual(driver.get_reference_mode(), "internal 27mhz")
        self.assertEqual(driver.get_reference_frequency(), 27.0e6)

        driver.set_reference_mode("external")
        driver.set_reference_frequency(10.0e6)

        self.assertEqual(driver.get_reference_mode(), "external")
        self.assertEqual(driver.get_reference_frequency(), 10.0e6)


if __name__ == "__main__":
    unittest.main()
