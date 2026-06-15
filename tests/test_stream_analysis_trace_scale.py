# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.stream_analysis import (
    OPS,
    OP_PARAM_SCHEMAS,
    execute_trace_scale,
)


class TraceScaleOpTests(unittest.TestCase):
    def test_registered(self) -> None:
        self.assertIn("trace.scale", OPS)
        self.assertEqual(OPS["trace.scale"].input_types, {"trace": "trace"})
        self.assertEqual(OPS["trace.scale"].output_type, "trace")
        self.assertFalse(OPS["trace.scale"].stateful)
        self.assertIn("trace.scale", OP_PARAM_SCHEMAS)

    def test_negate(self) -> None:
        t = np.array([0.0, -1.0, -5.0, -2.0], dtype=np.float64)
        out = execute_trace_scale(t, {"factor": -1.0})
        np.testing.assert_allclose(out, [0.0, 1.0, 5.0, 2.0])

    def test_default_factor_is_identity(self) -> None:
        t = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        np.testing.assert_allclose(execute_trace_scale(t, {}), t)

    def test_arbitrary_factor(self) -> None:
        t = np.array([2.0, 4.0], dtype=np.float64)
        np.testing.assert_allclose(execute_trace_scale(t, {"factor": 0.5}), [1.0, 2.0])

    def test_none_input_is_none(self) -> None:
        self.assertIsNone(execute_trace_scale(None, {"factor": -1.0}))

    def test_invalid_factor_falls_back_to_identity(self) -> None:
        t = np.array([1.0, -2.0], dtype=np.float64)
        np.testing.assert_allclose(execute_trace_scale(t, {"factor": "x"}), t)


if __name__ == "__main__":
    unittest.main()
