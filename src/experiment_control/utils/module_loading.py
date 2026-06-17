from __future__ import annotations

from pathlib import Path

__all__ = ["module_name_from_path"]


def module_name_from_path(path: Path) -> tuple[str | None, Path | None]:
    """Infer a dotted module name for ``path`` by walking up ``__init__.py`` files.

    Returns ``(module_name, package_root)`` where ``package_root`` is the
    directory that must be on ``sys.path`` for ``module_name`` to import, or
    ``(None, None)`` if ``path`` is not inside an importable package.

    This lets a driver/process file that is loaded by path (e.g. resolved from a
    ``module:``/``file:`` config entry) be imported under its real dotted name so
    that relative imports inside it (``from ._helpers import x``) resolve.
    """
    parts: list[str] = []
    cur = path.parent
    while (cur / "__init__.py").exists():
        parts.append(cur.name)
        cur = cur.parent
    if not parts:
        return None, None
    module_name = ".".join(list(reversed(parts)) + [path.stem])
    return module_name, cur
