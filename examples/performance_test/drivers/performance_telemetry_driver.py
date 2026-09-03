from __future__ import annotations

import math
import time
from typing import Any


class PerformanceTelemetryDriver:
    """Cheap deterministic telemetry source for browser performance profiling."""

    def __init__(self, device_index: int = 0, signal_count: int = 20) -> None:
        self.device_index = int(device_index)
        self.signal_count = max(1, min(24, int(signal_count)))
        self._started_s = time.monotonic()
        self._reads = 0

    def connect(self) -> None:
        return

    def disconnect(self) -> None:
        return

    def device_metadata(self) -> dict[str, object]:
        return {
            "device_type": "performance_telemetry",
            "device_index": self.device_index,
            "signal_count": self.signal_count,
        }

    def read_all(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self._started_s
        phase = self.device_index * 0.173
        self._reads += 1
        values: dict[str, Any] = {}
        float_count = max(0, self.signal_count - 4)
        for index in range(float_count):
            omega = 0.11 + (index % 5) * 0.07
            baseline = self.device_index * 2.0 + index * 0.25
            if index % 4 == 2:
                value = baseline + ((elapsed * (0.4 + index * 0.01)) % 10.0)
            else:
                value = baseline + math.sin(elapsed * omega + phase + index * 0.31)
            values[f"float_{index:02d}"] = value
        if self.signal_count >= 3:
            values["counter"] = self._reads
        if self.signal_count >= 2:
            values["device_index"] = self.device_index
        if self.signal_count >= 1:
            values["toggle_slow"] = int(elapsed) % 2 == 0
        if self.signal_count >= 4:
            values["toggle_fast"] = int(elapsed * 4.0) % 2 == 0
        return values
