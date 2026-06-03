# ruff: noqa: E402

"""Phase 8 cross-mixin protocol coverage.

The mixin migration (REFACTOR_PLAN §8) splits ``Manager`` across many
``manager_*.py`` modules. Each mixin method that calls a sibling
method types ``self`` against :class:`ManagerProtocol`. This test
guards against drift between the Protocol declarations and the actual
method implementations on the composed ``Manager`` class.

If a sibling-mixin method's signature changes without a matching
update to ``ManagerProtocol``, this test fails — long before a
runtime ``AttributeError`` or a quietly-stale type hint can ship.
"""

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import Manager
from experiment_control.manager_protocol import ManagerProtocol


# Names typing.Protocol injects into class.__dict__ at runtime; not
# Manager-method declarations, so the drift test must skip them.
_PROTOCOL_INTERNALS = frozenset({"_abc_impl", "_is_protocol", "_is_runtime_protocol"})


def _protocol_method_names() -> list[str]:
    # Protocol members include dunders inherited from object plus
    # ``typing``-internal markers (``_abc_impl``, ``_is_protocol``).
    # Filter to actual method declarations: underscored single-prefix
    # names that resolve to a function on the Protocol class.
    out = []
    for name in sorted(vars(ManagerProtocol)):
        if not name.startswith("_") or name.startswith("__"):
            continue
        if name in _PROTOCOL_INTERNALS:
            continue
        if not callable(getattr(ManagerProtocol, name, None)):
            continue
        out.append(name)
    return out


class ManagerProtocolDriftTests(unittest.TestCase):
    def test_every_protocol_method_resolves_on_Manager(self) -> None:
        # Every method declared on ``ManagerProtocol`` must be reachable
        # from ``Manager`` via MRO. A missing method here means a mixin
        # that promised to provide the method never landed, or the
        # protocol declaration is stale.
        for name in _protocol_method_names():
            with self.subTest(method=name):
                self.assertTrue(
                    hasattr(Manager, name),
                    f"Manager does not provide {name!r} declared on ManagerProtocol",
                )

    def test_protocol_signatures_match_Manager_implementations(self) -> None:
        # Each ``ManagerProtocol`` method signature must agree with the
        # resolved ``Manager`` method, parameter-by-parameter. We compare
        # ``inspect.signature`` (which normalises positional/keyword/var
        # args) rather than raw ``__code__`` so type-only differences
        # (e.g. mypy-checked stub bodies vs. real implementations) are
        # tolerated. Stub bodies on Protocol are ``...`` so ``return``
        # annotations on the real impl may be more specific — only
        # parameter count and kind/name are compared.
        for name in _protocol_method_names():
            with self.subTest(method=name):
                proto = inspect.signature(getattr(ManagerProtocol, name))
                actual = inspect.signature(getattr(Manager, name))
                # Strip 'self' from both sides; Protocol methods always
                # declare it, but the resolved Manager method may be a
                # bound function or classmethod.
                proto_params = [
                    p for p in proto.parameters.values() if p.name != "self"
                ]
                actual_params = [
                    p for p in actual.parameters.values() if p.name != "self"
                ]
                self.assertEqual(
                    [(p.name, p.kind) for p in proto_params],
                    [(p.name, p.kind) for p in actual_params],
                    f"Signature drift on {name}: protocol parameters "
                    f"{[(p.name, str(p.kind)) for p in proto_params]} "
                    f"vs. actual "
                    f"{[(p.name, str(p.kind)) for p in actual_params]}",
                )

    def test_protocol_keyword_defaults_match_Manager_implementations(self) -> None:
        # Pass-5 review surfaced a real drift class the parameter-only
        # comparison missed: ``_start_process_handle(..., *,
        # reset_collision_retry: bool = True)`` had the default repeated
        # in three places (helper, mixin wrapper, Protocol). Flipping
        # the helper's default to False would silently be re-passed as
        # True by the wrapper. This test catches that by comparing
        # the default value of every keyword-only (and keyword-or-positional
        # with a default) parameter on every Protocol method.
        #
        # We skip parameters with ``Parameter.empty`` defaults — those
        # are required and don't drift. We also skip ``*args`` /
        # ``**kwargs`` parameters (they carry no default).
        for name in _protocol_method_names():
            with self.subTest(method=name):
                proto = inspect.signature(getattr(ManagerProtocol, name))
                actual = inspect.signature(getattr(Manager, name))
                proto_defaults = {
                    p.name: p.default
                    for p in proto.parameters.values()
                    if p.name != "self"
                    and p.default is not inspect.Parameter.empty
                }
                actual_defaults = {
                    p.name: p.default
                    for p in actual.parameters.values()
                    if p.name != "self"
                    and p.default is not inspect.Parameter.empty
                }
                self.assertEqual(
                    proto_defaults,
                    actual_defaults,
                    f"Default-value drift on {name}: protocol declares "
                    f"defaults {proto_defaults} but Manager method has "
                    f"defaults {actual_defaults}. Update one to match "
                    f"the other so the wrapper doesn't silently re-pass "
                    f"a stale default.",
                )


if __name__ == "__main__":
    unittest.main()
