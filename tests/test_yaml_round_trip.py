# ruff: noqa: E402

import sys
import textwrap
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import yaml

from experiment_control.utils.yaml_round_trip import (
    detect_indent,
    dump_yaml_preserving,
)

STORE = textwrap.dedent(
    """\
    # Detection stack analysis workspaces.
    version: 1
    workspaces:
      - workspace_id: detection_fluorescence
        name: Detection fluorescence vs scan
        enabled: true
        graph:
          nodes:
            # --- raw sources ---
            - node_id: fluor_raw
              op: source.stream
              params: {device_id: pxie5171, stream: waveforms, channel_indices: "1,2"}

            - node_id: abs_raw
              op: source.stream
              params: {device_id: pxie5171, stream: waveforms, channel_index: 3}

        publish:
          outputs:
            # histograms
            - {output_id: fluor_vs_scan, node_id: fluor_raw, label: Fluorescence vs scan}

            - {output_id: abs_trace,     node_id: abs_raw}
    """
)


def _load(text: str):
    return yaml.safe_load(text)


class DetectIndentTests(unittest.TestCase):
    def test_reads_the_store_style(self) -> None:
        self.assertEqual(
            detect_indent(STORE), {"mapping": 2, "sequence": 4, "offset": 2}
        )

    def test_reads_a_flush_sequence_style(self) -> None:
        text = textwrap.dedent(
            """\
            items:
            - id: a
              v: 1
            """
        )
        indent = detect_indent(text)
        self.assertEqual(indent["offset"], 0)
        self.assertEqual(indent["sequence"], 2)

    def test_falls_back_to_defaults_for_a_flat_document(self) -> None:
        self.assertEqual(
            detect_indent("a: 1\nb: 2\n"),
            {"mapping": 2, "sequence": 4, "offset": 2},
        )


class PreservingDumpTests(unittest.TestCase):
    """A save keeps the file a person wrote; only edited values move."""

    def test_editing_one_label_touches_one_line(self) -> None:
        payload = _load(STORE)
        outputs = payload["workspaces"][0]["publish"]["outputs"]
        outputs[0]["label"] = "Normalized fluorescence"
        text = dump_yaml_preserving(payload, STORE)

        self.assertEqual(_load(text), payload)
        before = STORE.splitlines()
        after = text.splitlines()
        changed = [
            (old, new)
            for old, new in zip(before, after)
            if old != new
        ]
        self.assertEqual(len(before), len(after))
        # The other changed line is the flow padding ruamel collapses; the
        # edit itself is the only content change.
        self.assertEqual(len(changed), 2)
        self.assertIn("Normalized fluorescence", changed[0][1])
        self.assertEqual(
            changed[1][1].strip(), "- {output_id: abs_trace, node_id: abs_raw}"
        )

    def test_comments_and_blank_lines_survive(self) -> None:
        payload = _load(STORE)
        payload["workspaces"][0]["publish"]["outputs"][0]["label"] = "Renamed"
        text = dump_yaml_preserving(payload, STORE)

        self.assertIn("# Detection stack analysis workspaces.", text)
        self.assertIn("# --- raw sources ---", text)
        self.assertIn("# histograms", text)
        self.assertIn("\n\n", text)

    def test_flow_style_and_quoting_survive(self) -> None:
        payload = _load(STORE)
        payload["workspaces"][0]["enabled"] = False
        text = dump_yaml_preserving(payload, STORE)

        self.assertIn("- {output_id: fluor_vs_scan,", text)
        self.assertIn('channel_indices: "1,2"', text)

    def test_a_new_output_is_appended_without_disturbing_the_rest(self) -> None:
        payload = _load(STORE)
        outputs = payload["workspaces"][0]["publish"]["outputs"]
        outputs.append({"output_id": "scan_x", "node_id": "abs_raw"})
        text = dump_yaml_preserving(payload, STORE)

        self.assertEqual(_load(text), payload)
        self.assertIn("# histograms", text)
        self.assertIn("- {output_id: fluor_vs_scan,", text)
        self.assertIn("scan_x", text)

    def test_a_removed_output_takes_only_its_own_line(self) -> None:
        payload = _load(STORE)
        outputs = payload["workspaces"][0]["publish"]["outputs"]
        del outputs[1]
        text = dump_yaml_preserving(payload, STORE)

        self.assertEqual(_load(text), payload)
        self.assertNotIn("abs_trace", text)
        self.assertIn("- {output_id: fluor_vs_scan,", text)
        self.assertIn("# histograms", text)

    def test_an_item_is_matched_by_identity_not_position(self) -> None:
        """An edit follows its node even when the list order differs.

        The in-memory model is rebuilt from a dict, so its order is not
        the file's. Matching on `node_id` is what keeps the edit — and
        every comment around it — where it belongs.
        """
        payload = _load(STORE)
        nodes = payload["workspaces"][0]["graph"]["nodes"]
        nodes.reverse()
        nodes[0]["params"]["channel_index"] = 4
        text = dump_yaml_preserving(payload, STORE)

        written = _load(text)
        written_nodes = written["workspaces"][0]["graph"]["nodes"]
        # Disk order stands — the edit landed on the node it belongs to.
        self.assertEqual(
            [node["node_id"] for node in written_nodes], ["fluor_raw", "abs_raw"]
        )
        self.assertEqual(written_nodes[1]["params"]["channel_index"], 4)
        self.assertIn("# --- raw sources ---", text)

    def test_removing_a_key_removes_it_from_the_file(self) -> None:
        payload = _load(STORE)
        del payload["workspaces"][0]["name"]
        text = dump_yaml_preserving(payload, STORE)

        self.assertEqual(_load(text), payload)
        self.assertNotIn("Detection fluorescence vs scan", text)

    def test_a_scalar_list_is_replaced_positionally(self) -> None:
        source = "values:\n  - 1\n  - 2\n  - 3\n"
        payload = {"values": [1, 9]}
        text = dump_yaml_preserving(payload, source)
        self.assertEqual(_load(text), payload)


class FallbackTests(unittest.TestCase):
    """Formatting is a nicety; writing the right content is not."""

    def test_no_existing_file_dumps_plainly(self) -> None:
        payload = {"version": 1, "workspaces": []}
        self.assertEqual(_load(dump_yaml_preserving(payload, None)), payload)
        self.assertEqual(_load(dump_yaml_preserving(payload, "   ")), payload)

    def test_unparsable_existing_file_dumps_plainly(self) -> None:
        payload = {"version": 1}
        text = dump_yaml_preserving(payload, "version: [1\n  broken")
        self.assertEqual(_load(text), payload)

    def test_a_document_of_another_shape_dumps_plainly(self) -> None:
        payload = {"version": 1}
        text = dump_yaml_preserving(payload, "- a\n- b\n")
        self.assertEqual(_load(text), payload)

    def test_payload_is_what_lands_on_disk(self) -> None:
        """Whichever path runs, the file reads back as the payload."""
        payload = _load(STORE)
        payload["workspaces"][0]["graph"]["nodes"] = []
        payload["workspaces"].append(
            {"workspace_id": "second", "enabled": True, "graph": {}, "publish": {}}
        )
        text = dump_yaml_preserving(payload, STORE)
        self.assertEqual(_load(text), payload)


if __name__ == "__main__":
    unittest.main()
