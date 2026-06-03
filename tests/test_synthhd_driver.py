# ruff: noqa: E402

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


class _BaseSynthHD:
    def __init__(self, port: str | None = None) -> None:
        self.connected_port = port
        self.channels = [_Channel(), _Channel()]
        self.closed = False

    def __getitem__(self, channel: int) -> _Channel:
        return self.channels[int(channel)]

    def close(self) -> None:
        self.closed = True


windfreak = types.ModuleType("windfreak")
windfreak.SynthHD = _BaseSynthHD
sys.modules.setdefault("windfreak", windfreak)

from experiment_control.drivers.synthhd_driver import SynthHD


class SynthHDDriverTests(unittest.TestCase):
    def test_parameterized_channel_methods(self) -> None:
        driver = SynthHD("COM1")
        driver.connect()
        self.assertEqual(driver.connected_port, "COM1")

        driver.set_frequency(1, 10.5)
        driver.set_power(1, -3.0)
        driver.set_enable(1, True)
        driver.set_phase(1, 90.0)

        self.assertEqual(driver.get_frequency(1), 10.5)
        self.assertEqual(driver.get_power(1), -3.0)
        self.assertIs(driver.get_enable(1), True)
        self.assertEqual(driver.get_phase(1), 90.0)

        driver.disconnect()
        self.assertTrue(driver.closed)

    def test_per_channel_aliases_delegate_and_warn(self) -> None:
        # Deprecation shims kept for one release while the downstream
        # YAML configs (laser-lock-1 SG[123].yaml, frequency_step_guard,
        # laser_lock_freq_nltl_power) still reference the old names.
        # The shims MUST forward to the parameterized methods AND emit
        # DeprecationWarning so the migration signal is visible.
        import warnings

        driver = SynthHD("COM1")
        driver.connect()

        cases = [
            ("set_frequency_channel_0", (12.0,), lambda: driver.get_frequency(0), 12.0),
            ("set_frequency_channel_1", (13.0,), lambda: driver.get_frequency(1), 13.0),
            ("set_power_channel_0", (-1.0,), lambda: driver.get_power(0), -1.0),
            ("set_power_channel_1", (-2.0,), lambda: driver.get_power(1), -2.0),
            ("set_enable_channel_0", (True,), lambda: driver.get_enable(0), True),
            ("set_enable_channel_1", (True,), lambda: driver.get_enable(1), True),
            ("set_phase_channel_0", (45.0,), lambda: driver.get_phase(0), 45.0),
            ("set_phase_channel_1", (135.0,), lambda: driver.get_phase(1), 135.0),
        ]
        for setter_name, args, getter, expected in cases:
            setter = getattr(driver, setter_name)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                setter(*args)
            self.assertEqual(getter(), expected)
            self.assertTrue(
                any(
                    issubclass(w.category, DeprecationWarning)
                    and setter_name in str(w.message)
                    for w in caught
                ),
                f"expected DeprecationWarning naming {setter_name}, got "
                f"{[(w.category.__name__, str(w.message)) for w in caught]}",
            )

        # Mirror coverage for the per-channel getters.
        getter_cases = [
            ("get_frequency_channel_0", 12.0),
            ("get_frequency_channel_1", 13.0),
            ("get_power_channel_0", -1.0),
            ("get_power_channel_1", -2.0),
            ("get_enable_channel_0", True),
            ("get_enable_channel_1", True),
            ("get_phase_channel_0", 45.0),
            ("get_phase_channel_1", 135.0),
        ]
        for getter_name, expected in getter_cases:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                value = getattr(driver, getter_name)()
            self.assertEqual(value, expected)
            self.assertTrue(
                any(
                    issubclass(w.category, DeprecationWarning)
                    and getter_name in str(w.message)
                    for w in caught
                ),
                f"expected DeprecationWarning naming {getter_name}",
            )


if __name__ == "__main__":
    unittest.main()
