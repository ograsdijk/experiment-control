from __future__ import annotations

import numpy as np

from experiment_control.types import TelemetryCall

from ._dummy_helpers import scalar_telemetry


DEFAULT_TELEMETRY_CALLS_DUMMYFREQUENCYNOISEDRIVER = [
    TelemetryCall(
        method="read_frequency_setpoint_hz",
        outputs=[scalar_telemetry("frequency_setpoint_hz", "Hz", dtype="float64")],
    ),
    TelemetryCall(
        method="read_frequency_hz",
        outputs=[scalar_telemetry("frequency_hz", "Hz", dtype="float64")],
    ),
]


class DummyFrequencyNoiseDriver:
    def __init__(
        self,
        port: int,
        *,
        initial_frequency_hz: float = 1_000_000_000.0,
        noise_sigma_hz: float = 150_000.0,
        rng_seed: int | None = None,
    ) -> None:
        self.port = int(port)
        self.frequency_setpoint_hz = float(initial_frequency_hz)
        self.noise_sigma_hz = float(noise_sigma_hz)
        self._rng = np.random.default_rng(rng_seed)

        if self.noise_sigma_hz < 0:
            raise ValueError("noise_sigma_hz must be >= 0")

    def connect(self) -> None:
        print(f"Connecting to dummy frequency-noise device on port {self.port}")

    def disconnect(self) -> None:
        print(f"Disconnecting from dummy frequency-noise device on port {self.port}")

    def set_frequency_hz(self, frequency_hz: float) -> None:
        self.frequency_setpoint_hz = float(frequency_hz)

    def read_frequency_setpoint_hz(self) -> float:
        return float(self.frequency_setpoint_hz)

    def read_frequency_hz(self) -> float:
        if self.noise_sigma_hz == 0:
            return float(self.frequency_setpoint_hz)
        return float(
            self.frequency_setpoint_hz + self._rng.normal(0.0, self.noise_sigma_hz)
        )
