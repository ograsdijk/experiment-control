# ruff: noqa: E402

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control._manager.process_spec import process_spec_kwargs_from_yaml
from experiment_control._manager.models import RestartPolicy


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


class ProcessSpecPathResolutionTests(unittest.TestCase):
    def test_process_paths_resolve_relative_to_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as cwd_td:
            root = Path(td)
            process_dir = root / "instances" / "test" / "processes"
            seq_dir = root / "instances" / "test" / "sequences"
            _write(
                seq_dir / "a.yaml",
                """
                version: 1
                steps:
                  - assign:
                      a_seen: 1
                """,
            )
            _write(
                seq_dir / "b.yaml",
                """
                version: 1
                steps:
                  - assign:
                      b_seen: 1
                """,
            )
            _write(
                root / "instances" / "test" / "sequence_library.yaml",
                """
                version: 1
                autoload_dirs:
                  - dir: sequences
                    pattern: "*.yaml"
                """,
            )
            _write(
                process_dir / "sequencer.yaml",
                """
                process_id: sequencer
                process:
                  module: experiment_control.sequencer.sequencer
                  class_name: SequencerProcess
                init_kwargs:
                  sequence_library_path: sequence_library.yaml
                  autoload_path: sequences/a.yaml
                cwd: runner/work
                restart_policy: NEVER
                """,
            )
            other_cwd = Path(cwd_td)
            old_cwd = Path.cwd()
            os.chdir(other_cwd)
            try:
                spec = process_spec_kwargs_from_yaml(
                    process_dir / "sequencer.yaml",
                    manager_rpc="tcp://127.0.0.1:6502",
                    manager_pub="tcp://127.0.0.1:6503",
                    restart_policy_enum=RestartPolicy,
                )
            finally:
                os.chdir(old_cwd)

        init_json = spec["argv"][spec["argv"].index("--process-init-json") + 1]
        init_kwargs = json.loads(init_json)
        self.assertEqual(
            init_kwargs["sequence_library_path"],
            str((root / "instances" / "test" / "sequence_library.yaml").resolve()),
        )
        self.assertEqual(
            init_kwargs["autoload_path"],
            str((root / "instances" / "test" / "sequences" / "a.yaml").resolve()),
        )
        self.assertEqual(
            spec["cwd"],
            str((root / "instances" / "test" / "runner" / "work").resolve()),
        )

    def test_absolute_cwd_remains_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root / "process.yaml",
                """
                process_id: sequencer
                process:
                  module: experiment_control.sequencer.sequencer
                  class_name: SequencerProcess
                init_kwargs:
                  sequence_library_path: /tmp/library.yaml
                cwd: /tmp/actual-cwd
                restart_policy: NEVER
                """,
            )
            spec = process_spec_kwargs_from_yaml(
                root / "process.yaml",
                manager_rpc="tcp://127.0.0.1:6502",
                manager_pub="tcp://127.0.0.1:6503",
                restart_policy_enum=RestartPolicy,
            )

        self.assertEqual(spec["cwd"], str(Path("/tmp/actual-cwd").resolve()))
        init_json = spec["argv"][spec["argv"].index("--process-init-json") + 1]
        init_kwargs = json.loads(init_json)
        self.assertEqual(init_kwargs["sequence_library_path"], str(Path("/tmp/library.yaml").resolve()))


if __name__ == "__main__":
    unittest.main()
