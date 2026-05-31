# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils.responses import is_response_ok


class IsResponseOkTests(unittest.TestCase):
    def test_dict_with_ok_true(self) -> None:
        self.assertTrue(is_response_ok({"ok": True}))

    def test_dict_with_ok_false(self) -> None:
        self.assertFalse(is_response_ok({"ok": False}))

    def test_dict_with_ok_truthy_int(self) -> None:
        # ``bool(1) == True`` — accepted for backwards compatibility.
        self.assertTrue(is_response_ok({"ok": 1}))

    def test_dict_with_ok_falsy_zero(self) -> None:
        self.assertFalse(is_response_ok({"ok": 0}))

    def test_dict_with_status_OK_exact(self) -> None:
        self.assertTrue(is_response_ok({"status": "OK"}))

    def test_dict_with_status_ERROR(self) -> None:
        self.assertFalse(is_response_ok({"status": "ERROR"}))

    def test_dict_with_status_lowercase_rejected(self) -> None:
        # Lowercase is rejected on purpose; if a device emits this, the bug
        # belongs in the device driver, not in this predicate.
        self.assertFalse(is_response_ok({"status": "ok"}))
        self.assertFalse(is_response_ok({"status": "Ok"}))

    def test_dict_with_unknown_status(self) -> None:
        self.assertFalse(is_response_ok({"status": "MAYBE"}))

    def test_dict_with_no_ok_and_no_status(self) -> None:
        self.assertFalse(is_response_ok({"result": "anything"}))

    def test_ok_wins_over_status(self) -> None:
        # If both are present, "ok" is authoritative.
        self.assertTrue(is_response_ok({"ok": True, "status": "ERROR"}))
        self.assertFalse(is_response_ok({"ok": False, "status": "OK"}))

    def test_non_dict_inputs(self) -> None:
        self.assertFalse(is_response_ok(None))
        self.assertFalse(is_response_ok(""))
        self.assertFalse(is_response_ok("OK"))
        self.assertFalse(is_response_ok(True))
        self.assertFalse(is_response_ok([{"ok": True}]))

    def test_empty_dict(self) -> None:
        self.assertFalse(is_response_ok({}))


if __name__ == "__main__":
    unittest.main()
