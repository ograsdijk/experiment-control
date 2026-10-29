"""Validation of a sequence's top-level `form:` block.

The block describes the Sequencer UI's run form for the sequence. The engine
never reads it while running: every field only sets a declared var for the
next start (`vars_override`). It is checked at load so a typo in a var name
fails here, not as a form that silently sets nothing. See
docs/sequencer_run_form.md for the schema.
"""

from __future__ import annotations

from typing import Any

FIELD_TYPES = ("checkbox", "number", "integer", "text", "select", "chips", "target", "range")

_FORM_KEYS = {"title", "presets", "summary", "sections"}
_SECTION_KEYS = {"title", "toggle", "fields"}
_SUMMARY_KEYS = {"points", "passes", "bursts", "shots"}
_COMMON_FIELD_KEYS = {"type", "label", "enabled_by"}
_FIELD_KEYS: dict[str, set[str]] = {
    "checkbox": {"var"},
    "number": {"var", "min", "max", "step", "unit"},
    "integer": {"var", "min", "max", "unit"},
    "text": {"var"},
    "select": {"var", "options", "options_call", "empty_label"},
    "chips": {"var", "options"},
    "target": {"var", "catalog", "other_exclude"},
    "range": {"target", "relative", "from", "to", "points"},
}
_OPTIONS_CALL_KEYS = {"device", "action", "params", "list", "value", "label"}


def _require_dict(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{where} must be a dict")
    return value


def _check_keys(obj: dict[str, Any], where: str, allowed: set[str]) -> None:
    unknown = sorted(str(key) for key in obj if key not in allowed)
    if unknown:
        raise TypeError(f"{where} has unknown key(s): {', '.join(unknown)}")


def _check_var(name: Any, where: str, vars_map: dict[str, Any]) -> str:
    if not isinstance(name, str) or not name.strip():
        raise TypeError(f"{where} must name a var")
    if name not in vars_map:
        raise TypeError(f"{where} names {name!r}, which is not declared in vars")
    return name


def _check_field(field: Any, where: str, vars_map: dict[str, Any]) -> None:
    obj = _require_dict(field, where)
    kind = obj.get("type")
    if kind not in FIELD_TYPES:
        raise TypeError(f"{where}.type must be one of {', '.join(FIELD_TYPES)}, got {kind!r}")
    _check_keys(obj, where, _COMMON_FIELD_KEYS | _FIELD_KEYS[kind])
    if "enabled_by" in obj:
        _check_var(obj["enabled_by"], f"{where}.enabled_by", vars_map)
    if kind == "range":
        for key in ("target", "relative", "from", "to", "points"):
            _check_var(obj.get(key), f"{where}.{key}", vars_map)
        return
    _check_var(obj.get("var"), f"{where}.var", vars_map)
    if kind == "target":
        catalog = _check_var(obj.get("catalog"), f"{where}.catalog", vars_map)
        if not isinstance(vars_map[catalog], list):
            raise TypeError(f"{where}.catalog {catalog!r} must be a list of targets")
        exclude = obj.get("other_exclude", [])
        if not isinstance(exclude, list) or not all(isinstance(d, str) for d in exclude):
            raise TypeError(f"{where}.other_exclude must be a list of device ids")
    if kind == "chips" or (kind == "select" and "options_call" not in obj):
        options = _check_var(obj.get("options"), f"{where}.options", vars_map)
        _check_options(vars_map[options], f"{where}.options ({options})")
    if kind == "select" and "options_call" in obj:
        if "options" in obj:
            raise TypeError(f"{where} sets both options and options_call")
        call = _require_dict(obj["options_call"], f"{where}.options_call")
        _check_keys(call, f"{where}.options_call", _OPTIONS_CALL_KEYS)
        for key in ("device", "action"):
            if not isinstance(call.get(key), str) or not call[key].strip():
                raise TypeError(f"{where}.options_call.{key} must be a non-empty string")


def _check_options(options: Any, where: str) -> None:
    if not isinstance(options, list):
        raise TypeError(f"{where} must be a list of {{value, label}}")
    for index, option in enumerate(options):
        if not isinstance(option, dict) or "value" not in option:
            raise TypeError(f"{where}[{index}] must be a dict with a value")


def validate_run_form(raw: Any, vars_map: dict[str, Any]) -> dict[str, Any]:
    form = _require_dict(raw, "form")
    _check_keys(form, "form", _FORM_KEYS)
    sections = form.get("sections")
    if not isinstance(sections, list) or not sections:
        raise TypeError("form.sections must be a non-empty list")
    for s_index, section_raw in enumerate(sections):
        where = f"form.sections[{s_index}]"
        section = _require_dict(section_raw, where)
        _check_keys(section, where, _SECTION_KEYS)
        if "toggle" in section:
            _check_var(section["toggle"], f"{where}.toggle", vars_map)
        fields = section.get("fields")
        if not isinstance(fields, list) or not fields:
            raise TypeError(f"{where}.fields must be a non-empty list")
        for f_index, field in enumerate(fields):
            _check_field(field, f"{where}.fields[{f_index}]", vars_map)
    for p_index, preset_raw in enumerate(form.get("presets", []) or []):
        where = f"form.presets[{p_index}]"
        preset = _require_dict(preset_raw, where)
        _check_keys(preset, where, {"label", "set"})
        for name in _require_dict(preset.get("set"), f"{where}.set"):
            _check_var(name, f"{where}.set", vars_map)
    if "summary" in form:
        summary = _require_dict(form["summary"], "form.summary")
        _check_keys(summary, "form.summary", _SUMMARY_KEYS)
        for key, name in summary.items():
            _check_var(name, f"form.summary.{key}", vars_map)
    return form
