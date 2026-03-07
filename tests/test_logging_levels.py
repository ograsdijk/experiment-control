import unittest

from experiment_control.utils.logging_levels import (
    is_valid_log_severity,
    normalize_log_severity,
    severity_rank,
)


class LoggingLevelsTests(unittest.TestCase):
    def test_warn_alias_normalizes_to_warning(self) -> None:
        self.assertEqual(normalize_log_severity("warn"), "warning")
        self.assertTrue(is_valid_log_severity("warn"))

    def test_unknown_normalizes_to_default(self) -> None:
        self.assertEqual(normalize_log_severity("nope"), "info")
        self.assertEqual(normalize_log_severity("nope", default="error"), "error")
        self.assertFalse(is_valid_log_severity("nope"))

    def test_severity_rank_order(self) -> None:
        self.assertLess(severity_rank("debug"), severity_rank("info"))
        self.assertLess(severity_rank("warning"), severity_rank("error"))
        self.assertLess(severity_rank("error"), severity_rank("critical"))


if __name__ == "__main__":
    unittest.main()

