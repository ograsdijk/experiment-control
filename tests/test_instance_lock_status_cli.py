# ruff: noqa: E402

import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.cli.instance_lock_status import main
from experiment_control.utils.instance_lock import InstanceLock
from tests._temp_utils import repo_temp_dir


class InstanceLockStatusCliTests(unittest.TestCase):
    def test_cli_json_output(self) -> None:
        with repo_temp_dir("instance-lock-cli") as root:
            with mock.patch(
                "experiment_control.utils.instance_lock._lock_root",
                return_value=root,
            ):
                lock = InstanceLock(
                    instance_id="vacuum",
                    manager_rpc="tcp://127.0.0.1:6000",
                )
                lock.acquire()
                buf = io.StringIO()
                with redirect_stdout(buf):
                    main(["vacuum", "--json"])
                lock.release()
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload.get("status"), "active")
        self.assertEqual(int(payload.get("owner_pid", -1)), int(os.getpid()))

    def test_cli_human_output(self) -> None:
        with repo_temp_dir("instance-lock-cli") as root:
            with mock.patch(
                "experiment_control.utils.instance_lock._lock_root",
                return_value=root,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    main(["vacuum"])
        text = buf.getvalue()
        self.assertIn("instance_lock", text)
        self.assertIn("status='missing'", text)


if __name__ == "__main__":
    unittest.main()
