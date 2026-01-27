# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ranges import generate_from_gen


class SequencerRangeGeneratorTests(unittest.TestCase):
    def test_triangle_low_to_high_has_2n_points(self) -> None:
        values = generate_from_gen(
            {"triangle": {"start": 0.0, "stop": 4.0, "num": 3}},
            env={},
        )
        self.assertEqual(values, [0.0, 2.0, 4.0, 4.0, 2.0, 0.0])
        self.assertEqual(len(values), 6)
        self.assertEqual(values[0], values[-1])

    def test_triangle_high_to_low_has_2n_points(self) -> None:
        values = generate_from_gen(
            {"triangle": {"start": 4.0, "stop": 0.0, "num": 3}},
            env={},
        )
        self.assertEqual(values, [4.0, 2.0, 0.0, 0.0, 2.0, 4.0])
        self.assertEqual(len(values), 6)
        self.assertEqual(values[0], values[-1])

    def test_triangle_shuffle_supported(self) -> None:
        spec = {
            "triangle": {"start": 0.0, "stop": 3.0, "num": 4},
            "shuffle": True,
            "seed": 123,
        }
        a = generate_from_gen(spec, env={})
        b = generate_from_gen(spec, env={})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 8)

    def test_triangle_num_must_be_at_least_two(self) -> None:
        with self.assertRaises(ValueError):
            generate_from_gen({"triangle": {"start": 0, "stop": 1, "num": 1}}, env={})


if __name__ == "__main__":
    unittest.main()
