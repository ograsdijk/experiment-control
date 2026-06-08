import warnings

from windfreak import SynthHD as _SynthHD


class SynthHD(_SynthHD):
    def __init__(self, port: str) -> None:
        self.port = port

    def connect(self) -> None:
        super().__init__(self.port)

    def disconnect(self) -> None:
        self.close()

    def set_frequency(self, channel: int, freq_hz: float) -> None:
        self[int(channel)].frequency = float(freq_hz)

    def get_frequency(self, channel: int) -> float:
        return self[int(channel)].frequency

    def set_power(self, channel: int, dbm: float) -> None:
        self[int(channel)].power = float(dbm)

    def get_power(self, channel: int) -> float:
        return self[int(channel)].power

    def set_enable(self, channel: int, on: bool) -> None:
        self[int(channel)].enable = bool(on)

    def get_enable(self, channel: int) -> bool:
        return self[int(channel)].enable

    def set_phase(self, channel: int, deg: float) -> None:
        self[int(channel)].phase = float(deg)

    def get_phase(self, channel: int) -> float:
        return self[int(channel)].phase

    # --- Deprecated per-channel aliases (REFACTOR_PLAN §6) -----------
    # Downstream instance YAML in `centrex-experimental-stack` still
    # references `get_frequency_channel_0`, `set_power_channel_1`,
    # etc. (laser-lock-1 SG[123].yaml telemetry_calls and
    # frequency_step_guard/laser_lock_freq_nltl_power interceptors).
    # Keep one release worth of shims so upstream can ship without
    # breaking telemetry polling and command interceptors during the
    # downstream-YAML migration. Remove per REFACTOR_PLAN §10.12.

    def _warn_per_channel_alias(self, old: str, new: str) -> None:
        warnings.warn(
            f"SynthHD.{old} is deprecated; use {new} instead.",
            DeprecationWarning,
            stacklevel=3,
        )

    def set_frequency_channel_0(self, freq_hz: float) -> None:
        self._warn_per_channel_alias("set_frequency_channel_0", "set_frequency(0, freq_hz)")
        self.set_frequency(0, freq_hz)

    def set_frequency_channel_1(self, freq_hz: float) -> None:
        self._warn_per_channel_alias("set_frequency_channel_1", "set_frequency(1, freq_hz)")
        self.set_frequency(1, freq_hz)

    def get_frequency_channel_0(self) -> float:
        self._warn_per_channel_alias("get_frequency_channel_0", "get_frequency(0)")
        return self.get_frequency(0)

    def get_frequency_channel_1(self) -> float:
        self._warn_per_channel_alias("get_frequency_channel_1", "get_frequency(1)")
        return self.get_frequency(1)

    def set_power_channel_0(self, dbm: float) -> None:
        self._warn_per_channel_alias("set_power_channel_0", "set_power(0, dbm)")
        self.set_power(0, dbm)

    def set_power_channel_1(self, dbm: float) -> None:
        self._warn_per_channel_alias("set_power_channel_1", "set_power(1, dbm)")
        self.set_power(1, dbm)

    def get_power_channel_0(self) -> float:
        self._warn_per_channel_alias("get_power_channel_0", "get_power(0)")
        return self.get_power(0)

    def get_power_channel_1(self) -> float:
        self._warn_per_channel_alias("get_power_channel_1", "get_power(1)")
        return self.get_power(1)

    def set_enable_channel_0(self, on: bool) -> None:
        self._warn_per_channel_alias("set_enable_channel_0", "set_enable(0, on)")
        self.set_enable(0, on)

    def set_enable_channel_1(self, on: bool) -> None:
        self._warn_per_channel_alias("set_enable_channel_1", "set_enable(1, on)")
        self.set_enable(1, on)

    def get_enable_channel_0(self) -> bool:
        self._warn_per_channel_alias("get_enable_channel_0", "get_enable(0)")
        return self.get_enable(0)

    def get_enable_channel_1(self) -> bool:
        self._warn_per_channel_alias("get_enable_channel_1", "get_enable(1)")
        return self.get_enable(1)

    def set_phase_channel_0(self, deg: float) -> None:
        self._warn_per_channel_alias("set_phase_channel_0", "set_phase(0, deg)")
        self.set_phase(0, deg)

    def set_phase_channel_1(self, deg: float) -> None:
        self._warn_per_channel_alias("set_phase_channel_1", "set_phase(1, deg)")
        self.set_phase(1, deg)

    def get_phase_channel_0(self) -> float:
        self._warn_per_channel_alias("get_phase_channel_0", "get_phase(0)")
        return self.get_phase(0)

    def get_phase_channel_1(self) -> float:
        self._warn_per_channel_alias("get_phase_channel_1", "get_phase(1)")
        return self.get_phase(1)
