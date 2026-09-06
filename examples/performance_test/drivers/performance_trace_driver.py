from __future__ import annotations

import numpy as np


class PerformanceTraceDriver:
    """Precomputed multi-channel stream source with visibly changing frames."""

    def __init__(
        self,
        device_index: int = 0,
        channels: int = 4,
        points: int = 2048,
        frame_bank_size: int = 16,
        dtype: str = "float32",
    ) -> None:
        self.device_index = int(device_index)
        self.channels = max(1, int(channels))
        self.points = max(16, int(points))
        self.dtype = np.dtype(dtype)
        bank_size = max(2, int(frame_bank_size))
        x = np.linspace(0.0, 2.0 * np.pi, self.points, endpoint=False)
        bank = np.empty((bank_size, self.channels, self.points), dtype=self.dtype)
        for frame_index in range(bank_size):
            phase = frame_index * (2.0 * np.pi / bank_size)
            for channel in range(self.channels):
                carrier = np.sin(x * (channel + 1) + phase)
                envelope = 0.2 * np.cos(x * 0.25 + phase * 0.5)
                bank[frame_index, channel] = (
                    carrier + envelope + self.device_index * 0.1 + channel * 0.25
                )
        self._frames = bank
        self._next_frame = 0

    def connect(self) -> None:
        return

    def disconnect(self) -> None:
        return

    def device_metadata(self) -> dict[str, object]:
        return {
            "device_type": "performance_trace",
            "device_index": self.device_index,
        }

    def stream_metadata(self) -> dict[str, dict[str, object]]:
        return {
            "trace": {
                "n_channels": self.channels,
                "n_points": self.points,
                "channel_descriptions": [
                    f"Performance channel {index}" for index in range(self.channels)
                ],
                "channel_units": ["arb"] * self.channels,
                "x_axis": "sample_index",
                "x_units": "sample",
            }
        }

    def acquire_trace(self) -> np.ndarray:
        frame = self._frames[self._next_frame]
        self._next_frame = (self._next_frame + 1) % len(self._frames)
        return frame
