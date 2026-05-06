# ruff: noqa: E402

import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.validation.config import validate_instance_config


class ConfigValidationTests(unittest.TestCase):
    def test_reports_missing_process_csv_and_unknown_init_kwarg(self) -> None:
        with self.subTest("static process validation"):
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "devices").mkdir()
                (root / "processes").mkdir()
                (root / "processes" / "proc.py").write_text(
                    textwrap.dedent(
                        """
                        class DemoProcess:
                            def __init__(self, *, rules=None):
                                self.rules = rules
                        """
                    ).strip(),
                    encoding="utf-8",
                )
                (root / "processes" / "demo.yaml").write_text(
                    textwrap.dedent(
                        """
                        version: 1
                        process_id: demo
                        process:
                          file: processes/proc.py
                          class_name: DemoProcess
                        init_kwargs:
                          rules:
                            - device_id: synth
                              trigger_action: set_frequency
                              csv_path: missing.csv
                          extra: true
                        """
                    ).strip(),
                    encoding="utf-8",
                )

                diagnostics = validate_instance_config(root)
                messages = "\n".join(f"{d.field_path}: {d.message}" for d in diagnostics)
                self.assertIn("init_kwargs: unknown constructor argument(s): extra", messages)
                self.assertIn("init_kwargs.rules[0].csv_path: file does not exist: missing.csv", messages)


if __name__ == "__main__":
    unittest.main()
