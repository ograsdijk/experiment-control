# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.stream_analysis import compile_workspace_graph


def _two_source_workspace(stream_a: str, stream_b: str) -> dict:
    return {
        "workspace_id": "multi",
        "enabled": True,
        "graph": {
            "nodes": [
                {
                    "node_id": "src_a",
                    "op": "source.stream",
                    "params": {"device_id": "dev", "stream": stream_a, "channel_index": 1},
                },
                {
                    "node_id": "src_b",
                    "op": "source.stream",
                    "params": {"device_id": "dev", "stream": stream_b, "channel_index": 3},
                },
                {"node_id": "int_a", "op": "trace.integrate", "inputs": {"trace": "src_a"}},
                {"node_id": "int_b", "op": "trace.integrate", "inputs": {"trace": "src_b"}},
            ]
        },
        "publish": {
            "outputs": [
                {"output_id": "out_a", "node_id": "int_a"},
                {"output_id": "out_b", "node_id": "int_b"},
            ]
        },
    }


class MultiSourceCompileTests(unittest.TestCase):
    def test_same_stream_multiple_sources_compile(self) -> None:
        compiled = compile_workspace_graph(_two_source_workspace("trace", "trace"))
        self.assertEqual(compiled.stream_key, ("dev", "trace"))
        self.assertCountEqual(compiled.stream_source_node_ids, ["src_a", "src_b"])
        # both source nodes are kept in the eval order (not pruned)
        self.assertIn("src_a", compiled.order)
        self.assertIn("src_b", compiled.order)
        # both outputs survive
        self.assertCountEqual(
            [o.output_id for o in compiled.outputs], ["out_a", "out_b"]
        )

    def test_different_streams_rejected(self) -> None:
        with self.assertRaises(ValueError) as cm:
            compile_workspace_graph(_two_source_workspace("trace_a", "trace_b"))
        self.assertIn("same", str(cm.exception).lower())

    def test_single_source_still_compiles(self) -> None:
        compiled = compile_workspace_graph(
            {
                "workspace_id": "single",
                "enabled": True,
                "graph": {
                    "nodes": [
                        {
                            "node_id": "src",
                            "op": "source.stream",
                            "params": {"device_id": "dev", "stream": "trace"},
                        },
                        {
                            "node_id": "intg",
                            "op": "trace.integrate",
                            "inputs": {"trace": "src"},
                        },
                    ]
                },
                "publish": {"outputs": [{"output_id": "out", "node_id": "intg"}]},
            }
        )
        self.assertEqual(compiled.stream_source_node_ids, ["src"])
        self.assertEqual(compiled.stream_key, ("dev", "trace"))


if __name__ == "__main__":
    unittest.main()
