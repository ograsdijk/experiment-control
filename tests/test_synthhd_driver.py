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

    def test_per_channel_aliases_removed(self) -> None:
        driver = SynthHD("COM1")
        for name in (
            "set_frequency_channel_0",
            "set_frequency_channel_1",
            "get_frequency_channel_0",
            "get_frequency_channel_1",
            "set_power_channel_0",
            "set_power_channel_1",
            "get_power_channel_0",
            "get_power_channel_1",
            "set_enable_channel_0",
            "set_enable_channel_1",
            "get_enable_channel_0",
            "get_enable_channel_1",
            "set_phase_channel_0",
            "set_phase_channel_1",
            "get_phase_channel_0",
            "get_phase_channel_1",
        ):
            self.assertFalse(hasattr(driver, name), name)


if __name__ == "__main__":
    unittest.main()
