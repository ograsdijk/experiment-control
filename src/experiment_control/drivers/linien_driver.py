import math
import pickle
import time

import numpy as np
from linien_client.connection import LinienClient
from linien_client.device import Device
from linien_client.remote_parameters import RemoteParameters
from linien_common.common import N_POINTS

ADC_TO_VOLT = 1.0 / 8192


class LinienDriver:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        autostart_server: bool,
        max_age_s: float = 0.25,
    ) -> None:
        dev = Device(host=host, username=username, password=password)
        self._client: LinienClient = LinienClient(dev)
        self._autostart: bool = autostart_server
        self._remote_params: RemoteParameters | None = None
        self._last_refresh: float = 0.0
        self._max_age_s: float = max_age_s

    def connect(self) -> None:
        self._client.connect(
            autostart_server=self._autostart,
            use_parameter_cache=True,
        )
        self._remote_params = self._client.parameters

    def disconnect(self) -> None:
        self._client.disconnect()

    def _maybe_refresh(self, max_age_s: float | None = None) -> None:
        max_age = self._max_age_s if max_age_s is None else max_age_s
        now = time.monotonic()
        if now - self._last_refresh > max_age:
            if self._remote_params is None:
                raise RuntimeError("Not connected")
            self._remote_params.check_for_changed_parameters()
            self._last_refresh = now

    # ---- lock enable/disable ----
    def get_lock(self, max_age_s: float | None = None) -> bool:
        self._maybe_refresh(max_age_s)
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        return bool(self._remote_params.lock.value)

    def set_lock(self, enabled: bool) -> None:
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        self._remote_params.lock.value = bool(enabled)

    # ---- modulation ----
    def get_modulation(self, max_age_s: float | None = None) -> tuple[int, int]:
        self._maybe_refresh(max_age_s)
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        return (
            int(self._remote_params.modulation_amplitude.value),
            int(self._remote_params.modulation_frequency.value),
        )

    def set_modulation_amplitude(self, amp_internal: int) -> None:
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        self._remote_params.modulation_amplitude.value = int(amp_internal)

    def set_modulation_frequency(self, freq_internal: int) -> None:
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        self._remote_params.modulation_frequency.value = int(freq_internal)

    # ---- demod phase (single channel) ----
    def get_demod_phase(self, max_age_s: float | None = None) -> int:
        self._maybe_refresh(max_age_s)
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        return int(self._remote_params.demodulation_phase_a.value)

    def set_demod_phase(self, degrees: int) -> None:
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        self._remote_params.demodulation_phase_a.value = int(degrees)

    # ---- PID ----
    def get_pid(self, max_age_s: float | None = None) -> tuple[int, int, int]:
        self._maybe_refresh(max_age_s)
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        return (
            int(self._remote_params.p.value),
            int(self._remote_params.i.value),
            int(self._remote_params.d.value),
        )

    def set_pid(
        self, p: int | None = None, i: int | None = None, d: int | None = None
    ) -> None:
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        self._maybe_refresh()
        if p is not None:
            self._remote_params.p.value = int(p)
        if i is not None:
            self._remote_params.i.value = int(i)
        if d is not None:
            self._remote_params.d.value = int(d)

    def signal_stats(self) -> dict[str, float]:
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        signal_stats = self._remote_params.signal_stats.value

        if "error_signal_mean" not in signal_stats:
            signals = [
                "error_signal_1_mean",
                "error_signal_1_std",
                "monitor_signal_mean",
                "monitor_signal_std",
                "control_signal_mean",
                "control_signal_std",
            ]
            return {
                k.replace("_1", ""): float(signal_stats.get(k, math.nan) * ADC_TO_VOLT)
                for k in signals
            }

        else:
            signals = [
                "error_signal_mean",
                "error_signal_std",
                "monitor_signal_mean",
                "monitor_signal_std",
                "control_signal_mean",
                "control_signal_std",
            ]
            return {k: float(signal_stats[k] * ADC_TO_VOLT) for k in signals}

    def traces(self) -> np.ndarray:
        if self._remote_params is None:
            raise RuntimeError("Not connected")
        trace_data = pickle.loads(self._remote_params.to_plot.value)

        traces = np.zeros((3, N_POINTS), dtype=np.float64)

        if "error_signal" not in trace_data:
            signals = ["error_signal_1", "monitor_signal", "control_signal"]
            for ids, signal in enumerate(signals):
                trace = trace_data.get(signal, None)
                if trace is None:
                    continue
                traces[ids, :] = np.asarray(trace, dtype=np.float64) * ADC_TO_VOLT

        else:
            signals = ["error_signal", "monitor_signal", "control_signal"]
            for ids, signal in enumerate(signals):
                trace = trace_data.get(signal, None)
                if trace is None:
                    continue
                traces[ids, :] = np.asarray(trace, dtype=np.float64) * ADC_TO_VOLT

        return traces
