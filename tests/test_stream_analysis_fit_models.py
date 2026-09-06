"""Fit model registry, and parameter recovery for each model.

Nothing previously asserted that a fit recovers known parameters, so the
gaussian and lorentzian cases here establish that baseline alongside the new
reciprocal-normal model.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from experiment_control.processes.stream_analysis_fit import (
    FIT_MODEL_CHOICES,
    FIT_MODELS,
    FitCurve1DState,
    _fit_curve_build_models,
    _fit_curve_initial_guess,
    _fit_curve_run,
    _model_reciprocal_normal,
    _normalize_fit_param_name,
    _parse_fit_model,
    _reciprocal_normal_tau_peak,
    execute_fit_curve_1d,
)

# Matches the state-preparation-b-detection PXIe: 100 kHz, 4096 samples,
# 0.8 ms trigger delay.
SAMPLE_RATE_HZ = 100_000.0
N_SAMPLES = 4096
X = 0.8e-3 + np.arange(N_SAMPLES) / SAMPLE_RATE_HZ

TRUE_AMP = 2.0
TRUE_TAU0 = 6.7e-3
TRUE_R = 0.2


def reciprocal_normal_trace(
    *,
    amp: float = TRUE_AMP,
    tau0: float = TRUE_TAU0,
    r: float = TRUE_R,
    t0_s: float = 0.0,
    noise: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    y = _model_reciprocal_normal(X, amp, tau0, r, t0_s=t0_s)
    if noise:
        y = y + np.random.default_rng(seed).normal(0.0, noise, N_SAMPLES)
    return y


class FitModelRegistryTests(unittest.TestCase):
    def test_registry_contains_the_three_models(self) -> None:
        self.assertEqual(
            sorted(FIT_MODELS), ["gaussian", "lorentzian", "reciprocal_normal"]
        )

    def test_parse_accepts_registered_names_and_normalizes(self) -> None:
        self.assertEqual(_parse_fit_model("  Reciprocal_Normal "), "reciprocal_normal")
        self.assertEqual(_parse_fit_model(None), "gaussian")

    def test_parse_rejects_unknown_model_listing_all_options(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _parse_fit_model("beam_arrival")
        message = str(ctx.exception)
        for name in FIT_MODELS:
            self.assertIn(name, message)

    def test_choices_payload_matches_registry(self) -> None:
        self.assertEqual(
            [choice["value"] for choice in FIT_MODEL_CHOICES], list(FIT_MODELS)
        )
        for choice in FIT_MODEL_CHOICES:
            self.assertTrue(str(choice["label"]).strip())

    def test_param_count_matrix_across_baselines(self) -> None:
        """Guards the baseline wrapper against hardcoding a core param count."""
        for model, spec in FIT_MODELS.items():
            consts = spec.parse_consts({})
            for baseline_mode, extra in (("none", 0), ("constant", 1), ("linear", 2)):
                fit_func, _eval, names = _fit_curve_build_models(
                    model=model,
                    baseline_mode=baseline_mode,
                    model_params=consts,
                )
                self.assertEqual(
                    len(names),
                    len(spec.param_names) + extra,
                    msg=f"{model}/{baseline_mode}",
                )
                out = fit_func(X[:64], *([1.0] * len(names)))
                self.assertEqual(np.asarray(out).shape, (64,))


class ReciprocalNormalShapeTests(unittest.TestCase):
    def test_peak_relation_is_exact(self) -> None:
        """The mode is earlier than tau0 because of the 1/tau^2 Jacobian."""
        for tau0, r in ((6.7e-3, 0.05), (6.7e-3, 0.2), (5.0e-3, 0.35)):
            tau = np.linspace(1e-5, 60e-3, 2_000_000)
            y = _model_reciprocal_normal(tau, 1.0, tau0, r)
            numeric_peak = float(tau[int(np.argmax(y))])
            self.assertAlmostEqual(
                numeric_peak, _reciprocal_normal_tau_peak(tau0, r), places=6
            )

    def test_peak_is_earlier_than_nominal_flight_time(self) -> None:
        self.assertLess(_reciprocal_normal_tau_peak(6.7e-3, 0.3), 6.7e-3)

    def test_normalized_to_unit_height_at_its_peak(self) -> None:
        tau = np.linspace(1e-5, 60e-3, 200_000)
        y = _model_reciprocal_normal(tau, 3.0, TRUE_TAU0, TRUE_R)
        self.assertAlmostEqual(float(np.max(y)), 3.0, places=4)

    def test_zero_before_t0_and_finite_everywhere(self) -> None:
        x = np.linspace(-1e-3, 20e-3, 1000)
        y = _model_reciprocal_normal(x, 1.0, TRUE_TAU0, TRUE_R, t0_s=2e-3)
        self.assertTrue(np.all(np.isfinite(y)))
        self.assertTrue(np.all(y[x <= 2e-3] == 0.0))


class ReciprocalNormalRecoveryTests(unittest.TestCase):
    def _fit(self, y: np.ndarray, *, baseline_mode: str = "none", t0_s: float = 0.0):
        result = _fit_curve_run(
            x_raw=X,
            y_raw=y,
            model="reciprocal_normal",
            baseline_mode=baseline_mode,
            model_params={"t0_s": t0_s},
        )
        self.assertIsNotNone(result)
        return result

    def test_noiseless_round_trip(self) -> None:
        params = self._fit(reciprocal_normal_trace())["params"]
        self.assertAlmostEqual(params["amplitude"], TRUE_AMP, places=6)
        self.assertAlmostEqual(params["tau0"], TRUE_TAU0, places=9)
        self.assertAlmostEqual(params["r"], TRUE_R, places=8)

    def test_noisy_round_trip_reports_finite_stderr(self) -> None:
        result = self._fit(reciprocal_normal_trace(noise=0.02))
        params, stderr = result["params"], result["stderr"]
        self.assertLess(abs(params["tau0"] - TRUE_TAU0) / TRUE_TAU0, 0.01)
        self.assertLess(abs(params["r"] - TRUE_R) / TRUE_R, 0.02)
        for name in ("amplitude", "tau0", "r"):
            self.assertIn(name, stderr)
            self.assertTrue(math.isfinite(stderr[name]))
            self.assertGreater(stderr[name], 0.0)

    def test_recovery_across_the_physical_r_range(self) -> None:
        for r_true in (0.05, 0.1, 0.2, 0.35):
            y = reciprocal_normal_trace(r=r_true, noise=0.02, seed=3)
            params = self._fit(y)["params"]
            self.assertLess(abs(params["tau0"] - TRUE_TAU0) / TRUE_TAU0, 0.01)
            self.assertLess(abs(params["r"] - r_true) / r_true, 0.05)

    def test_constant_and_linear_baselines(self) -> None:
        base = reciprocal_normal_trace(noise=0.02)
        for baseline_mode, offset in (
            ("constant", 0.2),
            ("linear", 0.2 + 3.0 * X),
        ):
            params = self._fit(base + offset, baseline_mode=baseline_mode)["params"]
            self.assertLess(abs(params["tau0"] - TRUE_TAU0) / TRUE_TAU0, 0.01)
            self.assertLess(abs(params["r"] - TRUE_R) / TRUE_R, 0.05)

    def test_bounds_keep_shape_parameters_physical(self) -> None:
        params = self._fit(reciprocal_normal_trace(noise=0.05, seed=7))["params"]
        self.assertGreater(params["r"], 0.0)
        self.assertGreater(params["tau0"], 0.0)

    def test_t0_s_is_honoured_and_echoed(self) -> None:
        y = reciprocal_normal_trace(t0_s=1.5e-3)
        params = self._fit(y, t0_s=1.5e-3)["params"]
        self.assertAlmostEqual(params["tau0"], TRUE_TAU0, places=8)
        self.assertAlmostEqual(params["t0_s"], 1.5e-3)

    def test_wrong_t0_s_biases_tau0(self) -> None:
        """t0 is not fitted, so a wrong constant shows up as a shifted tau0."""
        y = reciprocal_normal_trace(t0_s=1.5e-3)
        params = self._fit(y, t0_s=0.0)["params"]
        self.assertGreater(abs(params["tau0"] - TRUE_TAU0) / TRUE_TAU0, 0.1)

    def test_t0_is_degenerate_against_the_width(self) -> None:
        """Why t0 is a constant and not a fit parameter.

        With t0 free, it is almost perfectly anti-correlated with tau0, which
        is what destroys the precision of the velocity determination. If this
        ever stops holding, revisit fixing t0 -- but do not promote it to a fit
        parameter without re-measuring this.
        """
        from scipy.optimize import curve_fit

        def free_t0(x, amp, t0, tau0, r):
            return _model_reciprocal_normal(x, amp, tau0, r, t0_s=t0)

        y = reciprocal_normal_trace(r=0.05, noise=0.02, seed=11)
        popt, pcov = curve_fit(
            free_t0, X, y, p0=[TRUE_AMP, 0.0, TRUE_TAU0, 0.05], maxfev=40000
        )
        sd = np.sqrt(np.diag(pcov))
        corr = pcov[1, 2] / (sd[1] * sd[2])
        self.assertGreater(abs(corr), 0.97)


class PeakModelRegressionTests(unittest.TestCase):
    """Gaussian and lorentzian must be unchanged by the registry refactor."""

    def setUp(self) -> None:
        self.x = np.linspace(-5.0, 5.0, 400)

    def test_gaussian_recovers_known_parameters(self) -> None:
        y = 3.0 * np.exp(-0.5 * ((self.x - 0.7) / 1.3) ** 2)
        params = _fit_curve_run(
            x_raw=self.x, y_raw=y, model="gaussian", baseline_mode="none"
        )["params"]
        self.assertAlmostEqual(params["amplitude"], 3.0, places=6)
        self.assertAlmostEqual(params["center"], 0.7, places=6)
        self.assertAlmostEqual(abs(params["sigma"]), 1.3, places=6)

    def test_lorentzian_recovers_known_parameters(self) -> None:
        y = 3.0 * (1.1**2) / ((self.x - 0.4) ** 2 + 1.1**2)
        params = _fit_curve_run(
            x_raw=self.x, y_raw=y, model="lorentzian", baseline_mode="none"
        )["params"]
        self.assertAlmostEqual(params["amplitude"], 3.0, places=6)
        self.assertAlmostEqual(params["center"], 0.4, places=6)
        self.assertAlmostEqual(abs(params["gamma"]), 1.1, places=6)

    def test_initial_guess_matches_the_pre_refactor_formula(self) -> None:
        y = 3.0 * np.exp(-0.5 * ((self.x - 0.7) / 1.3) ** 2)
        span = float(np.max(self.x)) - float(np.min(self.x))
        for model, divisor in (("gaussian", 8.0), ("lorentzian", 10.0)):
            guess = _fit_curve_initial_guess(
                x=self.x, y=y, model=model, baseline_mode="none"
            )
            expected = np.asarray(
                [
                    float(np.max(y)) - float(np.median(y)),
                    float(self.x[int(np.argmax(y))]),
                    span / divisor,
                ]
            )
            np.testing.assert_array_equal(guess, expected)

    def test_baseline_guess_tail_is_appended(self) -> None:
        y = 3.0 * np.exp(-0.5 * ((self.x - 0.7) / 1.3) ** 2)
        core = _fit_curve_initial_guess(
            x=self.x, y=y, model="gaussian", baseline_mode="none"
        )
        constant = _fit_curve_initial_guess(
            x=self.x, y=y, model="gaussian", baseline_mode="constant"
        )
        linear = _fit_curve_initial_guess(
            x=self.x, y=y, model="gaussian", baseline_mode="linear"
        )
        np.testing.assert_array_equal(constant[:3], core)
        np.testing.assert_array_equal(linear[:3], core)
        self.assertEqual(len(constant), 4)
        self.assertEqual(len(linear), 5)
        self.assertAlmostEqual(constant[3], float(np.median(y)))


class FitStateTests(unittest.TestCase):
    def test_model_constants_are_parsed_onto_the_state(self) -> None:
        state = FitCurve1DState.from_params(
            {"model": "reciprocal_normal", "t0_s": 1e-3}
        )
        self.assertEqual(state.model_params, {"t0_s": 1e-3})

    def test_t0_s_defaults_to_zero(self) -> None:
        state = FitCurve1DState.from_params({"model": "reciprocal_normal"})
        self.assertEqual(state.model_params, {"t0_s": 0.0})

    def test_models_without_constants_get_an_empty_mapping(self) -> None:
        self.assertEqual(
            FitCurve1DState.from_params({"model": "gaussian"}).model_params, {}
        )

    def test_non_finite_t0_s_is_rejected(self) -> None:
        for bad in (float("nan"), float("inf"), "soon"):
            with self.assertRaises(ValueError):
                FitCurve1DState.from_params(
                    {"model": "reciprocal_normal", "t0_s": bad}
                )

    def test_execute_fit_curve_1d_uses_the_state_constants(self) -> None:
        state = FitCurve1DState.from_params(
            {"model": "reciprocal_normal", "t0_s": 1.5e-3}
        )
        out = execute_fit_curve_1d(
            state=state,
            x_raw=X,
            y_raw=reciprocal_normal_trace(t0_s=1.5e-3),
            gate_raw=None,
        )
        self.assertIsNotNone(out)
        self.assertAlmostEqual(out["params"]["tau0"], TRUE_TAU0, places=8)


class ParamAliasTests(unittest.TestCase):
    def test_dead_width_and_fwhm_aliases_are_gone(self) -> None:
        """They mapped to keys no model produces, implying false support."""
        self.assertEqual(_normalize_fit_param_name("mu"), "center")
        self.assertEqual(_normalize_fit_param_name("offset"), "baseline_const")
        # Unknown names still pass through unchanged rather than raising.
        self.assertEqual(_normalize_fit_param_name("fwhm"), "fwhm")
        self.assertEqual(_normalize_fit_param_name("tau0"), "tau0")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
