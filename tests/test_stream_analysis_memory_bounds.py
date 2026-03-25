# ruff: noqa: E402

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.stream_analysis import (
    StreamAnalysisProcess,
    compile_workspace_graph,
)


def _make_process_for_bounds() -> StreamAnalysisProcess:
    proc = StreamAnalysisProcess.__new__(StreamAnalysisProcess)
    proc._context_by_seq = {}  # noqa: SLF001
    proc._context_cache_limit = 64  # noqa: SLF001
    proc._telemetry_history = {}  # noqa: SLF001
    proc._telemetry_history_max_points = 32  # noqa: SLF001
    proc._telemetry_history_max_age_s = 10.0  # noqa: SLF001
    return proc


class StreamAnalysisMemoryBoundsTests(unittest.TestCase):
    def test_compile_workspace_graph_accepts_source_stream_node(self) -> None:
        compiled = compile_workspace_graph(
            {
                "workspace_id": "workspace-1",
                "graph": {
                    "nodes": [
                        {
                            "id": "src",
                            "op": "source.stream",
                            "params": {"device_id": "dev-a", "stream": "trace"},
                        }
                    ]
                },
                "publish": {
                    "outputs": [
                        {"output_id": "raw_trace", "node_id": "src"},
                    ]
                },
            }
        )
        self.assertEqual(compiled.stream_source_node_id, "src")
        self.assertEqual(compiled.stream_key, ("dev-a", "trace"))

    def test_context_cache_per_stream_is_bounded(self) -> None:
        proc = _make_process_for_bounds()
        key = ("device-1", "trace")

        for seq in range(1, 1001):
            proc._remember_context_for_seq(  # noqa: SLF001
                key=key,
                seq=seq,
                context_id=seq,
                context_fields={"x": seq},
            )

        bucket = proc._context_by_seq.get(key, {})  # noqa: SLF001
        self.assertLessEqual(len(bucket), proc._context_cache_limit)  # noqa: SLF001
        self.assertEqual(min(bucket.keys()), 1000 - proc._context_cache_limit + 1)  # noqa: SLF001
        self.assertEqual(max(bucket.keys()), 1000)

        proc._prune_context_cache(key=key, last_seq=1000)  # noqa: SLF001
        self.assertNotIn(key, proc._context_by_seq)  # noqa: SLF001

    def test_telemetry_history_is_bounded_by_point_limit(self) -> None:
        proc = _make_process_for_bounds()
        proc._telemetry_history_max_age_s = 1e9  # noqa: SLF001
        for i in range(500):
            proc._record_telemetry_sample(  # noqa: SLF001
                device_id="dev",
                signal="sig",
                t_mono_s=float(i),
                value=float(i),
            )

        samples = proc._telemetry_history.get(("dev", "sig"), [])  # noqa: SLF001
        self.assertEqual(len(samples), proc._telemetry_history_max_points)  # noqa: SLF001
        self.assertEqual(samples[0][0], 500 - proc._telemetry_history_max_points)
        self.assertEqual(samples[-1][0], 499.0)

    def test_telemetry_history_is_bounded_by_age_window(self) -> None:
        proc = _make_process_for_bounds()
        proc._telemetry_history_max_points = 1000  # noqa: SLF001
        proc._telemetry_history_max_age_s = 5.0  # noqa: SLF001

        for i in range(20):
            proc._record_telemetry_sample(  # noqa: SLF001
                device_id="dev",
                signal="sig",
                t_mono_s=float(i),
                value=float(i),
            )

        samples = proc._telemetry_history.get(("dev", "sig"), [])  # noqa: SLF001
        self.assertTrue(samples)
        self.assertGreaterEqual(samples[0][0], 14.0)
        self.assertEqual(samples[-1][0], 19.0)


if __name__ == "__main__":
    unittest.main()
