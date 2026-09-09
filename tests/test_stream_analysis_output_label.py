# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.stream_analysis import compile_workspace_graph


def _workspace(publish_output: dict) -> dict:
    return {
        "workspace_id": "test_workspace",
        "enabled": True,
        "graph": {
            "nodes": [
                {
                    "node_id": "src",
                    "op": "source.stream",
                    "params": {"device_id": "dev", "stream": "trace"},
                },
            ],
        },
        "publish": {"outputs": [publish_output]},
    }


class PublishOutputLabelTests(unittest.TestCase):
    """`label` is optional presentation metadata on a published output.

    `output_id` remains the stable programmatic identity; a UI reads
    `label` when present and derives a name from the id otherwise.
    """

    def test_label_is_carried_through_compile(self) -> None:
        compiled = compile_workspace_graph(
            _workspace(
                {
                    "output_id": "fluor_integral_vs_scan",
                    "node_id": "src",
                    "label": "Fluorescence vs scan",
                }
            )
        )
        self.assertEqual(compiled.outputs[0].label, "Fluorescence vs scan")
        self.assertEqual(compiled.outputs[0].output_id, "fluor_integral_vs_scan")

    def test_absent_label_compiles_to_none(self) -> None:
        """Every workspace written before labels existed must still compile."""
        compiled = compile_workspace_graph(
            _workspace({"output_id": "raw_trace", "node_id": "src"})
        )
        self.assertIsNone(compiled.outputs[0].label)

    def test_blank_and_non_string_labels_are_dropped(self) -> None:
        """A blank label must not shadow the derived name with an empty one."""
        for bad in ("", "   ", 17, None, ["a"]):
            with self.subTest(label=bad):
                compiled = compile_workspace_graph(
                    _workspace(
                        {"output_id": "raw_trace", "node_id": "src", "label": bad}
                    )
                )
                self.assertIsNone(compiled.outputs[0].label)

    def test_label_is_trimmed(self) -> None:
        compiled = compile_workspace_graph(
            _workspace(
                {
                    "output_id": "raw_trace",
                    "node_id": "src",
                    "label": "  Absorption trace  ",
                }
            )
        )
        self.assertEqual(compiled.outputs[0].label, "Absorption trace")


if __name__ == "__main__":
    unittest.main()
