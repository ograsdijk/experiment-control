"""`trace.block_average` -- non-overlapping averaging ahead of a fit.

The point of this op over `trace.rolling_mean` is that each input trace feeds
exactly one output, so consecutive fits are statistically independent. These
tests pin that property, not just the arithmetic.
"""

from __future__ import annotations

import unittest

import numpy as np

from experiment_control.processes.stream_analysis import (
    OP_PARAM_SCHEMAS,
    OPS,
    TraceBlockAverageState,
    TraceRollingMeanState,
)


class TestBlockAverageParams(unittest.TestCase):
    def test_registered_as_a_stateful_trace_op(self) -> None:
        spec = OPS["trace.block_average"]
        self.assertEqual(spec.input_types, {"trace": "trace"})
        self.assertEqual(spec.output_type, "trace")
        self.assertTrue(spec.stateful)

    def test_param_schema_declares_block_traces(self) -> None:
        names = [f["name"] for f in OP_PARAM_SCHEMAS["trace.block_average"]]
        self.assertEqual(names, ["block_traces"])

    def test_rejects_a_non_positive_block(self) -> None:
        for bad in (0, -1, None):
            with self.assertRaises(ValueError):
                TraceBlockAverageState.from_params({"block_traces": bad})


class TestBlockAverageBehaviour(unittest.TestCase):
    def _state(self, block: int) -> TraceBlockAverageState:
        return TraceBlockAverageState.from_params({"block_traces": block})

    def test_emits_nothing_until_the_first_block_is_full(self) -> None:
        # A partial average has the wrong noise level to fit against.
        state = self._state(4)
        for _ in range(3):
            self.assertIsNone(state.update(np.ones(8)))
        self.assertIsNotNone(state.update(np.ones(8)))

    def test_averages_exactly_the_traces_in_the_block(self) -> None:
        state = self._state(4)
        out = None
        for value in (1.0, 2.0, 3.0, 4.0):
            out = state.update(np.full(5, value))
        assert out is not None
        np.testing.assert_allclose(out, np.full(5, 2.5))

    def test_blocks_do_not_overlap(self) -> None:
        """The defining difference from rolling_mean: the second block must be
        free of the first block's traces."""
        state = self._state(2)
        state.update(np.full(3, 10.0))
        first = state.update(np.full(3, 10.0))
        state.update(np.full(3, 0.0))
        second = state.update(np.full(3, 0.0))
        assert first is not None and second is not None
        np.testing.assert_allclose(first, np.full(3, 10.0))
        np.testing.assert_allclose(second, np.zeros(3))

    def test_rolling_mean_would_have_overlapped(self) -> None:
        """Contrast case, so the two ops cannot silently converge."""
        rolling = TraceRollingMeanState.from_params({"window_traces": 2})
        for value in (10.0, 10.0, 0.0):
            out = rolling.update(np.full(3, value))
        assert out is not None
        np.testing.assert_allclose(out, np.full(3, 5.0))

    def test_holds_the_completed_average_between_blocks(self) -> None:
        # Panels stay steady and the fit result updates once per block.
        state = self._state(3)
        for _ in range(3):
            state.update(np.full(4, 6.0))
        held = state.update(np.full(4, 0.0))
        assert held is not None
        np.testing.assert_allclose(held, np.full(4, 6.0))
        self.assertEqual(state.blocks_completed, 1)

    def test_one_output_per_block_over_a_long_run(self) -> None:
        state = self._state(8)
        rng = np.random.default_rng(0)
        for _ in range(80):
            state.update(rng.normal(size=16))
        self.assertEqual(state.blocks_completed, 10)

    def test_block_of_one_is_a_pass_through(self) -> None:
        state = self._state(1)
        out = state.update(np.arange(4, dtype=np.float64))
        assert out is not None
        np.testing.assert_allclose(out, np.arange(4))

    def test_a_shape_change_discards_the_partial_block(self) -> None:
        """A reconfigured digitizer must not be spliced onto the old record."""
        state = self._state(4)
        state.update(np.full(8, 5.0))
        state.update(np.full(8, 5.0))
        self.assertIsNone(state.update(np.full(16, 1.0)))
        self.assertEqual(state.count, 1)
        out = None
        for _ in range(3):
            out = state.update(np.full(16, 1.0))
        assert out is not None
        np.testing.assert_allclose(out, np.ones(16))

    def test_a_non_trace_input_does_not_corrupt_the_block(self) -> None:
        state = self._state(3)
        state.update(np.full(4, 3.0))
        self.assertIsNone(state.update(None))
        self.assertEqual(state.count, 1)
        state.update(np.full(4, 3.0))
        out = state.update(np.full(4, 3.0))
        assert out is not None
        np.testing.assert_allclose(out, np.full(4, 3.0))

    def test_reset_clears_the_partial_block_and_the_held_average(self) -> None:
        state = self._state(2)
        state.update(np.ones(4))
        state.update(np.ones(4))
        state.update(np.ones(4))
        state.reset()
        self.assertIsNone(state.last_average)
        self.assertEqual(state.count, 0)
        self.assertEqual(state.blocks_completed, 0)
        self.assertIsNone(state.update(np.ones(4)))

    def test_averaging_reduces_noise_as_sqrt_n(self) -> None:
        """The reason to do this before a fit at all."""
        rng = np.random.default_rng(7)
        state = self._state(16)
        out = None
        for _ in range(16):
            out = state.update(rng.normal(scale=1.0, size=4096))
        assert out is not None
        self.assertLess(float(np.std(out)), 0.5)
        self.assertGreater(float(np.std(out)), 0.1)


