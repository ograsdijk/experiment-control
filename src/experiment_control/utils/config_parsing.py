from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, TypeVar

Json = dict[str, Any]


@dataclass(frozen=True)
class ConfigError(ValueError):
    path: str
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.path}: {self.message}"


def _fmt_path(parts: Iterable[str | int]) -> str:
    out: list[str] = []
    for p in parts:
        if isinstance(p, int):
            out.append(f"[{p}]")
        else:
            if not out:
                out.append(p)
            else:
                out.append(f".{p}")
    return "".join(out) if out else "<root>"


def _err(parts: list[str | int], msg: str) -> ConfigError:
    return ConfigError(path=_fmt_path(parts), message=msg)


def normalize_list(raw: object, *, path: list[str | int]) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    raise _err(path, "must be a JSON list")


def require_dict(raw: object, *, path: list[str | int]) -> Json:
    if not isinstance(raw, dict):
        raise _err(path, "must be an object/dict")
    return raw


def optional_dict(raw: object, *, path: list[str | int]) -> Json:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _err(path, "must be an object/dict")
    return raw


def require_str(raw: object, *, path: list[str | int]) -> str:
    if not isinstance(raw, str) or not raw:
        raise _err(path, "must be a non-empty string")
    return raw


def optional_str(raw: object, *, path: list[str | int]) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return str(raw)


Kind = Literal["scalar", "index", "key", "attr"]


def require_kind(raw: object, *, path: list[str | int]) -> Kind:
    if raw is None:
        return "scalar"
    if not isinstance(raw, str):
        raise _err(path, "must be a string")
    if raw not in {"scalar", "index", "key", "attr"}:
        raise _err(path, "must be one of scalar/index/key/attr")
    return raw  # type: ignore[return-value]


_T = TypeVar("_T")


def require_list_of_dicts(raw: object, *, path: list[str | int]) -> list[Json]:
    items = normalize_list(raw, path=path)
    out: list[Json] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise _err([*path, i], "must be an object/dict")
        out.append(item)
    return out
