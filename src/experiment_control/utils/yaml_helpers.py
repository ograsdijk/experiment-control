from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _import_yaml() -> Any:
    try:
        import yaml  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover - dependency error
        raise RuntimeError(f"PyYAML missing: {e}") from e
    return yaml


_YAML12_LOADER: Any = None


def _yaml12_loader() -> Any:
    """SafeLoader that also reads YAML 1.2 floats such as `2.5e6` and `1e-3`.

    PyYAML implements YAML 1.1, which only resolves a float when the exponent
    carries an explicit sign (`2.5e+6`); `2.5e6` and `1e-3` load as strings.
    YAML 1.2 parsers (e.g. the web editor's) read them as numbers, so
    sequence files must resolve them the same way.
    """
    global _YAML12_LOADER
    if _YAML12_LOADER is None:
        yaml = _import_yaml()
        float_re = re.compile(
            r"^[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)[eE][-+]?[0-9]+$"
        )

        class _Yaml12Loader(yaml.SafeLoader):  # type: ignore[misc]
            pass

        _Yaml12Loader.add_implicit_resolver(
            "tag:yaml.org,2002:float", float_re, list("-+0123456789.")
        )
        _YAML12_LOADER = _Yaml12Loader
    return _YAML12_LOADER


class YamlLoadError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        source: str,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        parts = [source]
        if line is not None and column is not None:
            parts.append(f"line {line}, column {column}")
        parts.append(message)
        super().__init__(": ".join(parts))
        self.detail = message
        self.source = source
        self.line = line
        self.column = column


def load_yaml_text(text: str, *, source: str, yaml12_floats: bool = False) -> Any:
    yaml = _import_yaml()
    try:
        if yaml12_floats:
            return yaml.load(text, Loader=_yaml12_loader())  # noqa: S506 - SafeLoader subclass
        return yaml.safe_load(text)
    except Exception as e:
        line: int | None = None
        column: int | None = None
        mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
        if mark is not None:
            mark_line = getattr(mark, "line", None)
            mark_column = getattr(mark, "column", None)
            if isinstance(mark_line, int):
                line = mark_line + 1
            if isinstance(mark_column, int):
                column = mark_column + 1
        problem = getattr(e, "problem", None)
        context = getattr(e, "context", None)
        if problem:
            detail = str(problem)
            if context:
                detail += f" ({context})"
        else:
            detail = str(e).splitlines()[0] if str(e) else "invalid YAML"
        raise YamlLoadError(
            detail,
            source=source,
            line=line,
            column=column,
        ) from None


def load_yaml_file(path: str | Path, *, return_text: bool = False) -> Any:
    config_path = Path(path).expanduser().resolve()
    yaml_text = config_path.read_text(encoding="utf-8")
    raw = load_yaml_text(yaml_text, source=str(config_path))
    if return_text:
        return raw, yaml_text
    return raw
