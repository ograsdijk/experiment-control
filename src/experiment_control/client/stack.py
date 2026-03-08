from __future__ import annotations

from pathlib import Path
from typing import Any

from ..utils.config_parsing import optional_dict, require_dict
from ..utils.manager_network import resolve_manager_network
from ..utils.yaml_helpers import load_yaml_file
from .apis import (
    DeviceAPI,
    DeviceHandle,
    HdfAPI,
    ManagerAPI,
    ProcessAPI,
    ProcessHandle,
    SequencerAPI,
    WaitAPI,
)
from .errors import result_from_response
from .events import EventSubscriber
from .transport import RpcTransport
from .types import Json


class StackClient:
    def __init__(
        self,
        *,
        router_rpc: str,
        manager_pub: str | None = None,
        rpc_timeout_ms: int = 2000,
        rpc_retries: int = 0,
        source_kind: str = "script",
        source_id: str | None = None,
        auto_open: bool = True,
    ) -> None:
        self.router_rpc = str(router_rpc).strip()
        self.manager_pub = (
            str(manager_pub).strip() if manager_pub is not None else None
        ) or None
        self.transport = RpcTransport(
            router_rpc=self.router_rpc,
            timeout_ms=int(rpc_timeout_ms),
            retries=int(rpc_retries),
            source_kind=source_kind,
            source_id=source_id,
        )

        self.devices = DeviceAPI(self)
        self.processes = ProcessAPI(self)
        self.sequencer = SequencerAPI(self, process_id="sequencer")
        self.hdf = HdfAPI(self, process_id="hdf_writer")
        self.manager = ManagerAPI(self)
        self.wait = WaitAPI(self)

        if auto_open:
            self.open()

    @classmethod
    def from_endpoints(
        cls,
        *,
        router_rpc: str,
        manager_pub: str | None = None,
        rpc_timeout_ms: int = 2000,
        rpc_retries: int = 0,
        source_kind: str = "script",
        source_id: str | None = None,
        auto_open: bool = True,
    ) -> "StackClient":
        return cls(
            router_rpc=router_rpc,
            manager_pub=manager_pub,
            rpc_timeout_ms=rpc_timeout_ms,
            rpc_retries=rpc_retries,
            source_kind=source_kind,
            source_id=source_id,
            auto_open=auto_open,
        )

    @classmethod
    def from_stack_yaml(
        cls,
        stack_yaml: str | Path,
        *,
        rpc_timeout_ms: int = 2000,
        rpc_retries: int = 0,
        source_kind: str = "script",
        source_id: str | None = None,
        auto_open: bool = True,
    ) -> "StackClient":
        raw = load_yaml_file(stack_yaml)
        raw_obj = require_dict(raw, path=[])
        manager_raw = optional_dict(raw_obj.get("manager"), path=["manager"])
        network = resolve_manager_network(manager_raw)
        return cls(
            router_rpc=network.local_rpc_connect,
            manager_pub=network.local_pub_connect,
            rpc_timeout_ms=rpc_timeout_ms,
            rpc_retries=rpc_retries,
            source_kind=source_kind,
            source_id=source_id,
            auto_open=auto_open,
        )

    def open(self) -> None:
        self.transport.open()

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "StackClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def rpc(
        self,
        payload: Json,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
        expect_ok: bool = False,
    ) -> Any:
        response = self.transport.request(payload, timeout_ms=timeout_ms, retries=retries)
        if expect_ok:
            return result_from_response(response, request=payload)
        return response

    def device(self, device_id: str) -> DeviceHandle:
        return DeviceHandle(self.devices, str(device_id))

    def process(self, process_id: str) -> ProcessHandle:
        return ProcessHandle(self.processes, str(process_id))

    def subscribe(
        self,
        topics: list[str] | tuple[str, ...],
        *,
        rcvtimeo_ms: int = 200,
    ) -> EventSubscriber:
        if self.manager_pub is None:
            raise ValueError(
                "manager_pub is not configured; build client with manager_pub to subscribe"
            )
        return EventSubscriber(
            manager_pub=self.manager_pub,
            topics=topics,
            rcvtimeo_ms=rcvtimeo_ms,
        )

