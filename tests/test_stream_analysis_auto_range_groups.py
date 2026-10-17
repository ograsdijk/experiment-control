# ruff: noqa: E402
"""Auto-range bin_stats / bin_ratio_stats keep per-x running sums.

The expected values are computed the way the states used to: from the full
list of raw samples at emit time. Bins and counts must match exactly; sums
may differ in the last bit (the old bin_stats path used Python's
compensated sum()).
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.stream_analysis import (
    BinRatioStatsState,
    BinStatsState,
)


def _samples(unique_x: int, n: int, seed: int = 0):
    rng = np.random.default_rng(seed)
    grid = np.linspace(-3.0, 7.0, unique_x)
    xs = rng.choice(grid, size=n)
    ys = rng.normal(5.0, 2.0, size=n)
    ds = rng.normal(10.0, 1.0, size=n)
    return xs, ys, ds


def _reference_groups(xs, columns):
    """Old unique-value mode: group raw samples by x, sum in arrival order."""
    grouped: dict[str, tuple[float, list[list[float]]]] = {}
    for i, x in enumerate(xs):
        key = format(float(x), ".15g")
        entry = grouped.setdefault(key, (float(x), [[] for _ in columns]))
        for column, values in zip(entry[1], columns, strict=True):
            column.append(float(values[i]))
    items = sorted(grouped.values(), key=lambda item: item[0])
    centers = [item[0] for item in items]
    counts = [len(item[1][0]) for item in items]
    sums = [[float(sum(column)) for column in item[1]] for item in items]
    return centers, counts, sums


def _reference_binned(xs, columns, bins):
    """Old binned mode: bin every raw sample over the sample range."""
    lo, hi = float(np.min(xs)), float(np.max(xs))
    frac = np.clip((xs - lo) / (hi - lo), 0.0, 1.0)
    idx = np.minimum(np.floor(frac * bins).astype(np.int64), bins - 1)
    counts = np.bincount(idx, minlength=bins)
    sums = [np.bincount(idx, weights=column, minlength=bins) for column in columns]
    edges = np.linspace(lo, hi, bins + 1)
    return (edges[:-1] + edges[1:]) * 0.5, counts, sums


class BinStatsAutoRangeGroupsTests(unittest.TestCase):
    def _state(self, xs, ys, bin_count):
        state = BinStatsState.from_params({"auto_range": True, "bin_count": bin_count})
        for x, y in zip(xs, ys, strict=True):
            state.update_sample(float(x), float(y))
        return state.payload(last_sample=None)

    def test_unique_value_mode_matches_raw_samples(self) -> None:
        xs, ys, _ = _samples(unique_x=40, n=5000)
        payload = self._state(xs, ys, bin_count=100)
        centers, counts, sums = _reference_groups(xs, [ys, ys * ys])
        self.assertEqual(payload["x_bins"], centers)
        self.assertEqual(payload["count"], counts)
        means = [s[0] / c for s, c in zip(sums, counts, strict=True)]
        np.testing.assert_allclose(payload["mean"], means, rtol=1e-12)
        self.assertEqual(payload["x_min"], float(np.min(xs)))
        self.assertEqual(payload["x_max"], float(np.max(xs)))

    def test_binned_mode_matches_raw_samples(self) -> None:
        xs, ys, _ = _samples(unique_x=500, n=5000)
        payload = self._state(xs, ys, bin_count=37)
        centers, counts, sums = _reference_binned(xs, [ys], bins=37)
        np.testing.assert_allclose(payload["x_bins"], centers)
        self.assertEqual(payload["count"], counts.tolist())
        with np.errstate(invalid="ignore", divide="ignore"):
            expected_mean = sums[0] / counts
        np.testing.assert_allclose(
            np.asarray(payload["mean"], dtype=float), expected_mean, rtol=1e-12
        )

    def test_reset_clears_groups(self) -> None:
        state = BinStatsState.from_params({"auto_range": True, "bin_count": 10})
        state.update_sample(1.0, 2.0)
        state.reset()
        payload = state.payload(last_sample=None)
        self.assertEqual(payload["count"], [])
        state.update_sample(3.0, 4.0)
        self.assertEqual(state.payload(last_sample=None)["x_bins"], [3.0])


class BinRatioStatsAutoRangeGroupsTests(unittest.TestCase):
    def _state(self, xs, ns, ds, bin_count):
        state = BinRatioStatsState.from_params(
            {"auto_range": True, "bin_count": bin_count}
        )
        for x, n, d in zip(xs, ns, ds, strict=True):
            state.update_sample(float(x), float(n), float(d))
        return state.payload(last_sample=None)

    def test_unique_value_mode_matches_raw_samples(self) -> None:
        xs, ns, ds = _samples(unique_x=25, n=4000)
        payload = self._state(xs, ns, ds, bin_count=100)
        centers, counts, sums = _reference_groups(
            xs, [ns, ds, ns * ns, ds * ds, ns * ds]
        )
        self.assertEqual(payload["x_bins"], centers)
        self.assertEqual(payload["count"], counts)
        np.testing.assert_allclose(
            payload["numerator_mean"],
            [s[0] / c for s, c in zip(sums, counts, strict=True)],
            rtol=1e-12,
        )
        np.testing.assert_allclose(
            payload["denominator_mean"],
            [s[1] / c for s, c in zip(sums, counts, strict=True)],
            rtol=1e-12,
        )

    def test_binned_mode_matches_raw_samples(self) -> None:
        xs, ns, ds = _samples(unique_x=400, n=4000)
        payload = self._state(xs, ns, ds, bin_count=50)
        centers, counts, sums = _reference_binned(xs, [ns, ds, ns * ds], bins=50)
        np.testing.assert_allclose(payload["x_bins"], centers)
        self.assertEqual(payload["count"], counts.tolist())
        np.testing.assert_allclose(
            np.asarray(payload["mean"], dtype=float), sums[0] / sums[1], rtol=1e-12
        )
        np.testing.assert_allclose(
            np.asarray(payload["covariance"], dtype=float),
            sums[2] / counts - (sums[0] / counts) * (sums[1] / counts),
            rtol=1e-9,
            atol=1e-12,
        )

    def test_reset_clears_groups(self) -> None:
        state = BinRatioStatsState.from_params({"auto_range": True, "bin_count": 10})
        state.update_sample(1.0, 2.0, 4.0)
        state.reset()
        self.assertEqual(state.payload(last_sample=None)["count"], [])


if __name__ == "__main__":
    unittest.main()
