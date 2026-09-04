"""Write a YAML file back without destroying what a person put in it.

The workspace store is a hand-written config: comments explaining what a
node is for, blank lines grouping related entries, flow-style one-liners
(``- {output_id: x, node_id: y}``) that keep a long list readable.
Dumping the in-memory model over it with ``yaml.safe_dump`` loses all of
that on the first save made from the UI.

This module patches the *existing document* instead. The file on disk is
loaded in ruamel's round-trip mode, which keeps comments and formatting
attached to the nodes they belong to; only values that actually differ
are assigned, so every untouched line is re-emitted verbatim.

What survives: comments, blank-line grouping, flow vs block style,
quoting, and key order.

What does not: whitespace padding *inside* a flow mapping (ruamel
re-emits ``{a: 1,    b: 2}`` as ``{a: 1, b: 2}``), and the position of an
entry that was reordered in memory — items are matched to their existing
node by identity and patched where they sit, so a reorder is not written
back. Both are deliberate: comments are anchored by position, and moving
nodes around is how you orphan them.

Falls back to a plain ``safe_dump`` whenever the round trip cannot be
trusted: no existing file, ruamel missing, a document that will not
parse, or a result that does not read back as the payload it was meant
to encode. A save must never fail because formatting could not be kept.
"""

from __future__ import annotations

import io
import re
from typing import Any

# Keys that identify an item within a list, most specific first. Matching
# on these is what lets an edit find the node it belongs to instead of
# rewriting the list positionally and dragging every comment one slot up.
_IDENTITY_KEYS = ("workspace_id", "node_id", "output_id", "id", "name")

_DEFAULT_INDENT = {"mapping": 2, "sequence": 4, "offset": 2}

_KEY_LINE = re.compile(r"^(\s*)[^\s#-][^:]*:\s*(#.*)?$")
_ANY_KEY_LINE = re.compile(r"^(\s*)[^\s#-][^:]*:")
_DASH_LINE = re.compile(r"^(\s*)-\s")


def _plain_dump(payload: Any) -> str:
    try:
        import yaml  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - dependency error
        raise RuntimeError(f"PyYAML missing: {exc}") from exc
    return str(yaml.safe_dump(payload, sort_keys=False))


def _plain_load(text: str) -> Any:
    import yaml  # type: ignore[import-not-found]

    return yaml.safe_load(text)


def detect_indent(text: str) -> dict[str, int]:
    """Read the file's own indentation style out of its first few nodes.

    ruamel applies one indent style to the whole document, so guessing
    wrong rewrites every line. The defaults (2/4/2) match the workspace
    store; a file written in another style is measured instead.
    """
    indent = dict(_DEFAULT_INDENT)
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for idx, line in enumerate(lines[:-1]):
        parent = _KEY_LINE.match(line)
        if parent is None:
            continue
        dash = _DASH_LINE.match(lines[idx + 1])
        if dash is None:
            continue
        parent_indent = len(parent.group(1))
        dash_indent = len(dash.group(1))
        if dash_indent >= parent_indent:
            indent["offset"] = dash_indent - parent_indent
            # Content of a block sequence item starts just after "- ".
            indent["sequence"] = dash_indent + 2 - parent_indent
            break
    for idx, line in enumerate(lines[:-1]):
        parent = _KEY_LINE.match(line)
        if parent is None:
            continue
        following = lines[idx + 1]
        if _DASH_LINE.match(following):
            continue
        child = _ANY_KEY_LINE.match(following)
        if child is None:
            continue
        parent_indent = len(parent.group(1))
        child_indent = len(child.group(1))
        if child_indent > parent_indent:
            indent["mapping"] = child_indent - parent_indent
            break
    return indent


def _round_trip_yaml(text: str) -> Any:
    from ruamel.yaml import YAML  # type: ignore[import-not-found]

    yaml = YAML()
    yaml.preserve_quotes = True
    # Long flow mappings are how these files stay readable; re-wrapping
    # them at 80 columns would be its own kind of destruction.
    yaml.width = 4096
    indent = detect_indent(text)
    yaml.indent(
        mapping=indent["mapping"],
        sequence=indent["sequence"],
        offset=indent["offset"],
    )
    yaml.explicit_start = text.lstrip().startswith("---")
    return yaml


