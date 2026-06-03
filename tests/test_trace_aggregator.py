# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.fastapi._trace_aggregator import TraceAggregator


class TraceAggregatorTests(unittest.TestCase):
    def test_rolling_average(self) -> None:
        agg = TraceAggregator(rolling_window=2, trace_average_mode="rolling")
        first = agg.add_frame(np.asarray([1.0, 3.0]))
        second = agg.add_frame(np.asarray([3.0, 5.0]))
        third = agg.add_frame(np.asarray([5.0, 7.0]))
        self.assertEqual(first.tolist(), [1.0, 3.0])
        self.assertEqual(second.tolist(), [2.0, 4.0])
        self.assertEqual(third.tolist(), [4.0, 6.0])

    def test_block_average(self) -> None:
        agg = TraceAggregator(rolling_window=2, trace_average_mode="block")
        self.assertIsNone(agg.add_frame(np.asarray([1.0, 3.0])))
        out = agg.add_frame(np.asarray([3.0, 5.0]))
        self.assertEqual(out.tolist(), [2.0, 4.0])
        self.assertIsNone(agg.add_frame(np.asarray([10.0, 20.0])))

    def test_size_change_resets_accumulators(self) -> None:
        agg = TraceAggregator(rolling_window=2, trace_average_mode="rolling")
        self.assertEqual(agg.add_frame(np.asarray([1.0, 3.0])).tolist(), [1.0, 3.0])
        self.assertEqual(agg.add_frame(np.asarray([10.0])).tolist(), [10.0])

    def test_reset_and_flush(self) -> None:
        agg = TraceAggregator(rolling_window=2, trace_average_mode="rolling")
        agg.add_frame(np.asarray([1.0, 3.0]))
        agg.pending_msg = {"topic": "x"}
        self.assertEqual(agg.flush(), {"topic": "x"})
        self.assertIsNone(agg.flush())
        agg.add_frame(np.asarray([3.0, 5.0]))
        agg.reset()
        self.assertEqual(len(agg.rolling_buf), 0)
        self.assertIsNone(agg.rolling_sum)


if __name__ == "__main__":
    unittest.main()
