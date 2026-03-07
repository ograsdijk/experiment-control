from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils.yaml_helpers import load_yaml_text
from .ast import SequenceSpec, iter_use_ids, parse_sequence


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _normalize_string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list of strings")
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return tuple(out)


def _resolve_path(*, base_dir: Path, raw_path: str, field_name: str) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError(f"{field_name} path must not be empty")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    else:
        path = path.resolve()
    return path


def _display_path(path: Path, *, base_dir: Path) -> str:
    try:
        rel = path.relative_to(base_dir)
        return str(rel)
    except Exception:
        return str(path)


@dataclass(frozen=True)
class SequenceLibraryEntry:
    sequence_id: str
    path: str
    source: str
    label: str | None
    description: str | None
    tags: tuple[str, ...]
    editable_vars: tuple[str, ...]
    use_ids: tuple[str, ...]
    spec: SequenceSpec
    text: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.sequence_id,
            "path": self.path,
            "source": self.source,
            "label": self.label,
            "description": self.description,
            "tags": list(self.tags),
            "editable_vars": list(self.editable_vars),
            "vars": sorted(str(key) for key in self.spec.vars.keys()),
            "use_ids": list(self.use_ids),
        }


class SequenceLibrary:
    def __init__(
        self,
        *,
        manifest_path: str | Path,
        description_policy: str = "warn",
    ) -> None:
        path = Path(manifest_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"sequence library manifest not found: {path}")
        policy = str(description_policy or "warn").strip().lower()
        if policy not in {"off", "warn", "error"}:
            raise ValueError(
                "sequence library description_policy must be one of off|warn|error"
            )
        self._manifest_path = path
        self._description_policy = policy
        self._entries: dict[str, SequenceLibraryEntry] = {}
        self._warnings: list[str] = []

    @property
    def manifest_path(self) -> Path:
        return self._manifest_path

    @property
    def description_policy(self) -> str:
        return self._description_policy

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)

    def is_loaded(self) -> bool:
        return bool(self._entries)

    def reload(self) -> None:
        entries, warnings = self._load_entries()
        self._entries = entries
        self._warnings = warnings

    def has(self, sequence_id: str) -> bool:
        return str(sequence_id or "").strip() in self._entries

    def get_entry(self, sequence_id: str) -> SequenceLibraryEntry:
        key = str(sequence_id or "").strip()
        if not key:
            raise ValueError("sequence_id must not be empty")
        try:
            return self._entries[key]
        except KeyError:
            raise KeyError(f"unknown sequence library id {key!r}") from None

    def get_spec(self, sequence_id: str) -> SequenceSpec:
        return self.get_entry(sequence_id).spec

    def list_entries(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for sequence_id in sorted(self._entries.keys()):
            out.append(self._entries[sequence_id].to_public_dict())
        return out

    def _load_entries(self) -> tuple[dict[str, SequenceLibraryEntry], list[str]]:
        source = str(self._manifest_path)
        manifest_text = self._manifest_path.read_text(encoding="utf-8")
        raw = load_yaml_text(manifest_text, source=source)
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise TypeError("sequence library manifest root must be a dict")
        version = int(raw.get("version", 1))
        if version != 1:
            raise ValueError(
                f"unsupported sequence library manifest version {version}; expected 1"
            )

        base_dir = self._manifest_path.parent
        entries: dict[str, SequenceLibraryEntry] = {}
        warnings: list[str] = []

        explicit = raw.get("sequences", {}) or {}
        if not isinstance(explicit, dict):
            raise TypeError("sequence library manifest 'sequences' must be a dict")
        for raw_id, raw_entry in explicit.items():
            sequence_id = str(raw_id or "").strip()
            if not sequence_id:
                raise ValueError("sequence library explicit ids must be non-empty")
            if isinstance(raw_entry, str):
                entry_obj: dict[str, Any] = {"path": raw_entry}
            elif isinstance(raw_entry, dict):
                entry_obj = dict(raw_entry)
            else:
                raise TypeError(
                    f"sequence library entry {sequence_id!r} must be a dict or string path"
                )
            entry = self._build_entry(
                sequence_id=sequence_id,
                entry_obj=entry_obj,
                source="explicit",
                base_dir=base_dir,
            )
            if (
                self._description_policy in {"warn", "error"}
                and not entry.description
            ):
                message = (
                    f"sequence library entry {sequence_id!r} is missing description"
                )
                if self._description_policy == "error":
                    raise ValueError(message)
                warnings.append(message)
            entries[sequence_id] = entry

        autoload_dirs = raw.get("autoload_dirs", []) or []
        if not isinstance(autoload_dirs, list):
            raise TypeError("sequence library manifest 'autoload_dirs' must be a list")
        for index, raw_item in enumerate(autoload_dirs):
            item_label = f"autoload_dirs[{index}]"
            if not isinstance(raw_item, dict):
                raise TypeError(f"{item_label} must be a dict")
            raw_dir = str(raw_item.get("dir", "") or "").strip()
            if not raw_dir:
                raise ValueError(f"{item_label}.dir is required")
            pattern = str(raw_item.get("pattern", "*.yaml") or "*.yaml").strip()
            if not pattern:
                raise ValueError(f"{item_label}.pattern must not be empty")
            namespace = _normalize_optional_string(raw_item.get("namespace"))
            recursive = bool(raw_item.get("recursive", False))
            tags = _normalize_string_list(raw_item.get("tags"), field_name=f"{item_label}.tags")
            autoload_dir = _resolve_path(
                base_dir=base_dir,
                raw_path=raw_dir,
                field_name=f"{item_label}.dir",
            )
            if not autoload_dir.is_dir():
                raise FileNotFoundError(
                    f"{item_label}.dir does not exist or is not a directory: {autoload_dir}"
                )
            iterator = autoload_dir.rglob(pattern) if recursive else autoload_dir.glob(pattern)
            for path in sorted(iterator):
                if not path.is_file():
                    continue
                generated = self._generate_autoload_id(
                    path=path,
                    root_dir=autoload_dir,
                    namespace=namespace,
                )
                if not generated:
                    continue
                if generated in entries:
                    continue
                entry = self._build_entry(
                    sequence_id=generated,
                    entry_obj={"path": str(path)},
                    source="autoload",
                    base_dir=base_dir,
                    tags=tags,
                )
                entries[generated] = entry

        self._validate_use_graph(entries)
        return entries, warnings

    def _build_entry(
        self,
        *,
        sequence_id: str,
        entry_obj: dict[str, Any],
        source: str,
        base_dir: Path,
        tags: tuple[str, ...] = (),
    ) -> SequenceLibraryEntry:
        raw_path = str(entry_obj.get("path", "") or "").strip()
        if not raw_path:
            raise ValueError(f"sequence library entry {sequence_id!r} is missing path")
        path = _resolve_path(
            base_dir=base_dir,
            raw_path=raw_path,
            field_name=f"sequences[{sequence_id!r}]",
        )
        if not path.is_file():
            raise FileNotFoundError(
                f"sequence library entry {sequence_id!r} file does not exist: {path}"
            )

        text = path.read_text(encoding="utf-8")
        raw = load_yaml_text(text, source=str(path))
        try:
            spec = parse_sequence(raw)
        except Exception as exc:
            raise ValueError(
                f"sequence library entry {sequence_id!r} failed to parse: {exc}"
            ) from exc

        label = _normalize_optional_string(entry_obj.get("label"))
        description = _normalize_optional_string(entry_obj.get("description"))
        explicit_tags = _normalize_string_list(
            entry_obj.get("tags"), field_name=f"sequences[{sequence_id!r}].tags"
        )
        editable_vars = _normalize_string_list(
            entry_obj.get("editable_vars"),
            field_name=f"sequences[{sequence_id!r}].editable_vars",
        )
        use_ids = tuple(sorted(set(iter_use_ids(spec.steps))))
        return SequenceLibraryEntry(
            sequence_id=sequence_id,
            path=_display_path(path, base_dir=base_dir),
            source=source,
            label=label,
            description=description,
            tags=tuple([*tags, *explicit_tags]),
            editable_vars=editable_vars,
            use_ids=use_ids,
            spec=spec,
            text=text,
        )

    @staticmethod
    def _generate_autoload_id(
        *, path: Path, root_dir: Path, namespace: str | None
    ) -> str:
        rel = path.relative_to(root_dir).with_suffix("")
        parts = [str(item).strip() for item in rel.parts if str(item).strip()]
        if not parts:
            return ""
        generated = ".".join(parts)
        if namespace:
            return f"{namespace}.{generated}"
        return generated

    @staticmethod
    def _validate_use_graph(entries: dict[str, SequenceLibraryEntry]) -> None:
        graph: dict[str, list[str]] = {}
        for sequence_id, entry in entries.items():
            targets = sorted(set(str(item).strip() for item in entry.use_ids if str(item).strip()))
            graph[sequence_id] = targets
            for target in targets:
                if target not in entries:
                    raise ValueError(
                        f"sequence {sequence_id!r} references unknown use.id {target!r}"
                    )

        visited: dict[str, int] = {}
        stack: list[str] = []

        def visit(node: str) -> None:
            state = visited.get(node, 0)
            if state == 2:
                return
            if state == 1:
                if node in stack:
                    idx = stack.index(node)
                    cycle = [*stack[idx:], node]
                else:
                    cycle = [*stack, node]
                raise ValueError(
                    f"sequence library use cycle detected: {' -> '.join(cycle)}"
                )
            visited[node] = 1
            stack.append(node)
            for nxt in graph.get(node, []):
                visit(nxt)
            stack.pop()
            visited[node] = 2

        for node in sorted(graph.keys()):
            visit(node)
