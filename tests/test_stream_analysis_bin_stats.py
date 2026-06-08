# ruff: noqa: E402

import math
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.stream_analysis import BinStatsState, compile_workspace_graph


class BinStatsStateTests(unittest.TestCase):
    def test_source_records_workspace_accepts_node_id_alias(self) -> None:
        compiled = compile_workspace_graph(
            {
                "workspace_id": "wavemeter_channel_frequency",
                "enabled": True,
                "graph": {
                    "nodes": [
                        {
                            "node_id": "records",
                            "op": "source.records",
                            "params": {
                                "device_id": "hf_wavemeter",
                                "stream": "frequency_records",
                            },
                        },
                        {
                            "node_id": "channel",
                            "op": "record.field",
                            "inputs": {"record": "records"},
                            "params": {"field": "channel"},
                        },
                        {
                            "node_id": "frequency",
                            "op": "record.field",
                            "inputs": {"record": "records"},
                            "params": {"field": "frequency_hz"},
                        },
                        {
                            "node_id": "stats",
                            "op": "aggregate.bin_stats",
                            "inputs": {"x": "channel", "y": "frequency"},
                            "params": {"bin_count": 4, "x_min": 0.5, "x_max": 4.5},
                        },
                    ]
                },
                "publish": {
                    "outputs": [
                        {"output_id": "frequency_by_channel", "node_id": "stats"}
                    ]
                },
            }
        )

        self.assertEqual(compiled.stream_key, ("hf_wavemeter", "frequency_records"))
        self.assertEqual(compiled.order, ["records", "channel", "frequency", "stats"])

    def test_auto_range_keeps_distinct_x_buckets_while_sparse(self) -> None:
        state = BinStatsState.from_params({"auto_range": True, "bin_count": 4})
        for x_value, y_value in [
            (0.0, 10.0),
            (0.1, 20.0),
            (0.2, 30.0),
            (1.0, 40.0),
        ]:
            state.update_sample(x_value, y_value)

        payload = state.payload(last_sample=None)

        self.assertEqual(payload["active_bin_count"], 4)
        self.assertEqual(payload["populated_bin_count"], 4)
        self.assertEqual(payload["count"], [1, 1, 1, 1])
        self.assertEqual(payload["x_bins"], [0.0, 0.1, 0.2, 1.0])
        self.assertEqual(payload["mean"], [10.0, 20.0, 30.0, 40.0])
        self.assertEqual(payload["std"], [0.0, 0.0, 0.0, 0.0])
        # SEM is undefined for n=1 (single sample has no spread). The
        # payload returns NaN (sanitized to None in the JSON wire
        # payload via _sanitize_json) so downstream consumers (UI error
        # bars, fit weights) can render n/a instead of a falsely-perfect 0.
        sem = payload["sem"]
        self.assertEqual(len(sem), 4)
        for value in sem:
            self.assertTrue(
                value is None or (isinstance(value, float) and math.isnan(value)),
                f"expected None or NaN, got {value!r}",
            )

    def test_auto_range_groups_repeats_at_same_x(self) -> None:
        state = BinStatsState.from_params({"auto_range": True, "bin_count": 5})
        for x_value, y_value in [
            (1.0, 10.0),
            (1.0, 14.0),
            (2.0, 30.0),
            (2.0, 34.0),
            (2.0, 38.0),
        ]:
            state.update_sample(x_value, y_value)

        payload = state.payload(last_sample=None)

        self.assertEqual(payload["active_bin_count"], 2)
        self.assertEqual(payload["x_bins"], [1.0, 2.0])
        self.assertEqual(payload["count"], [2, 3])
        self.assertEqual(payload["mean"], [12.0, 34.0])
        std = payload["std"]
        self.assertEqual(len(std), 2)
        self.assertAlmostEqual(std[0], 2.0)
        self.assertAlmostEqual(std[1], 3.265986323710904)

    def test_auto_range_falls_back_to_equal_width_bins_after_threshold(self) -> None:
        state = BinStatsState.from_params({"auto_range": True, "bin_count": 4})
        for x_value in [0.0, 0.1, 0.2, 0.3, 1.0]:
            state.update_sample(x_value, x_value)

        payload = state.payload(last_sample=None)

        self.assertEqual(payload["active_bin_count"], 4)
        self.assertEqual(sum(payload["count"]), 5)
        self.assertNotEqual(payload["x_bins"], [0.0, 0.1, 0.2, 0.3, 1.0])


if __name__ == "__main__":
    unittest.main()
