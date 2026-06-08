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
