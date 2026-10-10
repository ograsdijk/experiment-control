"""Step-duration statistics behind the sequencer's time-remaining estimate.

The runtime counts how many times each step object still has to execute
(see `SequencerRuntime._walk_remaining`); this module turns those counts into
seconds. Each step's duration is measured from the previous step's finish, so
time spent between steps (tick gaps, atomic-boundary yields) is attributed to
the step that follows it and the per-step means sum back to the elapsed run
time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Kinds that only push frames or set variables: until measured, assume they
# take no time instead of borrowing a mean that may be dominated by sleeps.
_INSTANT_KINDS = frozenset(
    {"assign", "for", "repeat", "if", "while", "atomic", "try", "use"}
)
# Kinds whose durations say nothing about other steps' durations.
_UNREPRESENTATIVE_KINDS = frozenset({"sleep", "pause"})


@dataclass
class _RunningMean:
    count: int = 0
    total_s: float = 0.0

    def add(self, duration_s: float) -> None:
        self.count += 1
        self.total_s += duration_s

    @property
    def mean_s(self) -> float:
        return self.total_s / self.count


@dataclass
class _StepEntry:
    # Strong reference so `id(step)` cannot be reused by another object while
    # the entry exists (e.g. after a library reload re-creates specs mid-run).
    step: Any
    mean: _RunningMean = field(default_factory=_RunningMean)


class StepDurationStats:
    """Plain running means per step object, per step kind, and overall."""

    def __init__(self) -> None:
        self._by_step: dict[int, _StepEntry] = {}
        self._by_kind: dict[str, _RunningMean] = {}
        self._representative = _RunningMean()
        self._overall = _RunningMean()
        self._sleep_overhead = _RunningMean()

    def clear(self) -> None:
        self._by_step.clear()
        self._by_kind.clear()
        self._representative = _RunningMean()
        self._overall = _RunningMean()
        self._sleep_overhead = _RunningMean()

    def record_sleep_overhead(self, overhead_s: float) -> None:
        """Measured duration of a sleep minus the seconds it asked for."""
        self._sleep_overhead.add(max(0.0, float(overhead_s)))

    @property
    def sleep_overhead_s(self) -> float:
        return self._sleep_overhead.mean_s if self._sleep_overhead.count else 0.0

    def record(self, step: Any, kind: str, duration_s: float) -> None:
        duration_s = max(0.0, float(duration_s))
        entry = self._by_step.get(id(step))
        if entry is None or entry.step is not step:
            entry = _StepEntry(step)
            self._by_step[id(step)] = entry
        entry.mean.add(duration_s)
        self._by_kind.setdefault(kind, _RunningMean()).add(duration_s)
        if kind not in _UNREPRESENTATIVE_KINDS:
            self._representative.add(duration_s)
        self._overall.add(duration_s)

    def estimate(self, step: Any, kind: str) -> float | None:
        """Expected duration of one execution of `step`.

        Falls back from the step's own mean to the mean of its kind, then to
        zero for container and assign steps, then to the mean of all
        non-sleep steps, then to the overall mean. Returns None only when
        nothing has been measured. (Sleeps with a rendered duration don't
        come here: see `sleep_overhead_s`.)
        """
        entry = self._by_step.get(id(step))
        if entry is not None and entry.step is step:
            return entry.mean.mean_s
        by_kind = self._by_kind.get(kind)
        if by_kind is not None:
            return by_kind.mean_s
        if kind in _INSTANT_KINDS:
            return 0.0
        if self._representative.count:
            return self._representative.mean_s
        if self._overall.count:
            return self._overall.mean_s
        return None
