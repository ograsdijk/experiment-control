# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.target_spot_controller import TargetSpotControllerProcess


def _make_process() -> TargetSpotControllerProcess:
    return TargetSpotControllerProcess(
        manager_rpc="tcp://127.0.0.1:55001",
        manager_pub="tcp://127.0.0.1:55002",
    )


class AxisLimitsValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proc = _make_process()

    def test_range_spec_outside_default_axis_limits_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.proc._apply_config_patch(
                {
                    "range_spec": {
                        "center": {"x": 20000, "y": 0},
                        "size": {"width": 1000, "height": 1000},
                        "pitch": 25,
                    }
                }
            )

    def test_range_spec_inside_default_axis_limits_is_accepted(self) -> None:
        self.proc._apply_config_patch(
            {
                "range_spec": {
                    "center": {"x": 1000, "y": -500},
                    "size": {"width": 2000, "height": 2000},
                    "pitch": 25,
                }
            }
        )
        self.assertEqual(self.proc._config["range_spec"]["center"], {"x": 1000, "y": -500})

    def test_shrinking_axis_limits_below_current_range_is_rejected(self) -> None:
        self.proc._apply_config_patch(
            {
                "range_spec": {
                    "center": {"x": 0, "y": 0},
                    "size": {"width": 10000, "height": 10000},
                    "pitch": 25,
                }
            }
        )
        with self.assertRaises(ValueError):
            self.proc._apply_config_patch(
                {"axis_limits": {"xMin": -1000, "xMax": 1000, "yMin": -1000, "yMax": 1000}}
            )

    def test_malformed_axis_limits_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.proc._apply_config_patch(
                {"axis_limits": {"xMin": 1000, "xMax": -1000, "yMin": -1000, "yMax": 1000}}
            )
        with self.assertRaises(ValueError):
            self.proc._apply_config_patch(
                {"axis_limits": {"xMin": float("nan"), "xMax": 1000, "yMin": -1000, "yMax": 1000}}
            )

    def test_widening_axis_limits_then_matching_range_is_accepted(self) -> None:
        self.proc._apply_config_patch(
            {"axis_limits": {"xMin": -20000, "xMax": 20000, "yMin": -20000, "yMax": 20000}}
        )
        self.proc._apply_config_patch(
            {
                "range_spec": {
                    "center": {"x": 15000, "y": 0},
                    "size": {"width": 1000, "height": 1000},
                    "pitch": 25,
                }
            }
        )
        self.assertEqual(self.proc._config["axis_limits"]["xMax"], 20000)

    def test_combined_patch_validates_new_range_against_new_limits_atomically(self) -> None:
        # A single patch that changes range_spec and axis_limits together
        # must validate the new range against the new limits (not the
        # pre-patch limits) - a range that only fits the widened limits is
        # accepted in one call...
        self.proc._apply_config_patch(
            {
                "axis_limits": {"xMin": -20000, "xMax": 20000, "yMin": -20000, "yMax": 20000},
                "range_spec": {
                    "center": {"x": 15000, "y": 0},
                    "size": {"width": 1000, "height": 1000},
                    "pitch": 25,
                },
            }
        )
        self.assertEqual(self.proc._config["range_spec"]["center"], {"x": 15000, "y": 0})

        # ...and a combined patch that is still inconsistent (the new range
        # doesn't fit the new limits either) is rejected, leaving the prior
        # config untouched.
        with self.assertRaises(ValueError):
            self.proc._apply_config_patch(
                {
                    "axis_limits": {"xMin": -500, "xMax": 500, "yMin": -500, "yMax": 500},
                    "range_spec": {
                        "center": {"x": 15000, "y": 0},
                        "size": {"width": 1000, "height": 1000},
                        "pitch": 25,
                    },
                }
            )
        self.assertEqual(self.proc._config["axis_limits"]["xMax"], 20000)
        self.assertEqual(self.proc._config["range_spec"]["center"], {"x": 15000, "y": 0})


if __name__ == "__main__":
    unittest.main()
