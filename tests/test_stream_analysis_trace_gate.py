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
    execute_trace_gate,
)


class TraceGateOpTests(unittest.TestCase):
    def test_registered(self) -> None:
        self.assertIn("trace.gate", OPS)
        self.assertEqual(OPS["trace.gate"].input_types, {"trace": "trace"})
        self.assertEqual(OPS["trace.gate"].optional_input_types, {"gate": "scalar"})
        self.assertEqual(OPS["trace.gate"].output_type, "trace")
        self.assertFalse(OPS["trace.gate"].stateful)
        self.assertNotIn("trace.gate", OP_PARAM_SCHEMAS)

    def test_open_gate_passes_trace_through(self) -> None:
        t = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        out = execute_trace_gate(t, gate_open=True)
        np.testing.assert_allclose(out, t)

    def test_closed_gate_yields_none(self) -> None:
        t = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        self.assertIsNone(execute_trace_gate(t, gate_open=False))

    def test_none_input_is_none_regardless_of_gate(self) -> None:
        self.assertIsNone(execute_trace_gate(None, gate_open=True))
        self.assertIsNone(execute_trace_gate(None, gate_open=False))


if __name__ == "__main__":
    unittest.main()
