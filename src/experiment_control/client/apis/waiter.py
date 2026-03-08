from __future__ import annotations

import time
from typing import Any

from ._base import ClientFacadeBase
from ..errors import ProcessRpcNotReadyError, RpcResponseError
from ..types import Json


class WaitAPI(ClientFacadeBase):
    def manager_ready(
        self,
        *,
        timeout_s: float = 10.0,
        poll_s: float = 0.2,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        sleep_s = max(0.01, float(poll_s))
        while time.monotonic() < deadline:
            try:
                _ = self._client.manager.identity()
                return True
            except Exception:
                time.sleep(sleep_s)
        return False

    def process_rpc_ready(
        self,
        process_id: str,
        *,
        probe_action: str = "process.capabilities",
        probe_params: Json | None = None,
        timeout_s: float = 10.0,
        poll_s: float = 0.1,
        timeout_ms: int | None = None,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        sleep_s = max(0.01, float(poll_s))
        params = dict(probe_params or {})
        while time.monotonic() < deadline:
            try:
                _ = self._client.processes.call(
                    str(process_id),
                    str(probe_action),
                    params,
                    timeout_ms=timeout_ms,
                )
                return True
            except ProcessRpcNotReadyError:
                time.sleep(sleep_s)
                continue
            except RpcResponseError as exc:
                if exc.code in {"process_not_running", "process_starting"}:
                    time.sleep(sleep_s)
                    continue
                raise
            except Exception:
                time.sleep(sleep_s)
        return False

    def process_state(
        self,
        process_id: str,
        *,
        expected: str | list[str] | tuple[str, ...],
        timeout_s: float = 10.0,
        poll_s: float = 0.2,
    ) -> bool:
        expected_set: set[str]
        if isinstance(expected, str):
            expected_set = {expected.upper()}
        else:
            expected_set = {str(item).upper() for item in expected}
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        sleep_s = max(0.01, float(poll_s))
        while time.monotonic() < deadline:
            status = self._client.processes.get_status(str(process_id))
            if isinstance(status, dict):
                state = str(status.get("state", "")).upper()
                if state in expected_set:
                    return True
            time.sleep(sleep_s)
        return False

    def sequencer_stopped(
        self,
        *,
        timeout_s: float = 30.0,
        poll_s: float = 0.2,
    ) -> bool:
        return self.process_state(
            "sequencer",
            expected=("STOPPED", "ERROR"),
            timeout_s=timeout_s,
            poll_s=poll_s,
        )

    def hdf_open(
        self,
        *,
        timeout_s: float = 10.0,
        poll_s: float = 0.2,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        sleep_s = max(0.01, float(poll_s))
        while time.monotonic() < deadline:
            try:
                status: Any = self._client.hdf.status()
            except Exception:
                time.sleep(sleep_s)
                continue
            if isinstance(status, dict) and status.get("file"):
                return True
            time.sleep(sleep_s)
        return False

