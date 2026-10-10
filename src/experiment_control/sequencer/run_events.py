"""Per-run event log: what happened during a sequencer run, and at which step.

Pauses (with who paused and why), resumes, stops, step and cleanup failures,
external faults, and warnings other processes logged while the run was going.
Each event carries the step it happened at, so the UI can show it on that
step like a validation diagnostic. The log is cleared when a run starts and
kept after it ends.
"""

from __future__ import annotations

from typing import Any

_STEP_FIELDS = ("kind", "summary", "path", "line", "branch")


def _step_ref(step: Any) -> dict[str, Any] | None:
    if not isinstance(step, dict):
        return None
    return {key: step.get(key) for key in _STEP_FIELDS}


class RunEventLog:
    """Bounded event list; consecutive repeats are counted, not duplicated."""

    def __init__(self, max_events: int = 200) -> None:
        self._max_events = max(1, int(max_events))
        self._events: list[dict[str, Any]] = []
        self._dropped = 0
        # Bumped on every change (and on reset), so pollers can tell when to refetch.
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    def reset(self) -> None:
        self._events = []
        self._dropped = 0
        self._seq += 1

    def record(
        self,
        *,
        severity: str,
        kind: str,
        message: str,
        elapsed_s: float,
        step: Any = None,
        source: str | None = None,
        trigger: Any = None,
    ) -> None:
        step_ref = _step_ref(step)
        key = (severity, kind, message, (step_ref or {}).get("path"), source)
        self._seq += 1
        if self._events and self._events[-1]["_key"] == key:
            last = self._events[-1]
            last["count"] += 1
            last["last_elapsed_s"] = float(elapsed_s)
            return
        event: dict[str, Any] = {
            "_key": key,
            "severity": str(severity),
            "kind": str(kind),
            "message": str(message),
            "source": source,
            "step": step_ref,
            "elapsed_s": float(elapsed_s),
            "last_elapsed_s": float(elapsed_s),
            "count": 1,
        }
        if trigger is not None:
            event["trigger"] = trigger
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events.pop(0)
            self._dropped += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "seq": self._seq,
            "dropped": self._dropped,
            "events": [
                {key: value for key, value in event.items() if key != "_key"}
                for event in self._events
            ],
        }

    def summary(self, latest: int = 3) -> dict[str, Any]:
        """Small form for sequencer.status (polled often)."""
        events = self.snapshot()["events"]
        return {
            "seq": self._seq,
            "count": len(events),
            "dropped": self._dropped,
            "errors": sum(1 for event in events if event["severity"] in {"error", "critical"}),
            "warnings": sum(1 for event in events if event["severity"] == "warning"),
            "latest": events[-latest:] if latest > 0 else [],
        }
