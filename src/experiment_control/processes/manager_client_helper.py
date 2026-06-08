from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import zmq

from ..client.protocol import ManagerProtocol
from ..manager_client import ManagerClient


@dataclass(frozen=True)
class ManagerClientHelper:
    manager_rpc: str
    manager_pub: str
    rpc_timeout_ms: int

    def init_client(
        self,
        *,
        ctx: zmq.Context,
        process_id: str | None,
        subscribe_telemetry: bool,
    ) -> ManagerClient:
        return ManagerClient(
            ctx=ctx,
            manager_rpc=self.manager_rpc,
            manager_pub=self.manager_pub,
            rpc_timeout_ms=int(self.rpc_timeout_ms),
            process_id=process_id,
            subscribe_telemetry=subscribe_telemetry,
        )

    def open_sub(
        self,
        *,
        ctx: zmq.Context,
        topics: Iterable[str],
        rcvtimeo_ms: int = 200,
    ) -> zmq.Socket:
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.setsockopt(zmq.RCVTIMEO, int(rcvtimeo_ms))
        for topic in topics:
            sub.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
        sub.connect(self.manager_pub)
        return sub

    def publish_event(
        self,
        manager: ManagerProtocol,
        *,
        topic: str,
        payload: dict[str, Any],
        include_process_id: bool = True,
        include_ts: bool = True,
        severity: str | None = None,
        device_id: str | None = None,
    ) -> None:
        manager.publish_event(
            topic=topic,
            payload=payload,
            include_process_id=include_process_id,
            include_ts=include_ts,
            severity=severity,
            device_id=device_id,
        )
