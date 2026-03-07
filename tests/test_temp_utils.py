# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests._temp_utils import repo_temp_dir, repo_temp_root


class TestTempUtilsTests(unittest.TestCase):
    def test_repo_temp_dir_is_writable(self) -> None:
        with repo_temp_dir("temp-utils") as tmp_path:
            self.assertTrue(tmp_path.exists())
            self.assertTrue(tmp_path.is_dir())
            probe = tmp_path / "probe.txt"
            probe.write_text("ok", encoding="utf-8")
            self.assertEqual(probe.read_text(encoding="utf-8"), "ok")
            self.assertEqual(tmp_path.parent, repo_temp_root())


if __name__ == "__main__":
    unittest.main()
