# ruff: noqa: E402
"""Unit tests for the instance.yaml persistence helper backing
POST /api/gateway/stream-max-payload-points.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.fastapi.app import _persist_stream_max_payload_points


class PersistStreamMaxPayloadPointsTests(unittest.TestCase):
    def setUp(self) -> None:
        # Use mkdtemp + manual cleanup rather than tempfile.TemporaryDirectory:
        # test_hdf_writer.py monkeypatches that symbol at module level with no
        # teardown, and `unittest discover` imports every test module before
        # running any of them, so the patch is already active here regardless
        # of file ordering.
        self.instance_root = Path(tempfile.mkdtemp())
        self._prev_env = os.environ.get("EXPERIMENT_CONTROL_INSTANCE_ROOT")
        os.environ["EXPERIMENT_CONTROL_INSTANCE_ROOT"] = str(self.instance_root)

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop("EXPERIMENT_CONTROL_INSTANCE_ROOT", None)
        else:
            os.environ["EXPERIMENT_CONTROL_INSTANCE_ROOT"] = self._prev_env
        shutil.rmtree(self.instance_root, ignore_errors=True)

    def _write_instance_yaml(self, text: str) -> Path:
        path = self.instance_root / "instance.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_replaces_existing_scalar(self) -> None:
        path = self._write_instance_yaml(
            "fastapi:\n"
            "  host: 0.0.0.0\n"
            "  port: 8004\n"
            "  stream_max_payload_points: 200000\n"
            "other_key: 1\n"
        )
        _persist_stream_max_payload_points(250000)
        text = path.read_text(encoding="utf-8")
        self.assertIn("stream_max_payload_points: 250000", text)
        self.assertNotIn("200000", text)
        self.assertIn("other_key: 1", text)

    def test_inserts_when_missing(self) -> None:
        path = self._write_instance_yaml(
            "fastapi:\n  host: 0.0.0.0\n  port: 8004\nother_key: 1\n"
        )
        _persist_stream_max_payload_points(300000)
        text = path.read_text(encoding="utf-8")
        self.assertIn("stream_max_payload_points: 300000", text)
        self.assertIn("other_key: 1", text)

    def test_missing_instance_root_env_raises(self) -> None:
        os.environ.pop("EXPERIMENT_CONTROL_INSTANCE_ROOT", None)
        with self.assertRaises(ValueError):
            _persist_stream_max_payload_points(1)

    def test_missing_fastapi_mapping_raises(self) -> None:
        self._write_instance_yaml("other_key: 1\n")
        with self.assertRaises(ValueError):
            _persist_stream_max_payload_points(1)


if __name__ == "__main__":
    unittest.main()
