import math
from typing import Literal

from windfreak import SynthHD as _SynthHD


class SynthHD(_SynthHD):
    """Experiment-control wrapper for a two-channel Windfreak SynthHD.

    The public RPC API and telemetry use physical CH1/CH2 numbering. The
    upstream Windfreak API is zero-indexed internally, so this wrapper is the
    single conversion boundary: physical CH1/A maps to index 0 and CH2/B maps
    to index 1.

    This wrapper does not restrict its RPC surface. Inherited upstream members
    stay reachable, deliberately: hiding them was never a safety boundary,
    because ``apply_command_interceptor_chain`` passes any action with no
    matching route straight through, and anyone able to send device commands
    can also stop the interceptor process. Safety belongs in interceptor
    routes and manager policy, not in a per-driver name list -- which in
    practice only silently deleted capabilities (see #159).
    """

    _VALID_CHANNELS = (1, 2)

    def __init__(self, port: str) -> None:
        self.port = port

    def connect(self) -> None:
        super().__init__(self.port)

    def disconnect(self) -> None:
        self.close()

    def _channel_index(self, channel: int) -> int:
        # Keep the public driver boundary intentionally strict. Command
        # interceptors match the raw JSON ``channel`` value against physical
        # integer CH1/CH2 before the driver is called; accepting strings, bools,
        # or floats here would let a value bypass a channel-specific safety rule
        # and then be coerced onto real hardware.
        if isinstance(channel, bool) or not isinstance(channel, int):
            raise ValueError(
                f"SynthHD channel must be integer 1 (CH1/A) or 2 (CH2/B), got {channel!r}"
            )
        if channel not in self._VALID_CHANNELS:
            raise ValueError(
                f"SynthHD channel must be integer 1 (CH1/A) or 2 (CH2/B), got {channel!r}"
            )
        return channel - 1

    @staticmethod
    def _finite_float(value: float, name: str) -> float:
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError(f"SynthHD {name} must be finite, got {value!r}")
        return converted

    def set_frequency(self, channel: Literal[1, 2], freq_hz: float) -> None:
        self[self._channel_index(channel)].frequency = self._finite_float(
            freq_hz, "frequency"
        )

    def get_frequency(self, channel: Literal[1, 2]) -> float:
        return self[self._channel_index(channel)].frequency

    def set_power(self, channel: Literal[1, 2], dbm: float) -> None:
        self[self._channel_index(channel)].power = self._finite_float(dbm, "power")

    def get_power(self, channel: Literal[1, 2]) -> float:
        return self[self._channel_index(channel)].power

    def set_enable(self, channel: Literal[1, 2], on: bool) -> None:
        self[self._channel_index(channel)].enable = bool(on)

    def get_enable(self, channel: Literal[1, 2]) -> bool:
        return self[self._channel_index(channel)].enable

    def set_phase(self, channel: Literal[1, 2], deg: float) -> None:
        self[self._channel_index(channel)].phase = self._finite_float(deg, "phase")

    def get_phase(self, channel: Literal[1, 2]) -> float:
        return self[self._channel_index(channel)].phase

    def set_temp_compensation_mode(self, channel: Literal[1, 2], mode: str) -> None:
        self[self._channel_index(channel)].temp_compensation_mode = str(mode)

    def get_temp_compensation_mode(self, channel: Literal[1, 2]) -> str:
        return self[self._channel_index(channel)].temp_compensation_mode

    def get_lock_status(self, channel: Literal[1, 2]) -> bool:
        return self[self._channel_index(channel)].lock_status

    # --- Frequency reference (device-wide) ---
    def get_reference_mode(self) -> str:
        return self.reference_mode

    def set_reference_mode(self, mode: str) -> None:
        self.reference_mode = mode

    def get_reference_frequency(self) -> float:
        """Reference frequency in Hz."""
        return self.reference_frequency

    def set_reference_frequency(self, freq_hz: float) -> None:
        """Reference frequency in Hz."""
        self.reference_frequency = self._finite_float(freq_hz, "reference frequency")
