"""Whole-sequence plan behind `sequencer.plan` (the UI's timeline).

`SequencerRuntime.plan()` walks the loaded sequence from its first step with
the counting walk the progress estimate uses (`SequencerRuntime._walk_steps`),
in a mode that keeps going past what the estimate gives up on and records,
per step, how sure its count is. This module holds the parts that don't need
the runtime: the certainty levels, naming every step (including the steps of
`use:` sub-sequences), and finding the call a loop's list comes from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .ast import (
    AssignStep,
    CallStep,
    SequenceSpec,
    Step,
    UseStep,
)
from .source_info import step_bodies

# How sure a step's count is, from best to worst:
# - exact: follows from the YAML and its vars.
# - inferred: a loop over a list a call returns, counted as one item per
#   entry of the list that call was given (see `call_list_sources`).
# - approximate: an `if` whose condition is only known while running (each
#   branch is counted as if it always ran), a `while` (counted once), or a
#   loop too long to walk in the time budget (scaled from its first items).
# - unknown: a count that can't be worked out before running (an adaptive
#   step's trials, a `repeat` count from a call); the body is counted once.
CERTAINTIES = ("exact", "inferred", "approximate", "unknown")
_RANK = {name: rank for rank, name in enumerate(CERTAINTIES)}


def worse(a: str, b: str) -> str:
    """The less certain of two certainty levels."""
    return a if _RANK[a] >= _RANK[b] else b


@dataclass(frozen=True)
class PlanStepRef:
    step: Step
    path: str
    # Sub-sequence the step comes from (`use:` id), None for the loaded one.
    use_id: str | None


def iter_plan_steps(
    steps: list[Step],
    path: str,
    resolve_use: Callable[[str], SequenceSpec] | None,
    *,
    use_id: str | None = None,
    use_chain: tuple[str, ...] = (),
    include_disabled: bool = True,
) -> Iterator[PlanStepRef]:
    """Every step in document order (disabled ones too, by default).

    A `use:` step is followed by the steps of the sequence it runs, under
    `<use step path>.use.steps[...]`. A sequence used twice shares its step
    objects, so both places get the same (combined) counts. With
    `include_disabled=False`, disabled steps and everything inside them are
    left out.
    """
    for index, step in enumerate(steps):
        if not include_disabled and getattr(step, "disabled", False):
            continue
        step_path = f"{path}[{index}]"
        yield PlanStepRef(step=step, path=step_path, use_id=use_id)
        for body_path, body, _branch in step_bodies(step, step_path):
            yield from iter_plan_steps(
                body,
                body_path,
                resolve_use,
                use_id=use_id,
                use_chain=use_chain,
                include_disabled=include_disabled,
            )
        if isinstance(step, UseStep) and resolve_use is not None:
            name = str(step.sequence_id).strip()
            if not name or name in use_chain:
                continue
            try:
                spec = resolve_use(name)
            except Exception:
                continue
            yield from iter_plan_steps(
                spec.steps,
                f"{step_path}.use.steps",
                resolve_use,
                use_id=name,
                use_chain=(*use_chain, name),
                include_disabled=include_disabled,
            )


@dataclass(frozen=True)
class CallSource:
    """A call whose result is stored under a name, and what it was passed."""

    label: str
    params: Any


def _call_label(target: Any, action: Any) -> str:
    return f"{target}.{action}" if target else str(action)


def call_list_sources(
    steps: list[Step], resolve_use: Callable[[str], SequenceSpec] | None
) -> dict[str, list[CallSource]]:
    """Names set from a call's result -> the calls that set them.

    Covers `call` steps with `save_as`/`extract`/`assign` and `assign` steps
    whose value is a `call:` mapping.
    """
    out: dict[str, list[CallSource]] = {}
    for ref in iter_plan_steps(steps, "steps", resolve_use, include_disabled=False):
        step = ref.step
        if isinstance(step, CallStep):
            names: set[str] = set()
            if step.save_as:
                names.add(str(step.save_as))
            if step.extract:
                names.add(str(step.save_as or "value"))
            if step.assign:
                names.update(str(name) for name in step.assign)
            source = CallSource(
                label=_call_label(step.process or step.device, step.action),
                params=step.params,
            )
            for name in names:
                out.setdefault(name, []).append(source)
        elif isinstance(step, AssignStep):
            for key, value in step.values.items():
                if isinstance(value, dict) and isinstance(value.get("call"), dict):
                    spec = value["call"]
                    source = CallSource(
                        label=_call_label(
                            spec.get("process") or spec.get("device"), spec.get("action")
                        ),
                        params=spec.get("params") or {},
                    )
                    out.setdefault(str(key), []).append(source)
    return out


def single_list_param(
    params: Any, render: Callable[[Any], Any]
) -> tuple[str, int] | None:
    """The one list-valued param of a call, as (name, length).

    Each param is rendered on its own, so one that reads a name only known
    while running (`center_hz: ${freq_center_hz}`) doesn't hide the list.
    None when the call gets no list or more than one: then nothing says
    which list its result follows.
    """
    if not isinstance(params, dict):
        return None
    lists: list[tuple[str, int]] = []
    for name, raw in params.items():
        try:
            value = render(raw)
        except Exception:
            continue
        if isinstance(value, (list, tuple)):
            lists.append((str(name), len(value)))
    return lists[0] if len(lists) == 1 else None
