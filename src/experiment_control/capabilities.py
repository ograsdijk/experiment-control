from __future__ import annotations

from typing import Any

from .types import MemberParamSpec, MemberSpec

Json = dict[str, Any]


def member_to_json(m: MemberSpec) -> dict[str, object]:
    return {
        "name": m.name,
        "kind": m.kind,
        "readable": m.readable,
        "settable": m.settable,
        "value_annotation": m.value_annotation,
        "doc": m.doc,
        "params": [
            {
                "name": p.name,
                "kind": p.kind,
                "required": p.required,
                "default": p.default,
                "annotation": p.annotation,
            }
            for p in (m.params or [])
        ]
        if m.params is not None
        else None,
        "return_annotation": m.return_annotation,
        "source": m.source,
    }


def capabilities_payload(members: list[MemberSpec]) -> Json:
    return {"version": 1, "members": [member_to_json(m) for m in members]}


def param(
    name: str,
    *,
    required: bool,
    default: object | None,
    annotation: str | None,
    kind: str = "POSITIONAL_OR_KEYWORD",
) -> MemberParamSpec:
    return MemberParamSpec(
        name=name,
        kind=kind,
        required=required,
        default=default,
        annotation=annotation,
    )


def method(
    name: str,
    *,
    params: list[MemberParamSpec] | None,
    doc: str | None,
    return_annotation: str | None = None,
    source: str = "process",
) -> MemberSpec:
    return MemberSpec(
        name=name,
        kind="method",
        readable=True,
        settable=False,
        value_annotation=None,
        doc=doc,
        params=params,
        return_annotation=return_annotation,
        source=source,
    )
