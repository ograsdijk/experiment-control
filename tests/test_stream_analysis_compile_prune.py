# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.stream_analysis import compile_workspace_graph


def _base_workspace_with_extra_nodes(extra_nodes: list[dict]) -> dict:
    """Workspace template: one source.stream + a single feed_through publish.

    Callers add `extra_nodes` to test which ones survive compile-time
    pruning (only those reachable from `publish.outputs` are kept in
    `compiled.order`).
    """
    return {
        "workspace_id": "test_workspace",
        "enabled": True,
        "graph": {
            "nodes": [
                {
                    "node_id": "src",
                    "op": "source.stream",
                    "params": {
                        "device_id": "dev",
                        "stream": "trace",
                    },
                },
                {
                    "node_id": "passthrough",
                    "op": "trace.crop",
                    "inputs": {"trace": "src"},
                },
                *extra_nodes,
            ],
        },
        "publish": {
            "outputs": [
                {
                    "output_id": "out_passthrough",
                    "node_id": "passthrough",
                    "kind": "trace",
                },
            ],
        },
    }


class CompileWorkspacePruneTests(unittest.TestCase):
    def test_orphan_node_is_pruned_from_order(self) -> None:
        """A node that no publish.output transitively depends on is left
        out of `compiled.order` so the per-event loop doesn't waste
        cycles dispatching to it. Behavior verified end-to-end through
        compile_workspace_graph rather than the internal helper, so
        future refactors of the pruning machinery stay covered."""
        compiled = compile_workspace_graph(
            _base_workspace_with_extra_nodes(
                [
                    {
                        "node_id": "orphan",
                        "op": "trace.crop",
                        "inputs": {"trace": "src"},
                    },
                ]
            )
        )
        self.assertIn("src", compiled.order)
        self.assertIn("passthrough", compiled.order)
        self.assertNotIn("orphan", compiled.order)
        # Orphan still lives in `nodes` (the full definition map) — only
        # `order` is pruned.
        self.assertIn("orphan", compiled.nodes)

    def test_reachable_chain_is_preserved(self) -> None:
        """Intermediate nodes on the path from source to output stay in
        order. Stateful operators (aggregate.* etc.) would land in this
        category — kept because the output depends on them."""
        compiled = compile_workspace_graph(
            {
                "workspace_id": "chain_workspace",
                "enabled": True,
                "graph": {
                    "nodes": [
                        {
                            "node_id": "src",
                            "op": "source.stream",
                            "params": {
                                "device_id": "dev",
                                "stream": "trace",
                            },
                        },
                        {
                            "node_id": "intermediate",
                            "op": "trace.crop",
                            "inputs": {"trace": "src"},
                        },
                        {
                            "node_id": "final",
                            "op": "trace.crop",
                            "inputs": {"trace": "intermediate"},
                        },
                    ],
                },
                "publish": {
                    "outputs": [
                        {
                            "output_id": "out_final",
                            "node_id": "final",
                            "kind": "trace",
                        },
                    ],
                },
            }
        )
        self.assertEqual(compiled.order, ["src", "intermediate", "final"])

    def test_source_stream_node_is_kept_even_without_outputs(self) -> None:
        """The source stream node is always kept — the per-event loop
        relies on it to advance source-channel bookkeeping. Even if no
        output references it directly, the runtime needs it in order.
        (Workspaces with zero outputs aren't useful in practice; the
        invariant just keeps the runtime defensive.)"""
        compiled = compile_workspace_graph(
            {
                "workspace_id": "no_outputs_workspace",
                "enabled": True,
                "graph": {
                    "nodes": [
                        {
                            "node_id": "src",
                            "op": "source.stream",
                            "params": {
                                "device_id": "dev",
                                "stream": "trace",
                            },
                        },
                    ],
                },
                "publish": {"outputs": []},
            }
        )
        self.assertIn("src", compiled.order)


if __name__ == "__main__":
    unittest.main()
