from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeviceStatus:
    device_id: str
    registered: bool
    liveness: str | None
    hb_age_s: float | None
    telemetry_age_s: float | None
    driver_state: str | None
    device_state: str | None
    device_reachable: bool | None
    last_error: str | None
    driver_proc_state: str | None
    driver_pid: int | None
    driver_restart_count: int
    driver_last_exit_code: int | None
    driver_last_error: str | None
    is_remote: bool = False
