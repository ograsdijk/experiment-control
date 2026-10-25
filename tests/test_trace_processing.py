# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils.trace_processing import (
    coerce_stream_values_array,
    coerce_trace_array,
    decimate_trace_values,
    normalize_shape,
    parse_channel_index,
    parse_csv_query_list,
    parse_trace_average_mode,
    parse_trace_decimator,
    parse_trace_max_fps,
    parse_trace_max_points,
    parse_trace_rolling_window,
    select_trace_from_array,
)
from experiment_control.utils.trace_processing import (
    _decimate_trace_m4,
    _decimate_trace_m4_loop,
    _decimate_trace_minmax,
    _decimate_trace_minmax_loop,
)


class TraceProcessingTests(unittest.TestCase):
    def test_parse_helpers(self) -> None:
        self.assertEqual(parse_trace_decimator("stride"), "stride")
        self.assertEqual(parse_trace_decimator("MEAN"), "mean")
        self.assertEqual(parse_trace_decimator("unknown"), "minmax")

        self.assertIsNone(parse_trace_max_points(""))
        self.assertIsNone(parse_trace_max_points("abc"))
        self.assertEqual(parse_trace_max_points("31"), 32)
        self.assertEqual(parse_trace_max_points("25000"), 20000)

        self.assertIsNone(parse_trace_max_fps(""))
        self.assertIsNone(parse_trace_max_fps("nan"))
        self.assertEqual(parse_trace_max_fps("0.2"), 0.5)
        self.assertEqual(parse_trace_max_fps("200"), 120.0)

        self.assertEqual(parse_trace_rolling_window(""), 1)
        self.assertEqual(parse_trace_rolling_window("0"), 1)
        self.assertEqual(parse_trace_rolling_window("300"), 200)

        self.assertEqual(parse_trace_average_mode("rolling"), "rolling")
        self.assertEqual(parse_trace_average_mode("block"), "block")
        self.assertEqual(parse_trace_average_mode("other"), "block")

        self.assertIsNone(parse_csv_query_list(""))
        self.assertEqual(parse_csv_query_list(" a, , b "), ["a", "b"])

        self.assertEqual(parse_channel_index(""), 0)
        self.assertEqual(parse_channel_index("-3"), 0)
        self.assertEqual(parse_channel_index("2.9"), 2)

    def test_normalize_shape(self) -> None:
        self.assertEqual(normalize_shape(None), [])
        self.assertEqual(normalize_shape([2, "3", 0, -1, "bad"]), [2, 3])

    def test_coerce_stream_values_array_and_select_trace(self) -> None:
        arr = coerce_stream_values_array([0, 1, 2, 3, 4, 5], [2, 3])
        self.assertIsNotNone(arr)
        assert arr is not None
        self.assertEqual(arr.shape, (2, 3))
        trace = select_trace_from_array(arr, channel_index=1)
        self.assertEqual(trace.tolist(), [3.0, 4.0, 5.0])

        arr2 = coerce_stream_values_array([0, 1, 2, 3, 4, 5], [3, 2])
        self.assertIsNotNone(arr2)
        assert arr2 is not None
        trace2 = select_trace_from_array(arr2, channel_index=1)
        self.assertEqual(trace2.tolist(), [1.0, 3.0, 5.0])

        self.assertIsNone(coerce_stream_values_array([1.0, float("inf")], [2]))

    def test_coerce_trace_array(self) -> None:
        arr = coerce_trace_array(np.asarray([1, 2, 3], dtype=np.float32))
        self.assertIsNotNone(arr)
        assert arr is not None
        self.assertEqual(arr.tolist(), [1.0, 2.0, 3.0])
        self.assertIsNone(coerce_trace_array([1.0, float("nan")]))

    def test_decimate_trace_values(self) -> None:
        values = list(range(10))
        stride = decimate_trace_values(values, mode="stride", max_points=4)
        self.assertEqual(stride, [0.0, 3.0, 6.0, 9.0])

        mean = decimate_trace_values(values, mode="mean", max_points=3)
        self.assertEqual(mean, [1.0, 4.0, 7.5])

        m4 = decimate_trace_values(values, mode="m4", max_points=6)
        self.assertLessEqual(len(m4), 6)
        self.assertGreater(len(m4), 0)

        minmax = decimate_trace_values(values, mode="minmax", max_points=4)
        self.assertEqual(minmax, [0.0, 4.0, 5.0, 9.0])

        passthrough = {"not": "an-array"}
        self.assertIs(decimate_trace_values(passthrough, mode="stride", max_points=4), passthrough)

    def test_vectorized_m4_and_minmax_match_the_loop_implementations(self) -> None:
        rng = np.random.default_rng(1234)
        cases: list[np.ndarray] = [
            np.zeros(97),
            np.arange(1000, dtype=np.float64),
            np.arange(1000, 0, -1, dtype=np.float64),
        ]
        for n in (5, 33, 64, 65, 257, 1000, 4096, 10007):
            cases.append(rng.normal(size=n))
            # Small integer values create many ties within each bucket.
            cases.append(rng.integers(-3, 4, size=n).astype(np.float64))
        for points in cases:
            for max_points in (1, 2, 3, 4, 5, 7, 8, 63, 64, 100, 512, 2000):
                if max_points >= points.size:
                    continue
                with self.subTest(n=points.size, max_points=max_points):
                    self.assertEqual(
                        _decimate_trace_m4(points, max_points),
                        _decimate_trace_m4_loop(points, max_points),
                    )
                    self.assertEqual(
                        _decimate_trace_minmax(points, max_points),
                        _decimate_trace_minmax_loop(points, max_points),
                    )

    def test_vectorized_decimators_fall_back_for_non_finite_input(self) -> None:
        points = np.array([1.0, np.nan, 3.0, -2.0, np.inf, 0.5, 4.0, 2.0])
        self.assertEqual(
            _decimate_trace_m4(points, 4), _decimate_trace_m4_loop(points, 4)
        )
        self.assertEqual(
            _decimate_trace_minmax(points, 4), _decimate_trace_minmax_loop(points, 4)
        )


if __name__ == "__main__":
    unittest.main()