if __name__ == "__main__":
    unittest.main()


class TestBlockAverageImprovesFitQuality(unittest.TestCase):
    """The motivating claim: averaging N traces before fitting tightens the
    recovered parameters. Uses the real PXIe geometry (100 kHz, 4096 samples)
    and the reciprocal-normal arrival model.
    """

    BLOCK = 8
    TAU0 = 2.0e-3
    R = 0.15

    def _truth(self) -> tuple[np.ndarray, np.ndarray]:
        from experiment_control.processes.stream_analysis_fit import (
            _model_reciprocal_normal,
        )

        x = 0.8e-3 + 1e-5 * np.arange(4096, dtype=np.float64)
        y = _model_reciprocal_normal(x, 1.0, self.TAU0, self.R)
        return x, y

    def _fit(self, x: np.ndarray, y: np.ndarray) -> dict[str, float] | None:
        from experiment_control.processes.stream_analysis_fit import _fit_curve_run

        out = _fit_curve_run(
            x_raw=x,
            y_raw=y,
            model="reciprocal_normal",
            baseline_mode="none",
            model_params={"t0_s": 0.0},
        )
        return None if out is None else out.get("params")

    def test_block_averaged_fit_beats_single_shot_fits(self) -> None:
        x, clean = self._truth()
        rng = np.random.default_rng(11)
        noise = 0.25

        single_err: list[float] = []
        state = TraceBlockAverageState.from_params({"block_traces": self.BLOCK})
        averaged: np.ndarray | None = None
        for _ in range(self.BLOCK):
            shot = clean + rng.normal(scale=noise, size=clean.size)
            averaged = state.update(shot)
            params = self._fit(x, shot)
            if params is not None:
                single_err.append(abs(params["tau0"] - self.TAU0) / self.TAU0)

        assert averaged is not None
        block_params = self._fit(x, averaged)
        assert block_params is not None
        block_err = abs(block_params["tau0"] - self.TAU0) / self.TAU0

        self.assertGreaterEqual(len(single_err), self.BLOCK // 2)
        self.assertLess(block_err, float(np.median(single_err)))
        self.assertLess(block_err, 0.02)

    def test_the_averaged_trace_still_fits_the_same_shape(self) -> None:
        """Averaging must not bias the parameters, only tighten them."""
        x, clean = self._truth()
        rng = np.random.default_rng(3)
        state = TraceBlockAverageState.from_params({"block_traces": self.BLOCK})
        averaged = None
        for _ in range(self.BLOCK):
            averaged = state.update(clean + rng.normal(scale=0.25, size=clean.size))
        assert averaged is not None
        params = self._fit(x, averaged)
        assert params is not None
        self.assertAlmostEqual(params["tau0"] / self.TAU0, 1.0, delta=0.02)
        self.assertAlmostEqual(params["r"] / self.R, 1.0, delta=0.05)


class TestFitSkipsFramesWithNoTrace(unittest.TestCase):
    """A block-averaging node emits nothing until its first block fills. Those
    frames must not be recorded as failed fits."""

    def _state(self):
        from experiment_control.processes.stream_analysis_fit import FitCurve1DState

        return FitCurve1DState.from_params(
            {"model": "gaussian", "baseline_mode": "none", "every_n": 1}
        )

    def test_a_none_trace_is_not_a_fit_attempt(self) -> None:
        from experiment_control.processes.stream_analysis_fit import (
            execute_fit_curve_1d,
        )

        state = self._state()
        for _ in range(7):
            out = execute_fit_curve_1d(
                state=state, x_raw=None, y_raw=None, gate_raw=None
            )
            self.assertIsNone(out)
        self.assertEqual(state.sample_count, 0)

    def test_a_real_trace_still_fits_on_the_first_frame_after_the_gap(self) -> None:
        from experiment_control.processes.stream_analysis_fit import (
            execute_fit_curve_1d,
        )

        state = self._state()
        for _ in range(7):
            execute_fit_curve_1d(state=state, x_raw=None, y_raw=None, gate_raw=None)
        x = np.linspace(-5.0, 5.0, 256)
        y = 3.0 * np.exp(-0.5 * (x / 1.2) ** 2)
        out = execute_fit_curve_1d(state=state, x_raw=x, y_raw=y, gate_raw=None)
        assert out is not None
        self.assertAlmostEqual(out["params"]["center"], 0.0, places=3)
        self.assertEqual(state.sample_count, 1)


class TestModelIsFiniteNearThePole(unittest.TestCase):
    def test_no_nan_for_a_vanishingly_small_tau(self) -> None:
        from experiment_control.processes.stream_analysis_fit import (
            _model_reciprocal_normal,
        )

        x = np.array([1e-300, 1e-200, 1e-162, 1e-30, 1e-5, 2e-3])
        out = _model_reciprocal_normal(x, 1.0, 2.0e-3, 0.15)
        self.assertFalse(bool(np.isnan(out).any()))
        self.assertTrue(bool(np.isfinite(out).all()))
        self.assertGreater(float(out[-1]), 0.0)
