from nkt_basik import Basik


class NKTBasik(Basik):
    def __init__(self, port: str, devID) -> None:
        self.port = port
        self.devID = devID

    def connect(self) -> None:
        super().__init__(self.port, self.devID)

    def disconnect(self) -> None:
        raise NotImplementedError("Disconnect method not implemented yet.")

    def read_temperature(self) -> float:
        return self.temperature

    def read_power(self) -> float:
        return self.power

    def read_emission(self) -> bool:
        return self.emission

    def read_frequency(self) -> float:
        return self.frequency

    def read_frequency_setpoint(self) -> float:
        return self.frequency_setpoint