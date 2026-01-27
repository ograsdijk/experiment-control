from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Literal

from ..utils.config_parsing import ConfigError, normalize_list, optional_str, require_dict, require_str

Json = dict[str, Any]
MeasurementFieldType = Literal["string", "number", "integer", "boolean"]
_MISSING = object()
_FIELD_TYPES: tuple[MeasurementFieldType, ...] = ("string", "number", "integer", "boolean")


@dataclass(frozen=True, slots=True)
class MeasurementField:
    key: str
    label: str
    field_type: MeasurementFieldType
    required: bool
    options: tuple[str, ...]
    allow_custom: bool
    has_default: bool
    default: Any
    placeholder: str | None
    description: str | None
    multiline: bool


@dataclass(frozen=True, slots=True)
class MeasurementProfile:
    profile_id: str
    label: str
    description: str | None
    fields: tuple[MeasurementField, ...]


@dataclass(frozen=True, slots=True)
class MeasurementNoteSchema:
    fields: tuple[MeasurementField, ...]


@dataclass(frozen=True, slots=True)
class MeasurementSchema:
    version: int
    profiles: tuple[MeasurementProfile, ...]
    notes: MeasurementNoteSchema


def _err(path: list[str | int], message: str) -> ConfigError:
    return ConfigError(path="".join(
        f"[{item}]" if isinstance(item, int) else (item if i == 0 else f".{item}")
        for i, item in enumerate(path)
    ) or "<root>", message=message)


