# ruff: noqa: E402

"""Phase 8 owned-state drift coverage.

Each migrated mixin declares the ``Manager`` attributes it reads as
class-level type annotations (so mypy can type-check the mixin
method bodies without inheriting from ``Manager``). The companion
:mod:`experiment_control.manager` assigns those same attributes in
``__init__``. When the two type declarations disagree silently —
e.g., the mixin says ``list[Json]`` but ``Manager.__init__`` allocates
``deque[Json]`` — neither mypy nor runtime catches the drift, but a
future caller relying on a list-only operation (slicing, indexing)
will crash.

This test parses ``manager.py``'s ``__init__`` body for explicit
``self.<name>: <type> = ...`` annotations and asserts each matching
mixin-declared annotation agrees. Fields that ``Manager`` initialises
without an explicit type annotation are ignored — the mixin
annotation is the source of truth in that case. This catches the
specific class of drift that prompted the mixin cleanup
(``LogsMixin._log_history`` declared ``list`` while Manager allocated
``deque``).
"""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import Manager


def _normalize(annotation: str) -> str:
    """Collapse whitespace + strip quotes so equivalent strings compare equal.

    Forward references can be quoted either at the whole-annotation
    level (``"deque[Json]"``) or at the parameter level
    (``dict[str, 'DeviceHandle']``). Both forms map to the same
    runtime type, so we strip all double- and single-quotes after
    whitespace collapse — making ``dict[str,'DeviceHandle']`` and
    ``dict[str,DeviceHandle]`` compare equal.
    """
    text = re.sub(r"\s+", "", annotation)
    text = text.replace('"', "").replace("'", "")
    return text


def _explicit_init_annotations() -> dict[str, str]:
    """Return ``{attr_name: type_text}`` for every ``self.<name>: <type> = ...``
    line in ``Manager.__init__`` (and its helper methods like ``_bind_caches``
    that ``ManagerCaches.bind_to_manager`` invokes — actually only direct
    ``__init__`` body for now, matching the user's audit scope).
    """
    source = (SRC / "experiment_control" / "manager.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        target = node.target
        if not isinstance(target, ast.Attribute):
            continue
        if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
            continue
        attr = target.attr
        # ast.unparse preserves the original source-level type text.
        type_text = ast.unparse(node.annotation)
        out[attr] = type_text
    return out


def _mixin_owned_state(cls: type) -> dict[str, str]:
    """Return ``{attr_name: type_text}`` for class-level annotations on
    a single mixin class (excluding inherited annotations)."""
    raw = cls.__dict__.get("__annotations__", {})
    out: dict[str, str] = {}
    for name, ann in raw.items():
        out[name] = str(ann)
    return out


# Mixins migrated in Phase 8.2.1 through 8.2.7. Listed explicitly
# (rather than discovered via MRO) so the test fails loudly if a
# mixin is renamed without being added/removed here.
_MIGRATED_MIXINS = (
    "PubSubMixin",
    "CommandJournalMixin",
    "LogEventsMixin",
    "LogsMixin",
    "RuntimeMetadataMixin",
    "InternalRpcMixin",
    "RequestRoutingMixin",
)


class MixinOwnedStateDriftTests(unittest.TestCase):
    def test_every_migrated_mixin_state_agrees_with_Manager_init(self) -> None:
        init_anns = _explicit_init_annotations()

        # Build a name->mixin map by walking Manager's MRO.
        mixins_in_mro = {
            cls.__name__: cls
            for cls in Manager.__mro__
            if cls.__name__ in _MIGRATED_MIXINS
        }

        # Sanity: every migrated mixin we claim exists is actually in the MRO.
        for name in _MIGRATED_MIXINS:
            self.assertIn(
                name,
                mixins_in_mro,
                f"{name} declared as migrated but not in Manager.__mro__",
            )

        for mixin_name in _MIGRATED_MIXINS:
            mixin_cls = mixins_in_mro[mixin_name]
            mixin_anns = _mixin_owned_state(mixin_cls)
            for attr, mixin_type in mixin_anns.items():
                init_type = init_anns.get(attr)
                if init_type is None:
                    # Manager initialises without an explicit annotation
                    # (assignment-only). Mixin annotation wins — nothing
                    # to compare against.
                    continue
                with self.subTest(mixin=mixin_name, attr=attr):
                    self.assertEqual(
                        _normalize(mixin_type),
                        _normalize(init_type),
                        f"Type drift on {mixin_name}.{attr}: mixin declares "
                        f"{mixin_type!r} but Manager.__init__ annotates "
                        f"{init_type!r}",
                    )


if __name__ == "__main__":
    unittest.main()
