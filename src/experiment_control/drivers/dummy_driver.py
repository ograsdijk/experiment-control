from __future__ import annotations

import numpy as np

from experiment_control.types import TelemetryCall

from ._dummy_helpers import scalar_telemetry


DEFAULT_TELEMETRY_CALLS_DUMMYDRIVER = [
    TelemetryCall(
        method="read_temperature",
        outputs=[scalar_telemetry("temperature", "C")],
    ),
    TelemetryCall(
        method="read_voltage",
        outputs=[scalar_telemetry("voltage", "V")],
    ),
]


class DummyDriver:
    def __init__(self, port: int) -> None:
        self.port = port
        self.temperature = 20.0

    def connect(self) -> None:
        print(f"Connecting to dummy device on port {self.port}")

    def disconnect(self) -> None:
        print(f"Disconnecting from dummy device on port {self.port}")

    def read_temperature(self) -> float:
        return self.temperature + np.random.uniform(-1, 1)

    def read_voltage(self) -> float:
        return np.random.uniform(3.0, 3.3)

    def set_temperature(self, temperature: float) -> None:
        print(f"Setting temperature to {temperature} deg C")
        self.temperature = temperature