def _parse_bool(raw: object, *, path: list[str | int], default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    raise _err(path, "must be a boolean")


def _parse_int(raw: object, *, path: list[str | int], default: int) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise _err(path, "must be an integer")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    raise _err(path, "must be an integer")


def _parse_options(raw: object, *, path: list[str | int]) -> tuple[str, ...]:
    if raw is None:
        return ()
    values = normalize_list(raw, path=path)
    out: list[str] = []
    seen: set[str] = set()
    for i, value in enumerate(values):
        if not isinstance(value, str):
            raise _err([*path, i], "must be a string")
        text = value.strip()
        if not text:
            raise _err([*path, i], "must be a non-empty string")
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def _coerce_boolean(value: object, *, context: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value in {0, 1}:
            return bool(int(value))
        raise ValueError(f"{context} must be a boolean")
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{context} must be a boolean")


def _coerce_number(value: object, *, context: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{context} must be a number")
    if isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{context} must be a number")
        try:
            out = float(text)
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError(f"{context} must be a number") from exc
    else:
        raise ValueError(f"{context} must be a number")
    if not math.isfinite(out):
        raise ValueError(f"{context} must be finite")
    return out


def _coerce_integer(value: object, *, context: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{context} must be an integer")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value.is_integer() and math.isfinite(value):
            return int(value)
        raise ValueError(f"{context} must be an integer")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{context} must be an integer")
        try:
            parsed = float(text)
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError(f"{context} must be an integer") from exc
        if not math.isfinite(parsed) or not parsed.is_integer():
            raise ValueError(f"{context} must be an integer")
        return int(parsed)
    raise ValueError(f"{context} must be an integer")


def _coerce_string(
    value: object,
    *,
    options: tuple[str, ...],
    allow_custom: bool,
    context: str,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{context} must be a non-empty string")
    if not options:
        return text
    if text in options:
        return text
    if allow_custom:
        return text
    opts = ", ".join(options)
    raise ValueError(f"{context} must be one of: {opts}")


def _coerce_value(
    *,
    field_type: MeasurementFieldType,
    value: object,
    options: tuple[str, ...],
    allow_custom: bool,
    context: str,
) -> object:
    if field_type == "string":
        return _coerce_string(
            value,
            options=options,
            allow_custom=allow_custom,
            context=context,
        )
    if field_type == "number":
        return _coerce_number(value, context=context)
    if field_type == "integer":
        return _coerce_integer(value, context=context)
    if field_type == "boolean":
        return _coerce_boolean(value, context=context)
    raise ValueError(f"{context} has unsupported field type {field_type!r}")


def _parse_field(raw: object, *, path: list[str | int]) -> MeasurementField:
    obj = require_dict(raw, path=path)
    key = require_str(obj.get("key"), path=[*path, "key"])
    type_raw = obj.get("type", obj.get("kind", "string"))
    if not isinstance(type_raw, str):
        raise _err([*path, "type"], f"must be one of {', '.join(_FIELD_TYPES)}")
    field_type = type_raw.strip().lower()
    if field_type not in _FIELD_TYPES:
        raise _err([*path, "type"], f"must be one of {', '.join(_FIELD_TYPES)}")
    label = optional_str(obj.get("label"), path=[*path, "label"]) or key
    required = _parse_bool(obj.get("required"), path=[*path, "required"], default=False)
    options = _parse_options(obj.get("options"), path=[*path, "options"])
    allow_custom = _parse_bool(
        obj.get("allow_custom"), path=[*path, "allow_custom"], default=False
    )
    placeholder = optional_str(obj.get("placeholder"), path=[*path, "placeholder"])
    description = optional_str(obj.get("description"), path=[*path, "description"])
    multiline = _parse_bool(obj.get("multiline"), path=[*path, "multiline"], default=False)
    has_default = "default" in obj
    default: object = None
    if has_default:
        try:
            default = _coerce_value(
                field_type=field_type,  # type: ignore[arg-type]
                value=obj.get("default"),
                options=options,
                allow_custom=allow_custom,
                context=f"{key}.default",
            )
        except ValueError as exc:
            raise _err([*path, "default"], str(exc)) from exc
    return MeasurementField(
        key=key,
        label=label,
        field_type=field_type,  # type: ignore[arg-type]
        required=required,
        options=options,
        allow_custom=allow_custom,
        has_default=has_default,
        default=default,
        placeholder=placeholder,
        description=description,
        multiline=multiline,
    )


def _default_note_fields() -> tuple[MeasurementField, ...]:
    return (
        MeasurementField(
            key="author",
            label="Author",
            field_type="string",
            required=True,
            options=(),
            allow_custom=True,
            has_default=False,
            default=None,
            placeholder=None,
            description=None,
            multiline=False,
        ),
        MeasurementField(
            key="kind",
            label="Kind",
            field_type="string",
            required=True,
            options=("note", "observation", "issue", "action"),
            allow_custom=False,
            has_default=True,
            default="note",
            placeholder=None,
            description=None,
            multiline=False,
        ),
        MeasurementField(
            key="message",
            label="Message",
            field_type="string",
            required=True,
            options=(),
            allow_custom=True,
            has_default=False,
            default=None,
            placeholder=None,
            description=None,
            multiline=True,
        ),
    )


def _parse_profiles(raw: object) -> tuple[MeasurementProfile, ...]:
    profiles_raw = raw
    out: list[MeasurementProfile] = []
    seen: set[str] = set()
    if profiles_raw is None:
        return tuple(out)

    if isinstance(profiles_raw, dict):
        items = list(profiles_raw.items())
        for i, (profile_id, profile_raw) in enumerate(items):
            profile_obj = require_dict(profile_raw, path=["profiles", profile_id])
            label = optional_str(profile_obj.get("label"), path=["profiles", profile_id, "label"]) or str(profile_id)
            description = optional_str(
                profile_obj.get("description"),
                path=["profiles", profile_id, "description"],
            )
            fields_raw = normalize_list(profile_obj.get("fields"), path=["profiles", profile_id, "fields"])
            fields: list[MeasurementField] = []
            field_keys: set[str] = set()
            for j, field_raw in enumerate(fields_raw):
                field = _parse_field(field_raw, path=["profiles", profile_id, "fields", j])
                if field.key in field_keys:
                    raise _err(["profiles", profile_id, "fields", j, "key"], "duplicate key")
                field_keys.add(field.key)
                fields.append(field)
            profile_id_text = str(profile_id).strip()
            if not profile_id_text:
                raise _err(["profiles", i], "profile id must be non-empty")
            if profile_id_text in seen:
                raise _err(["profiles", profile_id_text], "duplicate profile id")
            seen.add(profile_id_text)
            out.append(
                MeasurementProfile(
                    profile_id=profile_id_text,
                    label=label,
                    description=description,
                    fields=tuple(fields),
                )
            )
        return tuple(out)

    profile_items = normalize_list(profiles_raw, path=["profiles"])
    for i, profile_raw in enumerate(profile_items):
        obj = require_dict(profile_raw, path=["profiles", i])
        profile_id = require_str(obj.get("id"), path=["profiles", i, "id"])
        if profile_id in seen:
            raise _err(["profiles", i, "id"], "duplicate profile id")
        seen.add(profile_id)
        label = optional_str(obj.get("label"), path=["profiles", i, "label"]) or profile_id
        description = optional_str(obj.get("description"), path=["profiles", i, "description"])
        fields_raw = normalize_list(obj.get("fields"), path=["profiles", i, "fields"])
        fields: list[MeasurementField] = []
        field_keys: set[str] = set()
        for j, field_raw in enumerate(fields_raw):
            field = _parse_field(field_raw, path=["profiles", i, "fields", j])
            if field.key in field_keys:
                raise _err(["profiles", i, "fields", j, "key"], "duplicate key")
            field_keys.add(field.key)
            fields.append(field)
        out.append(
            MeasurementProfile(
                profile_id=profile_id,
                label=label,
                description=description,
                fields=tuple(fields),
            )
        )
    return tuple(out)


def _parse_notes(raw: object) -> MeasurementNoteSchema:
    if raw is None:
        return MeasurementNoteSchema(fields=_default_note_fields())
    obj = require_dict(raw, path=["notes"])
    fields_raw = obj.get("fields")
    if fields_raw is None:
        return MeasurementNoteSchema(fields=_default_note_fields())
    items = normalize_list(fields_raw, path=["notes", "fields"])
    fields: list[MeasurementField] = []
    seen: set[str] = set()
    for i, field_raw in enumerate(items):
        field = _parse_field(field_raw, path=["notes", "fields", i])
        if field.key in seen:
            raise _err(["notes", "fields", i, "key"], "duplicate key")
        seen.add(field.key)
        fields.append(field)

    by_key = {field.key: field for field in fields}
    if "author" not in by_key:
        raise _err(["notes", "fields"], "must include an 'author' field")
    if by_key["author"].field_type != "string":
        raise _err(["notes", "fields"], "'author' must be type string")
    if "message" not in by_key:
        raise _err(["notes", "fields"], "must include a 'message' field")
    if by_key["message"].field_type != "string":
        raise _err(["notes", "fields"], "'message' must be type string")
    if "kind" not in by_key:
        fields.append(
            MeasurementField(
                key="kind",
                label="Kind",
                field_type="string",
                required=True,
                options=("note", "observation", "issue", "action"),
                allow_custom=False,
                has_default=True,
                default="note",
                placeholder=None,
                description=None,
                multiline=False,
            )
        )
    return MeasurementNoteSchema(fields=tuple(fields))


def measurement_schema_from_json(raw: object) -> MeasurementSchema:
    obj = require_dict(raw, path=[])
    version = _parse_int(obj.get("version"), path=["version"], default=1)
    if version <= 0:
        raise _err(["version"], "must be a positive integer")
    profiles = _parse_profiles(obj.get("profiles"))
    notes = _parse_notes(obj.get("notes"))
    return MeasurementSchema(version=version, profiles=profiles, notes=notes)


def _field_to_json(field: MeasurementField) -> Json:
    out: Json = {
        "key": field.key,
        "label": field.label,
        "type": field.field_type,
        "required": bool(field.required),
        "allow_custom": bool(field.allow_custom),
        "options": list(field.options),
        "multiline": bool(field.multiline),
    }
    if field.placeholder is not None:
        out["placeholder"] = field.placeholder
    if field.description is not None:
        out["description"] = field.description
    if field.has_default:
        out["default"] = field.default
    return out


def measurement_schema_to_json(schema: MeasurementSchema) -> Json:
    return {
        "version": int(schema.version),
        "profiles": [
            {
                "id": profile.profile_id,
                "label": profile.label,
                "description": profile.description,
                "fields": [_field_to_json(field) for field in profile.fields],
            }
            for profile in schema.profiles
        ],
        "notes": {
            "fields": [_field_to_json(field) for field in schema.notes.fields],
        },
    }


def _lookup_value(values: Json, key: str) -> object:
    if key in values:
        return values[key]
    if "." not in key:
        return _MISSING
    cur: object = values
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _assign_nested(target: Json, key: str, value: object) -> None:
    parts = [part for part in key.split(".") if part]
    if not parts:
        return
    cur = target
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _normalize_field_value(
    *,
    field: MeasurementField,
    raw_value: object,
    context: str,
) -> object:
    value = raw_value
    if value is _MISSING or (isinstance(value, str) and not value.strip()):
        if field.has_default:
            value = field.default
        elif field.required:
            raise ValueError(f"{context} is required")
        else:
            return _MISSING
    return _coerce_value(
        field_type=field.field_type,
        value=value,
        options=field.options,
        allow_custom=field.allow_custom,
        context=context,
    )


def normalize_measurement_values(
    schema: MeasurementSchema,
    *,
    profile_id: str,
    values: object,
) -> tuple[MeasurementProfile, Json, Json]:
    profile = next((p for p in schema.profiles if p.profile_id == profile_id), None)
    if profile is None:
        raise ValueError(f"unknown measurement profile {profile_id!r}")
    if values is None:
        raw_values: Json = {}
    elif isinstance(values, dict):
        raw_values = values
    else:
        raise ValueError("measurement_values must be a dict")

    flat: Json = {}
    nested: Json = {}
    for field in profile.fields:
        raw = _lookup_value(raw_values, field.key)
        value = _normalize_field_value(
            field=field,
            raw_value=raw,
            context=f"measurement_values.{field.key}",
        )
        if value is _MISSING:
            continue
        flat[field.key] = value
        _assign_nested(nested, field.key, value)
    return profile, flat, nested


def normalize_measurement_note_values(
    schema: MeasurementSchema,
    *,
    values: object,
) -> tuple[Json, Json]:
    if values is None:
        raw_values: Json = {}
    elif isinstance(values, dict):
        raw_values = values
    else:
        raise ValueError("note params must be a dict")

    normalized: Json = {}
    known_keys: set[str] = set()
    for field in schema.notes.fields:
        known_keys.add(field.key)
        raw = _lookup_value(raw_values, field.key)
        value = _normalize_field_value(
            field=field,
            raw_value=raw,
            context=f"note.{field.key}",
        )
        if value is _MISSING:
            continue
        normalized[field.key] = value

    kind = normalized.get("kind")
    if kind is None:
        normalized["kind"] = "note"

    author = normalized.get("author")
    message = normalized.get("message")
    if not isinstance(author, str) or not author.strip():
        raise ValueError("note.author is required")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("note.message is required")
    if "kind" in normalized and (
        not isinstance(normalized["kind"], str) or not str(normalized["kind"]).strip()
    ):
        raise ValueError("note.kind must be a non-empty string")

    core: Json = {
        "author": str(author).strip(),
        "kind": str(normalized.get("kind", "note")).strip() or "note",
        "message": str(message),
    }
    payload: Json = {}
    for key, value in normalized.items():
        if key in {"author", "kind", "message"}:
            continue
        payload[key] = value

    for key, value in raw_values.items():
        if key in known_keys:
            continue
        if isinstance(key, str) and key.startswith("_"):
            continue
        payload[str(key)] = value

    return core, payload

