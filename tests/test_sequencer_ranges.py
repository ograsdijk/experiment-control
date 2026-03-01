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
        records = generate_from_gen(
            {"triangle": {"start": 0.0, "stop": 4.0, "num": 3}},
            env={},
        )
        values = [record["value"] for record in records]
        self.assertEqual(values, [0.0, 2.0, 4.0, 4.0, 2.0, 0.0])
        self.assertEqual(len(records), 6)
        self.assertEqual(records[0]["index"], 0)
        self.assertEqual(records[-1]["index"], 5)
        self.assertEqual(values[0], values[-1])

    def test_triangle_high_to_low_has_2n_points(self) -> None:
        records = generate_from_gen(
            {"triangle": {"start": 4.0, "stop": 0.0, "num": 3}},
            env={},
        )
        values = [record["value"] for record in records]
        self.assertEqual(values, [4.0, 2.0, 0.0, 0.0, 2.0, 4.0])
        self.assertEqual(len(records), 6)
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

    def test_scan2d_serpentine_shorthand(self) -> None:
        records = generate_from_gen(
            {
                "scan2d": {
                    "center": {"x": 0.0, "y": 0.0},
                    "width": 2.0,
                    "height": 2.0,
                    "steps": {"x": 3, "y": 2},
                    "pattern": "serpentine",
                }
            },
            env={},
        )
        coords = [(record["row"], record["col"]) for record in records]
        self.assertEqual(coords, [(0, 0), (0, 1), (0, 2), (1, 2), (1, 1), (1, 0)])
        self.assertEqual(records[0]["x"], -1.0)
        self.assertEqual(records[0]["y"], -1.0)
        self.assertEqual(records[-1]["x"], -1.0)
        self.assertEqual(records[-1]["y"], 1.0)

    def test_scan2d_random_with_seed_is_reproducible(self) -> None:
        spec = {
            "scan2d": {
                "center": {"x": 0.0, "y": 0.0},
                "size": 1.0,
                "steps": 3,
                "pattern": "random",
                "seed": 7,
            }
        }
        a = generate_from_gen(spec, env={})
        b = generate_from_gen(spec, env={})
        self.assertEqual(a, b)

    def test_scan2d_pitch_is_supported(self) -> None:
        records = generate_from_gen(
            {
                "scan2d": {
                    "center": {"x": 0.0, "y": 0.0},
                    "width": 2.0,
                    "height": 1.0,
                    "pitch": {"x": 1.0, "y": 0.5},
                }
            },
            env={},
        )
        self.assertEqual(len(records), 9)


if __name__ == "__main__":
    unittest.main()
