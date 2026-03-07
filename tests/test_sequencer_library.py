# ruff: noqa: E402

import sys
from pathlib import Path
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.library import SequenceLibrary


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


class SequenceLibraryTests(unittest.TestCase):
    def test_explicit_entries_override_autoload_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root / "main.yaml",
                """
                version: 1
                steps:
                  - use: helper
                """,
            )
            _write(
                root / "helper.yaml",
                """
                version: 1
                steps:
                  - assign:
                      helper_seen: 1
                """,
            )
            _write(
                root / "auto" / "main.yaml",
                """
                version: 1
                steps:
                  - assign:
                      ignored: true
                """,
            )
            _write(
                root / "auto" / "extra.yaml",
                """
                version: 1
                steps:
                  - assign:
                      extra_seen: true
                """,
            )
            _write(
                root / "library.yaml",
                """
                version: 1
                sequences:
                  main:
                    path: main.yaml
                    description: Main sequence
                  helper:
                    path: helper.yaml
                    description: Shared helper
                autoload_dirs:
                  - dir: auto
                    pattern: "*.yaml"
                """,
            )

            lib = SequenceLibrary(manifest_path=root / "library.yaml")
            lib.reload()
            entries = {item["id"]: item for item in lib.list_entries()}
            self.assertIn("main", entries)
            self.assertIn("helper", entries)
            self.assertIn("extra", entries)
            self.assertEqual(entries["main"]["source"], "explicit")
            self.assertEqual(entries["main"]["path"], "main.yaml")
            self.assertEqual(entries["main"]["use_ids"], ["helper"])
            self.assertEqual(entries["extra"]["source"], "autoload")
            self.assertEqual(tuple(lib.warnings), ())

    def test_unknown_use_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root / "broken.yaml",
                """
                version: 1
                steps:
                  - use: missing_fragment
                """,
            )
            _write(
                root / "library.yaml",
                """
                version: 1
                sequences:
                  broken:
                    path: broken.yaml
                    description: broken
                """,
            )
            lib = SequenceLibrary(manifest_path=root / "library.yaml")
            with self.assertRaises(ValueError) as cm:
                lib.reload()
            self.assertIn("unknown use.id", str(cm.exception))

    def test_use_cycles_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root / "a.yaml",
                """
                version: 1
                steps:
                  - use: b
                """,
            )
            _write(
                root / "b.yaml",
                """
                version: 1
                steps:
                  - use: a
                """,
            )
            _write(
                root / "library.yaml",
                """
                version: 1
                sequences:
                  a:
                    path: a.yaml
                    description: seq a
                  b:
                    path: b.yaml
                    description: seq b
                """,
            )
            lib = SequenceLibrary(manifest_path=root / "library.yaml")
            with self.assertRaises(ValueError) as cm:
                lib.reload()
            self.assertIn("use cycle detected", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
