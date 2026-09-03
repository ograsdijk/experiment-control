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
    BinRatioStatsState,
    OPS,
    OP_PARAM_SCHEMAS,
    StreamAnalysisProcess,
    WorkspaceRuntime,
    compile_workspace_graph,
    execute_hist_divide,
    execute_scalar_threshold,
    execute_trace_crop,
    execute_trace_divide,
    execute_trace_integrate,
    execute_trace_scalar_math,
    execute_trace_scale,
    execute_trace_subtract_background,
    execute_trace_window_mean,
)


class TraceWindowMeanTests(unittest.TestCase):
    def test_registered(self) -> None:
        self.assertIn("trace.window_mean", OPS)
        self.assertEqual(OPS["trace.window_mean"].input_types, {"trace": "trace"})
        self.assertEqual(OPS["trace.window_mean"].output_type, "scalar")
        self.assertFalse(OPS["trace.window_mean"].stateful)
        self.assertIn("trace.window_mean", OP_PARAM_SCHEMAS)

    def test_window_mean(self) -> None:
        trace = np.asarray([1.0, 2.0, 4.0, 8.0], dtype=np.float64)
        self.assertAlmostEqual(
            execute_trace_window_mean(trace, {"start_idx": 1, "stop_idx": 3}),
            3.0,
        )

    def test_window_mean_requires_explicit_bounds(self) -> None:
        trace = np.asarray([1.0, 2.0, 4.0, 8.0], dtype=np.float64)
        with self.assertRaisesRegex(ValueError, "requires start_idx and stop_idx"):
            execute_trace_window_mean(trace, {"start_idx": 1})

    def test_trace_divide_preserves_invalid_denominator_samples(self) -> None:
        numerator = np.asarray([2.0, 4.0, 6.0], dtype=np.float64)
        denominator = np.asarray([2.0, 0.0, np.nan], dtype=np.float64)
        divided = execute_trace_divide(numerator, denominator)
        self.assertIsNotNone(divided)
        assert divided is not None
        self.assertAlmostEqual(divided[0], 1.0)
        self.assertTrue(np.isnan(divided[1]))
        self.assertTrue(np.isnan(divided[2]))

    def test_trace_divide_scalar_zero_invalidates_trace(self) -> None:
        trace = np.asarray([1.0, 2.0], dtype=np.float64)
        self.assertIsNone(execute_trace_scalar_math(trace, 0.0, op="divide"))

    def test_background_subtraction_ignores_isolated_nonfinite_samples(self) -> None:
        trace = np.asarray([2.0, np.nan, 4.0, 10.0], dtype=np.float64)
        out = execute_trace_subtract_background(
            trace, {"bg_start_idx": 0, "bg_stop_idx": 3}
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertAlmostEqual(out[0], -1.0)
        self.assertTrue(np.isnan(out[1]))
        self.assertAlmostEqual(out[2], 1.0)

    def test_fractional_absorption_pipeline(self) -> None:
        transmission_ratio = np.full(4096, 0.8, dtype=np.float64)
        transmission_ratio[10:1900] = 0.76  # 5% absorption relative to the tail.

        tail = execute_trace_window_mean(
            transmission_ratio,
            {"start_idx": 3096, "stop_idx": 4096},
        )
        self.assertAlmostEqual(tail, 0.8)

        normalized = execute_trace_scalar_math(
            transmission_ratio,
            tail,
            op="divide",
        )
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertAlmostEqual(float(np.mean(normalized[3096:4096])), 1.0)

        baseline_subtracted = execute_trace_subtract_background(
            normalized,
            {"bg_start_idx": 3096, "bg_stop_idx": 4096},
        )
        fractional = execute_trace_scale(baseline_subtracted, {"factor": -1.0})
        self.assertIsNotNone(fractional)
        assert fractional is not None
        self.assertAlmostEqual(float(np.mean(fractional[10:1900])), 0.05)

        window = execute_trace_crop(
            fractional,
            {"start_idx": 10, "stop_idx": 1900},
        )
        integral = execute_trace_integrate(window)
        self.assertAlmostEqual(integral, 0.05 * (1900 - 10), places=10)

    def test_fractional_absorption_ignores_bad_power_sample_in_tail(self) -> None:
        abs_pd = np.full(4096, 0.8, dtype=np.float64)
        abs_pd[10:1900] = 0.76
        power_pd = np.ones(4096, dtype=np.float64)
        power_pd[3500] = 0.0

        ratio = execute_trace_divide(abs_pd, power_pd)
        self.assertIsNotNone(ratio)
        assert ratio is not None
        self.assertTrue(np.isnan(ratio[3500]))

        tail = execute_trace_window_mean(
            ratio, {"start_idx": 3096, "stop_idx": 4096}
        )
        self.assertAlmostEqual(tail, 0.8)
        normalized = execute_trace_scalar_math(ratio, tail, op="divide")
        self.assertIsNotNone(normalized)
        assert normalized is not None
        background_subtracted = execute_trace_subtract_background(
            normalized, {"bg_start_idx": 3096, "bg_stop_idx": 4096}
        )
        fractional = execute_trace_scale(background_subtracted, {"factor": -1.0})
        window = execute_trace_crop(
            fractional, {"start_idx": 10, "stop_idx": 1900}
        )
        integral = execute_trace_integrate(window)
        self.assertAlmostEqual(integral, 0.05 * (1900 - 10), places=10)


class ExplicitGateTests(unittest.TestCase):
    def test_threshold_invalid_input_stays_invalid(self) -> None:
        self.assertIsNone(
            execute_scalar_threshold(np.nan, threshold=2.0, mode="gte")
        )

    def test_connected_invalid_gate_fails_closed_for_bin_stats(self) -> None:
        config = {
            "workspace_id": "invalid_gate",
            "graph": {
                "nodes": [
                    {
                        "node_id": "src",
                        "op": "source.stream",
                        "params": {"device_id": "dev", "stream": "trace"},
                    },
                    {
                        "node_id": "x",
                        "op": "source.context_field",
                        "params": {"field": "scan_value"},
                    },
                    {
                        "node_id": "y",
                        "op": "trace.integrate",
                        "inputs": {"trace": "src"},
                    },
                    {
                        "node_id": "invalid",
                        "op": "scalar.divide",
                        "inputs": {"a": "y", "b": "zero"},
                    },
                    {
                        "node_id": "zero",
                        "op": "scalar.subtract",
                        "inputs": {"a": "y", "b": "y"},
                    },
                    {
                        "node_id": "gate",
                        "op": "scalar.threshold",
                        "inputs": {"x": "invalid"},
                        "params": {"threshold": 0.0, "mode": "gte"},
                    },
                    {
                        "node_id": "hist",
                        "op": "aggregate.bin_stats",
                        "inputs": {"x": "x", "y": "y", "gate": "gate"},
                        "params": {
                            "auto_range": True,
                            "x_min": 0.0,
                            "x_max": 1.0,
                            "bin_count": 10,
                        },
                    },
                ]
            },
            "publish": {
                "outputs": [{"output_id": "hist", "node_id": "hist"}]
            },
        }
        compiled = compile_workspace_graph(config)
        from experiment_control.processes.stream_analysis import (
            StreamAnalysisProcess,
            WorkspaceRuntime,
        )

        proc = StreamAnalysisProcess.__new__(StreamAnalysisProcess)
        proc._max_payload_points = 200_000
        runtime = WorkspaceRuntime(compiled=compiled, raw_config=config, node_state={})
        outputs = proc._execute_workspace_event(
            workspace=runtime,
            array=np.ones(4, dtype=np.float64),
            context_fields={"scan_value": 1.0},
            event_t_mono_s=0.0,
            include_hist_outputs=True,
            include_trace_outputs=False,
        )
        payload = {item["output_id"]: item["value"] for item in outputs}["hist"]
        self.assertEqual(payload["count"], [])


class PairedBinRatioStatsTests(unittest.TestCase):
    def test_registered(self) -> None:
        spec = OPS["aggregate.bin_ratio_stats"]
        self.assertEqual(
            spec.input_types,
            {"x": "scalar", "numerator": "scalar", "denominator": "scalar"},
        )
        self.assertEqual(spec.output_type, "hist_agg")
        self.assertTrue(spec.stateful)
        self.assertIn("aggregate.bin_ratio_stats", OP_PARAM_SCHEMAS)

    def test_perfectly_correlated_inputs_have_zero_ratio_spread(self) -> None:
        state = BinRatioStatsState.from_params({"auto_range": True, "bin_count": 10})
        state.update_sample(1.0, 2.0, 1.0)
        state.update_sample(1.0, 4.0, 2.0)
        payload = state.payload(last_sample=None)
        self.assertEqual(payload["mean"], [2.0])
        self.assertAlmostEqual(payload["covariance"][0], 0.5)
        self.assertAlmostEqual(payload["std"][0], 0.0)
        self.assertAlmostEqual(payload["sem"][0], 0.0)
        self.assertEqual(payload["uncertainty_assumption"], "paired_delta_method")

    def test_covariance_aware_delta_method(self) -> None:
        numerators = np.asarray([2.0, 5.0, 7.0])
        denominators = np.asarray([1.0, 2.0, 4.0])
        state = BinRatioStatsState.from_params(
            {"auto_range": False, "x_min": 0.0, "x_max": 2.0, "bin_count": 1}
        )
        for numerator, denominator in zip(numerators, denominators, strict=True):
            state.update_sample(1.0, numerator, denominator)
        payload = state.payload(last_sample=None)
        mean_n = float(np.mean(numerators))
        mean_d = float(np.mean(denominators))
        var_n = float(np.var(numerators))
        var_d = float(np.var(denominators))
        covariance = float(np.mean(numerators * denominators) - mean_n * mean_d)
        expected_var = (
            var_n / mean_d**2
            + mean_n**2 * var_d / mean_d**4
            - 2.0 * mean_n * covariance / mean_d**3
        )
        self.assertAlmostEqual(payload["mean"][0], mean_n / mean_d)
        self.assertAlmostEqual(payload["std"][0], np.sqrt(expected_var))
        self.assertAlmostEqual(payload["sem"][0], np.sqrt(expected_var / 3.0))

    def test_single_sample_sem_is_unavailable(self) -> None:
        state = BinRatioStatsState.from_params({"auto_range": True, "bin_count": 10})
        state.update_sample(1.0, 4.0, 2.0)
        payload = state.payload(last_sample=None)
        self.assertEqual(payload["mean"], [2.0])
        self.assertEqual(payload["count"], [1])
        self.assertEqual(payload["sem"], [None])

    def test_invalid_pair_is_dropped_together(self) -> None:
        state = BinRatioStatsState.from_params({"auto_range": True, "bin_count": 10})
        self.assertIsNone(state.update_sample(1.0, np.nan, 2.0))
        payload = state.payload(last_sample=None)
        self.assertEqual(payload["count"], [])
        self.assertEqual(payload["dropped_samples"], 1)

    def test_reset_clears_paired_samples(self) -> None:
        state = BinRatioStatsState.from_params({"auto_range": True, "bin_count": 10})
        state.update_sample(1.0, 4.0, 2.0)
        state.reset()
        payload = state.payload(last_sample=None)
        self.assertEqual(payload["count"], [])
        self.assertEqual(payload["mean"], [])
        self.assertEqual(payload["dropped_samples"], 0)

    def test_workspace_executes_paired_ratio_aggregate(self) -> None:
        config = {
            "workspace_id": "paired_ratio",
            "graph": {
                "nodes": [
                    {
                        "node_id": "src",
                        "op": "source.stream",
                        "params": {"device_id": "dev", "stream": "trace"},
                    },
                    {
                        "node_id": "x",
                        "op": "source.context_field",
                        "params": {"field": "scan_value"},
                    },
                    {
                        "node_id": "numerator",
                        "op": "trace.window_mean",
                        "inputs": {"trace": "src"},
                        "params": {"start_idx": 0, "stop_idx": 2},
                    },
                    {
                        "node_id": "denominator",
                        "op": "trace.window_mean",
                        "inputs": {"trace": "src"},
                        "params": {"start_idx": 2, "stop_idx": 4},
                    },
                    {
                        "node_id": "ratio",
                        "op": "aggregate.bin_ratio_stats",
                        "inputs": {
                            "x": "x",
                            "numerator": "numerator",
                            "denominator": "denominator",
                        },
                        "params": {"auto_range": True, "bin_count": 10},
                    },
                ]
            },
            "publish": {"outputs": [{"output_id": "ratio", "node_id": "ratio"}]},
        }
        compiled = compile_workspace_graph(config)
        proc = StreamAnalysisProcess.__new__(StreamAnalysisProcess)
        proc._max_payload_points = 200_000
        runtime = WorkspaceRuntime(compiled=compiled, raw_config=config, node_state={})
        outputs = proc._execute_workspace_event(
            workspace=runtime,
            array=np.asarray([4.0, 4.0, 2.0, 2.0]),
            context_fields={"scan_value": 1.0},
            event_t_mono_s=0.0,
            include_hist_outputs=True,
            include_trace_outputs=False,
        )
        ratio = {item["output_id"]: item["value"] for item in outputs}["ratio"]
        self.assertEqual(ratio["mean"], [2.0])
        self.assertEqual(ratio["count"], [1])


class HistogramDivideTests(unittest.TestCase):
    @staticmethod
    def hist(
        *,
        x: list[float],
        mean: list[float],
        std: list[float],
        sem: list[float],
        count: list[int],
    ) -> dict:
        return {
            "auto_range": True,
            "x_min": min(x),
            "x_max": max(x),
            "bin_count": 100,
            "active_bin_count": len(x),
            "max_bin_count": 100,
            "populated_bin_count": len(x),
            "x_bins": x,
            "count": count,
            "mean": mean,
            "std": std,
            "sem": sem,
            "dropped_samples": 0,
        }

    def test_registered(self) -> None:
        self.assertIn("hist.divide", OPS)
        self.assertEqual(
            OPS["hist.divide"].input_types,
            {"numerator": "hist_agg", "denominator": "hist_agg"},
        )
        self.assertEqual(OPS["hist.divide"].output_type, "hist_agg")
        self.assertFalse(OPS["hist.divide"].stateful)

    def test_ratio_of_bin_means(self) -> None:
        numerator = self.hist(
            x=[1681.0],
            mean=[15.0],
            std=[5.0],
            sem=[2.0],
            count=[2],
        )
        denominator = self.hist(
            x=[1681.0],
            mean=[2.5],
            std=[1.5],
            sem=[0.5],
            count=[2],
        )
        out = execute_hist_divide(numerator, denominator)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertAlmostEqual(out["mean"][0], 6.0)
        self.assertEqual(out["numerator_count"], [2])
        self.assertEqual(out["denominator_count"], [2])

    def test_different_source_counts_are_allowed(self) -> None:
        numerator = self.hist(
            x=[1.0],
            mean=[10.0],
            std=[1.0],
            sem=[0.1],
            count=[10],
        )
        denominator = self.hist(
            x=[1.0],
            mean=[2.0],
            std=[0.5],
            sem=[0.2],
            count=[7],
        )
        out = execute_hist_divide(numerator, denominator)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["count"], [7])
        self.assertEqual(out["numerator_count"], [10])
        self.assertEqual(out["denominator_count"], [7])
        self.assertFalse(out["require_equal_counts"])
        self.assertEqual(out["count_mismatch_bin_count"], 1)

    def test_strict_equal_counts_rejects_mismatch(self) -> None:
        numerator = self.hist(
            x=[1.0],
            mean=[10.0],
            std=[1.0],
            sem=[0.1],
            count=[10],
        )
        denominator = self.hist(
            x=[1.0],
            mean=[2.0],
            std=[0.5],
            sem=[0.2],
            count=[7],
        )
        out = execute_hist_divide(
            numerator, denominator, {"require_equal_counts": True}
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["count"], [0])
        self.assertEqual(out["mean"], [None])
        self.assertEqual(out["std"], [None])
        self.assertEqual(out["sem"], [None])
        self.assertEqual(out["populated_bin_count"], 0)
        self.assertEqual(out["numerator_count"], [10])
        self.assertEqual(out["denominator_count"], [7])
        self.assertTrue(out["require_equal_counts"])
        self.assertEqual(out["count_mismatch_bin_count"], 1)

    def test_strict_equal_counts_only_invalidates_mismatched_bins(self) -> None:
        numerator = self.hist(
            x=[1.0, 2.0],
            mean=[10.0, 12.0],
            std=[1.0, 1.0],
            sem=[0.1, 0.1],
            count=[10, 8],
        )
        denominator = self.hist(
            x=[1.0, 2.0],
            mean=[2.0, 3.0],
            std=[0.5, 0.5],
            sem=[0.2, 0.2],
            count=[10, 7],
        )
        out = execute_hist_divide(
            numerator, denominator, {"require_equal_counts": True}
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["count"], [10, 0])
        self.assertEqual(out["mean"], [5.0, None])
        self.assertIsNotNone(out["std"][0])
        self.assertIsNotNone(out["sem"][0])
        self.assertIsNone(out["std"][1])
        self.assertIsNone(out["sem"][1])
        self.assertEqual(out["populated_bin_count"], 1)
        self.assertEqual(out["count_mismatch_bin_count"], 1)

    def test_strict_auto_range_aligns_missing_x_bin(self) -> None:
        numerator = self.hist(
            x=[1.0],
            mean=[10.0],
            std=[1.0],
            sem=[0.1],
            count=[1],
        )
        denominator = self.hist(
            x=[1.0, 2.0],
            mean=[2.0, 3.0],
            std=[0.5, 0.5],
            sem=[0.2, 0.2],
            count=[1, 1],
        )
        out = execute_hist_divide(
            numerator, denominator, {"require_equal_counts": True}
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["x_bins"], [1.0, 2.0])
        self.assertEqual(out["numerator_count"], [1, 0])
        self.assertEqual(out["denominator_count"], [1, 1])
        self.assertEqual(out["count"], [1, 0])
        self.assertEqual(out["mean"], [5.0, None])
        self.assertEqual(out["std"][1], None)
        self.assertEqual(out["sem"][1], None)
        self.assertEqual(out["count_mismatch_bin_count"], 1)
        self.assertEqual(out["populated_bin_count"], 1)

    def test_non_strict_still_rejects_mismatched_x_bins(self) -> None:
        numerator = self.hist(
            x=[1.0], mean=[10.0], std=[1.0], sem=[0.1], count=[1]
        )
        denominator = self.hist(
            x=[1.0, 2.0],
            mean=[2.0, 3.0],
            std=[0.5, 0.5],
            sem=[0.2, 0.2],
            count=[1, 1],
        )
        self.assertIsNone(execute_hist_divide(numerator, denominator))

    def test_zero_denominator_marks_bin_unpopulated(self) -> None:
        numerator = self.hist(
            x=[1.0],
            mean=[10.0],
            std=[1.0],
            sem=[0.1],
            count=[10],
        )
        denominator = self.hist(
            x=[1.0],
            mean=[0.0],
            std=[0.5],
            sem=[0.2],
            count=[10],
        )
        out = execute_hist_divide(numerator, denominator)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["count"], [0])
        self.assertEqual(out["populated_bin_count"], 0)
        self.assertEqual(out["mean"], [None])

    def test_mismatched_x_bins_are_rejected(self) -> None:
        numerator = self.hist(
            x=[1.0],
            mean=[10.0],
            std=[1.0],
            sem=[0.1],
            count=[10],
        )
        denominator = self.hist(
            x=[2.0],
            mean=[2.0],
            std=[0.5],
            sem=[0.2],
            count=[10],
        )
        self.assertIsNone(execute_hist_divide(numerator, denominator))

    def test_compile_hist_divide_graph(self) -> None:
        config = {
            "workspace_id": "ratio_test",
            "graph": {
                "nodes": [
                    {
                        "node_id": "src",
                        "op": "source.stream",
                        "params": {"device_id": "dev", "stream": "trace"},
                    },
                    {
                        "node_id": "signal",
                        "op": "trace.integrate",
                        "inputs": {"trace": "src"},
                    },
                    {
                        "node_id": "x",
                        "op": "source.context_field",
                        "params": {"field": "scan_value"},
                    },
                    {
                        "node_id": "hist_a",
                        "op": "aggregate.bin_stats",
                        "inputs": {"x": "x", "y": "signal"},
                        "params": {
                            "auto_range": False,
                            "x_min": 0.0,
                            "x_max": 1.0,
                            "bin_count": 10,
                        },
                    },
                    {
                        "node_id": "hist_b",
                        "op": "aggregate.bin_stats",
                        "inputs": {"x": "x", "y": "signal"},
                        "params": {
                            "auto_range": False,
                            "x_min": 0.0,
                            "x_max": 1.0,
                            "bin_count": 10,
                        },
                    },
                    {
                        "node_id": "ratio",
                        "op": "hist.divide",
                        "inputs": {
                            "numerator": "hist_a",
                            "denominator": "hist_b",
                        },
                    },
                ]
            },
            "publish": {
                "outputs": [{"output_id": "ratio", "node_id": "ratio"}],
            },
        }
        compiled = compile_workspace_graph(config)
        self.assertEqual(compiled.node_output_types["ratio"], "hist_agg")

    def test_compile_window_mean_requires_bounds(self) -> None:
        config = {
            "workspace_id": "window_test",
            "graph": {
                "nodes": [
                    {
                        "node_id": "src",
                        "op": "source.stream",
                        "params": {"device_id": "dev", "stream": "trace"},
                    },
                    {
                        "node_id": "tail",
                        "op": "trace.window_mean",
                        "inputs": {"trace": "src"},
                        "params": {"start_idx": 1},
                    },
                ]
            },
            "publish": {
                "outputs": [{"output_id": "tail", "node_id": "tail"}],
            },
        }
        with self.assertRaisesRegex(ValueError, "requires start_idx and stop_idx"):
            compile_workspace_graph(config)


if __name__ == "__main__":
    unittest.main()
