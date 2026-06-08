from windfreak import SynthHD as _SynthHD


class SynthHD(_SynthHD):
    def __init__(self, port: str) -> None:
        self.port = port

    def connect(self) -> None:
        super().__init__(self.port)

    def disconnect(self) -> None:
        self.close()

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
