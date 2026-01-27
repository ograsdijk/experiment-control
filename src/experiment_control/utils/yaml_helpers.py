from __future__ import annotations

from pathlib import Path
from typing import Any


def _import_yaml() -> Any:
    try:
        import yaml  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover - dependency error
        raise RuntimeError(f"PyYAML missing: {e}") from e
    return yaml


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
        self.source = source
        self.line = line
        self.column = column


def load_yaml_text(text: str, *, source: str) -> Any:
    yaml = _import_yaml()
    try:
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
        raise YamlLoadError(
            str(e),
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
