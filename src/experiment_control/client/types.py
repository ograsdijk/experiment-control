from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

Json = dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventMessage:
    topic: str
    payload: Any
    received_wall_s: float
    received_mono_s: float

    @classmethod
    def create(cls, *, topic: str, payload: Any) -> "EventMessage":
        return cls(
            topic=str(topic),
            payload=payload,
            received_wall_s=time.time(),
            received_mono_s=time.monotonic(),
        )

