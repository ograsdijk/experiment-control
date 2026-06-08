from __future__ import annotations

import numpy as np

from experiment_control.types import StreamCall, StreamOut, TelemetryCall

from ._dummy_helpers import scalar_telemetry


DEFAULT_TELEMETRY_CALLS_DUMMYRESONANCETRACEDRIVER = [
    TelemetryCall(
        method="read_frequency_setpoint_hz",
        outputs=[scalar_telemetry("frequency_setpoint_hz", "Hz", dtype="float64")],
    ),
    TelemetryCall(
        method="read_frequency_hz",
        outputs=[scalar_telemetry("frequency_hz", "Hz", dtype="float64")],
    ),
]

DEFAULT_STREAM_CALLS_DUMMYRESONANCETRACEDRIVER = [
    StreamCall(
        method="acquire_trace",
        outputs=[
            StreamOut(
                stream="trace",
                dtype="int16",
                shape=(5000,),
                units="counts",
                ring_slots=512,
            )
        ],
    )
]


class DummyResonanceTraceDriver:
    def __init__(
        self,
        port: int,
        *,
        initial_frequency_hz: float = 1_000_000_000.0,
        frequency_noise_sigma_hz: float = 150_000.0,
        n_points: int = 5000,
        time_sigma_points: float = 300.0,
        time_center_index: float | None = None,
        resonance_center_hz: float = 1_000_000_000.0,
        resonance_sigma_hz: float = 6_000_000.0,
        trace_offset_counts: float = 1200.0,
        amplitude_peak_counts: float = 2600.0,
        trace_noise_sigma_counts: float = 30.0,
        n_channels: int = 1,
        channel_offsets_counts: list[float] | tuple[float, ...] | None = None,
        rng_seed: int | None = None,
    ) -> None:
        self.port = int(port)
        self.frequency_setpoint_hz = float(initial_frequency_hz)
        self.frequency_noise_sigma_hz = float(frequency_noise_sigma_hz)
        self.n_points = int(n_points)
        self.time_sigma_points = float(time_sigma_points)
        self.resonance_center_hz = float(resonance_center_hz)
        self.resonance_sigma_hz = float(resonance_sigma_hz)
        self.trace_offset_counts = float(trace_offset_counts)
        self.amplitude_peak_counts = float(amplitude_peak_counts)
        self.trace_noise_sigma_counts = float(trace_noise_sigma_counts)
        self.n_channels = int(n_channels)
        self._rng = np.random.default_rng(rng_seed)

        if self.n_points < 8:
            raise ValueError("n_points must be >= 8")
        if self.n_channels < 1:
            raise ValueError("n_channels must be >= 1")
        if self.time_sigma_points <= 0:
            raise ValueError("time_sigma_points must be > 0")
        if self.resonance_sigma_hz <= 0:
            raise ValueError("resonance_sigma_hz must be > 0")
        if self.frequency_noise_sigma_hz < 0:
            raise ValueError("frequency_noise_sigma_hz must be >= 0")
        if self.trace_noise_sigma_counts < 0:
            raise ValueError("trace_noise_sigma_counts must be >= 0")
        if channel_offsets_counts is None:
            self._channel_offsets_counts = np.arange(
                self.n_channels, dtype=np.float64
            ) * 80.0
        else:
            parsed = np.asarray(channel_offsets_counts, dtype=np.float64).reshape(-1)
            if int(parsed.size) != int(self.n_channels):
                raise ValueError(
                    "channel_offsets_counts length must match n_channels"
                )
            self._channel_offsets_counts = parsed.astype(np.float64, copy=False)

        center = (
            float(time_center_index)
            if time_center_index is not None
            else 0.5 * (self.n_points - 1)
        )
        x = np.arange(self.n_points, dtype=np.float64)
        self._pulse_profile = np.exp(
            -0.5 * ((x - center) / self.time_sigma_points) ** 2
        )

    def connect(self) -> None:
        print(f"Connecting to dummy resonance-trace device on port {self.port}")

    def disconnect(self) -> None:
        print(f"Disconnecting from dummy resonance-trace device on port {self.port}")

    def device_metadata(self) -> dict[str, object]:
        return {
            "device_type": "dummy_resonance_trace",
            "port": int(self.port),
            "n_channels": int(self.n_channels),
            "n_points": int(self.n_points),
        }

    def stream_metadata(self) -> dict[str, dict[str, object]]:
        return {
            "trace": {
                "n_channels": int(self.n_channels),
                "n_points": int(self.n_points),
            }
        }

    def set_frequency_hz(self, frequency_hz: float) -> None:
        self.frequency_setpoint_hz = float(frequency_hz)

    def read_frequency_setpoint_hz(self) -> float:
        return float(self.frequency_setpoint_hz)

    def read_frequency_hz(self) -> float:
        if self.frequency_noise_sigma_hz == 0:
            return float(self.frequency_setpoint_hz)
        return float(
            self.frequency_setpoint_hz
            + self._rng.normal(0.0, self.frequency_noise_sigma_hz)
        )

    def _resonance_factor(self) -> float:
        detuning_hz = self.frequency_setpoint_hz - self.resonance_center_hz
        return float(np.exp(-0.5 * (detuning_hz / self.resonance_sigma_hz) ** 2))

    def acquire_trace(self, n_batch: int | None = None) -> np.ndarray:
        _ = n_batch

        resonance = self._resonance_factor()
        amplitude = self.amplitude_peak_counts * resonance
        signal = self.trace_offset_counts + amplitude * self._pulse_profile

        if self.n_channels <= 1:
            if self.trace_noise_sigma_counts > 0:
                noise = self._rng.normal(
                    0.0, self.trace_noise_sigma_counts, size=self.n_points
                )
            else:
                noise = 0.0
            trace = np.asarray(signal + noise, dtype=np.float64)
            trace = np.clip(
                np.rint(trace),
                np.iinfo(np.int16).min,
                np.iinfo(np.int16).max,
            )
            return trace.astype(np.int16, copy=False)

        traces = np.repeat(
            np.asarray(signal, dtype=np.float64)[np.newaxis, :],
            int(self.n_channels),
            axis=0,
        )
        traces += self._channel_offsets_counts[:, np.newaxis]
        if self.trace_noise_sigma_counts > 0:
            traces += self._rng.normal(
                0.0,
                self.trace_noise_sigma_counts,
                size=(int(self.n_channels), int(self.n_points)),
            )
        traces = np.clip(
            np.rint(traces),
            np.iinfo(np.int16).min,
            np.iinfo(np.int16).max,
        )
        return traces.astype(np.int16, copy=False)

