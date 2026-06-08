# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils.responses import (  # noqa: E402
    RpcResponse,
    ensure_error_shape,
    from_driver_status,
    is_response_ok,
    normalize_command_response,
)


class RpcResponseTests(unittest.TestCase):
    def test_failure_shape_omits_absent_fields(self) -> None:
        self.assertEqual(
            RpcResponse.failure("unknown_process").to_dict(),
            {"ok": False, "error": {"code": "unknown_process"}},
        )

    def test_failure_shape_with_message_details_and_result(self) -> None:
        self.assertEqual(
            RpcResponse.failure(
                "device_error",
                "bad",
                {"raw": True},
                result={"status": "ERROR"},
                include_result=True,
            ).to_dict(),
            {
                "ok": False,
                "result": {"status": "ERROR"},
                "error": {
                    "code": "device_error",
                    "message": "bad",
                    "details": {"raw": True},
                },
            },
        )

    def test_ensure_error_shape_preserves_downstream_error_message(self) -> None:
        resp = ensure_error_shape({"ok": False, "error": "invalid_state: bad state"})
        self.assertEqual(
            resp,
            {
                "ok": False,
                "error": {"code": "error", "message": "invalid_state: bad state"},
            },
        )

    def test_from_driver_status_legacy_error_shape(self) -> None:
        resp = from_driver_status({"status": "ERROR", "error": "bad state"}).to_dict()
        self.assertEqual(
            resp,
            {
                "ok": False,
                "result": None,
                "error": {
                    "code": "device_error",
                    "message": "bad state",
                    "details": {"status": "ERROR", "error": "bad state"},
                },
            },
        )

    def test_normalize_command_response_keeps_ok_envelope(self) -> None:
        resp = {"ok": False, "error": {"code": "invalid_state", "message": "bad state"}}
        self.assertIs(normalize_command_response(resp), resp)

    def test_success_shape_includes_result_even_when_none(self) -> None:
        # Pins down the success(None) wire shape — the envelope always
        # includes "result", even when the value is None, so downstream
        # parsers can rely on key presence rather than membership tests.
        self.assertEqual(
            RpcResponse.success().to_dict(),
            {"ok": True, "result": None},
        )
        self.assertEqual(
            RpcResponse.success(42).to_dict(),
            {"ok": True, "result": 42},
        )


class IsResponseOkTests(unittest.TestCase):
    def test_ok_true_is_success(self) -> None:
        self.assertTrue(is_response_ok({"ok": True}))

    def test_ok_false_is_failure(self) -> None:
        self.assertFalse(is_response_ok({"ok": False}))

    def test_status_ok_is_success(self) -> None:
        self.assertTrue(is_response_ok({"status": "OK"}))

    def test_status_error_is_failure(self) -> None:
        self.assertFalse(is_response_ok({"status": "ERROR"}))

    def test_truthy_non_bool_ok_is_not_accepted(self) -> None:
        # Per module docstring: ``ok`` is identity-checked. A buggy
        # driver returning ``"yes"`` or ``1`` must surface as a
        # failure, not be silently coerced to True.
        for value in (1, "yes", "true", "OK"):
            self.assertFalse(is_response_ok({"ok": value}), value)

    def test_status_lowercase_is_not_accepted(self) -> None:
        for value in ("ok", "Ok", " OK"):
            self.assertFalse(is_response_ok({"status": value}), value)

    def test_non_dict_is_failure(self) -> None:
        self.assertFalse(is_response_ok(None))
        self.assertFalse(is_response_ok("OK"))


class FromDriverStatusStrictTests(unittest.TestCase):
    def test_truthy_non_bool_ok_is_treated_as_failure(self) -> None:
        # Matches is_response_ok's strictness.
        resp = from_driver_status({"ok": 1, "result": 42}).to_dict()
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "error")

    def test_status_ok_round_trip(self) -> None:
        resp = from_driver_status({"status": "OK", "result": 7}).to_dict()
        self.assertEqual(resp, {"ok": True, "result": 7})


if __name__ == "__main__":
    unittest.main()