def _identity_key(existing: Any, payload: list[Any]) -> str | None:
    """The key both lists agree identifies an item, if there is one."""
    if not payload or not isinstance(existing, list) or not existing:
        return None
    if not all(isinstance(item, dict) for item in payload):
        return None
    if not all(isinstance(item, dict) for item in existing):
        return None
    for key in _IDENTITY_KEYS:
        payload_ids = [item.get(key) for item in payload]
        existing_ids = [item.get(key) for item in existing]
        if any(value is None for value in payload_ids + existing_ids):
            continue
        # Duplicate ids make the match ambiguous; positional is safer.
        if len({str(value) for value in payload_ids}) != len(payload_ids):
            continue
        if len({str(value) for value in existing_ids}) != len(existing_ids):
            continue
        return key
    return None


def _patch(node: Any, payload: Any) -> Any:
    """Return `node` updated to hold `payload`, reusing it where possible.

    Returning the *same object* is what preserves formatting: a replaced
    node is re-emitted from scratch, an updated one keeps its comments,
    its quotes and its flow style.
    """
    if isinstance(payload, dict) and isinstance(node, dict):
        _patch_mapping(node, payload)
        return node
    if isinstance(payload, list) and isinstance(node, list):
        _patch_sequence(node, payload)
        return node
    if type(node) is type(payload) and node == payload:
        return node
    return payload


def _assign(container: Any, key: Any, value: Any) -> None:
    """Write only when the patch produced a different object.

    Re-assigning the node already in place is not a no-op for ruamel: it
    can drop the formatting the round trip exists to keep.
    """
    if container[key] is not value:
        container[key] = value


def _patch_mapping(node: Any, payload: dict[str, Any]) -> None:
    for key in [key for key in node if key not in payload]:
        del node[key]
    for key, value in payload.items():
        if key in node:
            _assign(node, key, _patch(node[key], value))
        else:
            node[key] = value


def _patch_sequence(node: Any, payload: list[Any]) -> None:
    key = _identity_key(node, payload)
    if key is None:
        _patch_sequence_positional(node, payload)
        return
    by_id = {str(item.get(key)): item for item in payload}
    for idx in range(len(node) - 1, -1, -1):
        if str(node[idx].get(key)) not in by_id:
            del node[idx]
    seen: set[str] = set()
    for idx in range(len(node)):
        ident = str(node[idx].get(key))
        seen.add(ident)
        _assign(node, idx, _patch(node[idx], by_id[ident]))
    for item in payload:
        if str(item.get(key)) not in seen:
            node.append(item)


def _patch_sequence_positional(node: Any, payload: list[Any]) -> None:
    for idx in range(len(node) - 1, len(payload) - 1, -1):
        del node[idx]
    for idx, value in enumerate(payload):
        if idx < len(node):
            _assign(node, idx, _patch(node[idx], value))
        else:
            node.append(value)


def _canonical(value: Any) -> Any:
    """Order-insensitive view of an identity-keyed list, for comparison.

    Items are patched where they already sit, so a list reordered in
    memory comes back in the file's order. That is not a difference in
    content, and the verification below must not read it as one.
    """
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        key = _identity_key(value, value)
        if key is not None:
            return {
                str(item.get(key)): _canonical(item) for item in value
            }
        return [_canonical(item) for item in value]
    return value


def dump_yaml_preserving(payload: Any, existing_text: str | None) -> str:
    """Render `payload` as YAML, keeping the formatting of `existing_text`.

    `existing_text` is the file's current content, or None/blank when it
    does not exist yet. The result always encodes exactly `payload`: if
    the patched document would not read back as such, the plain dump is
    returned instead.
    """
    if not existing_text or not existing_text.strip():
        return _plain_dump(payload)
    try:
        yaml = _round_trip_yaml(existing_text)
        document = yaml.load(existing_text)
    except Exception:
        return _plain_dump(payload)
    if not isinstance(document, type(payload)):
        return _plain_dump(payload)
    try:
        patched = _patch(document, payload)
        buffer = io.StringIO()
        yaml.dump(patched, buffer)
        text = buffer.getvalue()
    except Exception:
        return _plain_dump(payload)
    # The patch walks a tree it did not build; a mismatch here means a
    # shape it does not handle, and a wrong config file is far worse
    # than a reformatted one.
    try:
        if _canonical(_plain_load(text)) != _canonical(payload):
            return _plain_dump(payload)
    except Exception:
        return _plain_dump(payload)
    return text
