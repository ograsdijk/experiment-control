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

    def set_frequency_channel_0(self, freq_hz: float) -> None:
        self[0].frequency = freq_hz

    def set_frequency_channel_1(self, freq_hz: float) -> None:
        self[1].frequency = freq_hz

    def get_frequency_channel_0(self) -> float:
        return self[0].frequency

    def get_frequency_channel_1(self) -> float:
        return self[1].frequency

    def set_power_channel_0(self, power_dbm: float) -> None:
        self[0].power = power_dbm

    def set_power_channel_1(self, power_dbm: float) -> None:
        self[1].power = power_dbm

    def get_power_channel_0(self) -> float:
        return self[0].power

    def get_power_channel_1(self) -> float:
        return self[1].power

    def set_enable_channel_0(self, enable: bool) -> None:
        self[0].enable = enable

    def set_enable_channel_1(self, enable: bool) -> None:
        self[1].enable = enable

    def get_enable_channel_0(self) -> bool:
        return self[0].enable

    def get_enable_channel_1(self) -> bool:
        return self[1].enable

    def set_phase_channel_0(self, phase_deg: float) -> None:
        self[0].phase = phase_deg

    def set_phase_channel_1(self, phase_deg: float) -> None:
        self[1].phase = phase_deg

    def get_phase_channel_0(self) -> float:
        return self[0].phase

    def get_phase_channel_1(self) -> float:
        return self[1].phase

    def set_temp_compensation_mode_channel_0(self, mode: str) -> None:
        self[0].temp_compensation_mode = mode

    def set_temp_compensation_mode_channel_1(self, mode: str) -> None:
        self[1].temp_compensation_mode = mode

    def get_temp_compensation_mode_channel_0(self) -> str:
        return self[0].temp_compensation_mode

    def get_temp_compensation_mode_channel_1(self) -> str:
        return self[1].temp_compensation_mode
