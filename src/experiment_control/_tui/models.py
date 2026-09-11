from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    owner_peer_id: str | None = None


ResourceKind = Literal["device", "process"]


@dataclass(frozen=True)
class ResourceView:
    """Normalized row model for the unified operator navigator."""

    kind: ResourceKind
    resource_id: str
    health: str
    state: str
    age_s: float | None
    error: str | None
    is_remote: bool = False
    owner_peer_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.resource_id}"

    @property
    def searchable_text(self) -> str:
        return " ".join(
            part
            for part in (
                self.kind,
                self.resource_id,
                self.health,
                self.state,
                self.owner_peer_id or "",
                self.error or "",
            )
            if part
        ).lower()
