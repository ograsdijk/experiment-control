# ruff: noqa: E402
"""Tests for Group H mypy-hygiene helpers.

These tests pin behavioural contracts on the new helpers introduced
to centralise patterns that previously triggered mypy noise:

* utils.env.{env_int, env_float, env_bool, env_str}: typed env-var
  helpers that replace inline `int(os.environ.get(KEY, "default"))`
  patterns.

* ManagedProcessBase._require_manager: narrows the
  `ManagerClient | None` attribute and surfaces a clear runtime error
  when an over-eager subclass invokes manager calls before init.

* The renamed `_drop_interceptor_routes_for_process` in device_router
  removes an LSP collision with the inherited
  `_unregister_command_interceptor_routes` no-arg hook from
  ManagedProcessBase. We assert both names exist with the right
  semantics.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.device_router import DeviceRouter
from experiment_control.processes.process_base import ManagedProcessBase
from experiment_control.utils.env import env_bool, env_float, env_int


# ---------------------------------------------------------------------------
# utils.env
# ---------------------------------------------------------------------------


class EnvHelpersTests(unittest.TestCase):
    """Each helper must return the default when the var is unset OR
    cannot be parsed. None of them should ever raise on operator-
    supplied input — a typo'd env var shouldn't crash the gateway."""

    KEY = "EC_TEST_ENV_HELPER"

    def tearDown(self) -> None:
        os.environ.pop(self.KEY, None)

    def test_env_int_unset_returns_default(self) -> None:
        os.environ.pop(self.KEY, None)
        self.assertEqual(env_int(self.KEY, 42), 42)

    def test_env_int_parses_valid_value(self) -> None:
        os.environ[self.KEY] = "  1234  "
        self.assertEqual(env_int(self.KEY, 0), 1234)

    def test_env_int_malformed_returns_default(self) -> None:
        os.environ[self.KEY] = "not a number"
        self.assertEqual(env_int(self.KEY, 99), 99)

    def test_env_int_empty_returns_default(self) -> None:
        os.environ[self.KEY] = ""
        self.assertEqual(env_int(self.KEY, 7), 7)

    def test_env_float_parses_valid_value(self) -> None:
        os.environ[self.KEY] = "1.5"
        self.assertEqual(env_float(self.KEY, 0.0), 1.5)

    def test_env_float_malformed_returns_default(self) -> None:
        os.environ[self.KEY] = "nope"
        self.assertEqual(env_float(self.KEY, 3.14), 3.14)

    def test_env_bool_truthy_strings(self) -> None:
        for v in ("1", "true", "TRUE", "yes", " on ", "True"):
            with self.subTest(value=v):
                os.environ[self.KEY] = v
                self.assertTrue(env_bool(self.KEY, default=False))

    def test_env_bool_falsy_strings(self) -> None:
        for v in ("0", "false", "no", "off", "anything-else"):
            with self.subTest(value=v):
                os.environ[self.KEY] = v
                self.assertFalse(env_bool(self.KEY, default=True))

    def test_env_bool_unset_returns_default(self) -> None:
        os.environ.pop(self.KEY, None)
        self.assertTrue(env_bool(self.KEY, default=True))
        self.assertFalse(env_bool(self.KEY, default=False))

    def test_env_bool_set_to_non_truthy_ignores_default(self) -> None:
        # Regression for review finding: the docstring's claim
        # "everything else returns default" was misleading. The actual
        # contract (matching the pre-existing _env_bool in fastapi/app.py)
        # is "any SET value is parsed as bool; only UNSET respects
        # default". A typo or explicit "false" must NOT silently become
        # True just because the caller passed default=True.
        for value in ("0", "false", "off", "no", "banana"):
            with self.subTest(value=value):
                os.environ[self.KEY] = value
                self.assertFalse(
                    env_bool(self.KEY, default=True),
                    f"env_bool with explicit non-truthy {value!r} must "
                    f"return False even when default=True",
                )


# ---------------------------------------------------------------------------
# ManagedProcessBase._require_manager
# ---------------------------------------------------------------------------


class _StubManagerClient:
    """Minimal stand-in: anything with the methods we'd call."""

    def call(self, payload, *, timeout_ms=None):
        return {"ok": True}


class RequireManagerTests(unittest.TestCase):
    def _make_proc(self) -> ManagedProcessBase:
        # Skip __init__ — we only want to exercise the helper.
        proc = object.__new__(ManagedProcessBase)
        proc._manager = None
        return proc

    def test_raises_when_manager_unset(self) -> None:
        proc = self._make_proc()
        with self.assertRaises(RuntimeError) as ctx:
            proc._require_manager()
        # Error message must be operator-friendly and name the subclass.
        self.assertIn("not initialized", str(ctx.exception))

    def test_returns_manager_when_set(self) -> None:
        proc = self._make_proc()
        proc._manager = _StubManagerClient()  # type: ignore[assignment]
        got = proc._require_manager()
        self.assertIs(got, proc._manager)


# ---------------------------------------------------------------------------
# device_router._drop_interceptor_routes_for_process rename
# ---------------------------------------------------------------------------


class DeviceRouterMethodRenameTests(unittest.TestCase):
    """The rename removes an LSP signature collision with the inherited
    `_unregister_command_interceptor_routes(self)` no-arg hook.
    We assert both names exist on the right class with the right shape."""

    def test_drop_interceptor_routes_for_process_exists_on_device_router(self) -> None:
        method = getattr(DeviceRouter, "_drop_interceptor_routes_for_process", None)
        self.assertIsNotNone(
            method,
            "the renamed _drop_interceptor_routes_for_process must exist "
            "on DeviceRouter; the old _unregister_command_interceptor_routes "
            "name was renamed to remove LSP collision with the inherited "
            "no-arg hook from ManagedProcessBase",
        )

    def test_unregister_no_arg_hook_still_inherited(self) -> None:
        # The inherited no-arg hook from ManagedProcessBase must still
        # be present on DeviceRouter (subclasses like InterlockProcess
        # call self._unregister_command_interceptor_routes() with no
        # args).
        self.assertTrue(
            hasattr(DeviceRouter, "_unregister_command_interceptor_routes"),
            "the inherited ManagedProcessBase._unregister_command_interceptor_routes"
            " no-arg hook must remain available on DeviceRouter",
        )

    def test_device_router_internal_caller_uses_renamed_method(self) -> None:
        # Smoke check that the rename is consistent inside DeviceRouter:
        # the manager-side process_id-keyed bulk-removal entry point
        # is the new name; the old positional-arg form must be gone.
        import inspect

        src = inspect.getsource(DeviceRouter)
        self.assertIn("_drop_interceptor_routes_for_process", src)
        # The old positional-arg call-site form must be gone (we allow
        # the name to appear in comments / docstrings; only the actual
        # invocation matters).
        self.assertNotIn(
            "self._unregister_command_interceptor_routes(process_id)", src
        )


if __name__ == "__main__":
    unittest.main()
