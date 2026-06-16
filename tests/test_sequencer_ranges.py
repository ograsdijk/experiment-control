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


class SequencerSampleModifierTests(unittest.TestCase):
    def _grid_spec(self) -> dict:
        # 5x5 = 25-point grid.
        return {
            "scan2d": {
                "center": {"x": 0.0, "y": 0.0},
                "size": {"width": 4.0, "height": 4.0},
                "steps": 5,
            }
        }

    def test_sample_count_with_replacement(self) -> None:
        spec = dict(self._grid_spec())
        spec["sample"] = {"count": 4, "replace": True, "seed": 7}
        records = generate_from_gen(spec, env={})
        self.assertEqual(len(records), 4)
        # x/y keys preserved from scan2d, index re-stamped 0..count-1.
        self.assertEqual([r["index"] for r in records], [0, 1, 2, 3])
        for r in records:
            self.assertIn("x", r)
            self.assertIn("y", r)
            self.assertEqual(r["count"], 4)

    def test_sample_with_replacement_may_exceed_population(self) -> None:
        spec = dict(self._grid_spec())
        spec["sample"] = {"count": 50, "replace": True, "seed": 1}
        records = generate_from_gen(spec, env={})
        self.assertEqual(len(records), 50)

    def test_sample_without_replacement_is_distinct(self) -> None:
        spec = dict(self._grid_spec())
        spec["sample"] = {"count": 10, "replace": False, "seed": 3}
        records = generate_from_gen(spec, env={})
        coords = [(r["x"], r["y"]) for r in records]
        self.assertEqual(len(coords), 10)
        self.assertEqual(len(set(coords)), 10)

    def test_sample_without_replacement_rejects_oversized_count(self) -> None:
        spec = dict(self._grid_spec())
        spec["sample"] = {"count": 100, "replace": False}
        with self.assertRaises(ValueError):
            generate_from_gen(spec, env={})

    def test_sample_seed_is_reproducible(self) -> None:
        spec = dict(self._grid_spec())
        spec["sample"] = {"count": 6, "replace": True, "seed": 42}
        a = generate_from_gen(dict(spec), env={})
        b = generate_from_gen(dict(spec), env={})
        self.assertEqual(
            [(r["x"], r["y"]) for r in a],
            [(r["x"], r["y"]) for r in b],
        )

    def test_sample_renders_templates_from_env(self) -> None:
        spec = dict(self._grid_spec())
        spec["sample"] = {"count": "${m}", "replace": True, "seed": "${s}"}
        records = generate_from_gen(spec, env={"m": 3, "s": 5})
        self.assertEqual(len(records), 3)

    def test_sample_on_scalar_values_generator(self) -> None:
        spec = {
            "values": [10, 20, 30, 40],
            "sample": {"count": 2, "replace": False, "seed": 0},
        }
        records = generate_from_gen(spec, env={})
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r["value"] in (10, 20, 30, 40) for r in records))


if __name__ == "__main__":
    unittest.main()
