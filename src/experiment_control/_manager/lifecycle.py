from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    import zmq

    from ..federation.hub import FederationHub
    from .models import DeviceHandle, ProcessHandle
    from ..manager_protocol import ManagerProtocol
    from ..utils.command_journal import CommandJournal

    _MixinBase = ManagerProtocol
else:
    _MixinBase = object


def _safe_call(cb: Callable[[], None]) -> None:
    try:
        cb()
    except Exception:
        pass


def _safe_close_socket(sock: Any) -> None:
    try:
        sock.close(0)
    except Exception:
        pass


class LifecycleMixin(_MixinBase):
    """Mixin providing manager startup / shutdown orchestration.

    Phase 8.2.11: migrated ``startup_sequence`` + ``shutdown_cleanup``
    (and the seven private wait/predicate helpers) from module-level
    helpers to mixin methods. Pure ``_safe_call`` / ``_safe_close_socket``
    stay at module level.
    """

    # Owned-state attributes (concrete types declared on Manager).
    _processes: dict[str, "ProcessHandle"]
    _devices: dict[str, "DeviceHandle"]
    _federation_hub: "FederationHub"
    _lifecycle_executor: ThreadPoolExecutor
    _command_journal: "CommandJournal | None"
    _registry_rep: "zmq.Socket"
    _sub: "zmq.Socket"
    _process_hb_sub: "zmq.Socket"
    _process_data_sub: "zmq.Socket"
    _internal_rpc: "zmq.Socket"
    _external_pub: "zmq.Socket"
    _ctx: "zmq.Context"
    _process_guard: Any  # ProcessGuardian — opaque to mypy

    def _not_running_process_ids(self, running_state: Any) -> list[str]:
        return [
            pid for pid, h in self._processes.items() if h.state != running_state
        ]

    def _missing_registered_devices(self) -> list[str]:
        return [k for k, h in self._devices.items() if h.rpc_endpoint is None]

    def _not_online_device_ids(self, *, driver_state_ok: Any) -> list[str]:
        not_online: list[str] = []
        for device_id, h in self._devices.items():
            if h.last_hb is None:
                not_online.append(device_id)
                continue
            if (not h.last_hb.device_reachable) or (
                h.last_hb.driver_state != driver_state_ok
            ):
                not_online.append(device_id)
        return not_online

    def _all_online(self, *, driver_state_ok: Any) -> bool:
        for h in self._devices.values():
            if h.last_hb is None:
                return False
            if (not h.last_hb.device_reachable) or (
                h.last_hb.driver_state != driver_state_ok
            ):
                return False
        return True

    def _wait_processes_running(
        self,
        *,
        deadline: float,
        poll_ms: int,
        managed_process_running: Any,
    ) -> None:
        while True:
            if time.monotonic() > deadline:
                not_running = self._not_running_process_ids(managed_process_running)
                self._emit_log(
                    severity="error",
                    topic="manager.startup.process_timeout",
                    message="Timed out waiting for processes RUNNING",
                    source_kind="manager",
                    source_id="manager",
                    stream="event",
                    payload={"not_running": not_running},
                )
                raise TimeoutError(
                    f"Timed out waiting for processes RUNNING: {not_running}"
                )
            self._pump_once(poll_ms=poll_ms)
            all_running = all(
                h.state == managed_process_running for h in self._processes.values()
            )
            if all_running:
                return

    def _wait_registered(self, *, deadline: float, poll_ms: int) -> None:
        while not all(
            h.rpc_endpoint is not None for h in self._devices.values()
        ):
            if time.monotonic() > deadline:
                missing = self._missing_registered_devices()
                self._emit_log(
                    severity="error",
                    topic="manager.startup.registration_timeout",
                    message="Timed out waiting for registration",
                    source_kind="manager",
                    source_id="manager",
                    stream="event",
                    payload={"missing": missing},
                )
                raise TimeoutError(
                    f"Timed out waiting for registration: {missing}"
                )
            self._pump_once(poll_ms=poll_ms)

    def _wait_online(
        self, *, deadline: float, poll_ms: int, driver_state_ok: Any
    ) -> None:
        while True:
            if time.monotonic() > deadline:
                not_online = self._not_online_device_ids(
                    driver_state_ok=driver_state_ok
                )
                self._emit_log(
                    severity="error",
                    topic="manager.startup.online_timeout",
                    message="Timed out waiting for devices ONLINE",
                    source_kind="manager",
                    source_id="manager",
                    stream="event",
                    payload={"not_online": not_online},
                )
                raise TimeoutError(
                    f"Timed out waiting for ONLINE devices: {not_online}"
                )
            self._pump_once(poll_ms=poll_ms)
            if self._all_online(driver_state_ok=driver_state_ok):
                return

    def startup_sequence(
        self,
        *,
        start_drivers: bool = True,
        start_processes: bool = True,
        wait_processes_running: bool | None = None,
        connect: bool | None = None,
        wait_for_registered: bool = True,
        wait_for_online: bool = True,
        timeout_s: float = 10.0,
        poll_ms: int = 50,
        # ``managed_process_running`` / ``driver_state_ok`` default to
        # the Manager-side enums so the mixin is callable standalone.
        # The Manager wrapper supplied them explicitly pre-refactor;
        # exposing defaults here lets the mixin method stand on its own.
        managed_process_running: Any = None,
        driver_state_ok: Any = None,
    ) -> None:
        # Late imports avoid a top-level ``from .types`` cycle since
        # ``Manager`` already pulls those constants. If a caller passes
        # custom states, use them; otherwise resolve at first use.
        if managed_process_running is None or driver_state_ok is None:
            from .models import ManagedProcessState as _MPS
            from ..types import DriverState as _DS

            if managed_process_running is None:
                managed_process_running = _MPS.RUNNING
            if driver_state_ok is None:
                driver_state_ok = _DS.OK
        self._startup_sequence_active = True
        try:
            self._ensure_router_running(timeout_s=timeout_s, poll_ms=poll_ms)
            self._federation_hub.activate()
            if wait_processes_running is None:
                wait_processes_running = start_processes
            if start_processes:
                self.start_all_processes()
            deadline = time.monotonic() + timeout_s
            if wait_processes_running:
                self._wait_processes_running(
                    deadline=deadline,
                    poll_ms=poll_ms,
                    managed_process_running=managed_process_running,
                )
            if start_drivers:
                self.start_all_drivers()
            if wait_for_registered:
                self._wait_registered(deadline=deadline, poll_ms=poll_ms)
            if connect is not None:
                self._emit_log(
                    severity="warning",
                    topic="manager.startup.connect_deprecated",
                    message=(
                        "startup_sequence(connect=...) is deprecated and ignored; "
                        "use manager.auto_connect_on_register for startup auto-connect "
                        "or call connect_all_devices manually"
                    ),
                    source_kind="manager",
                    source_id="manager",
                    stream="event",
                    payload={"connect_value": bool(connect)},
                )
            if wait_for_online:
                self._wait_online(
                    deadline=deadline,
                    poll_ms=poll_ms,
                    driver_state_ok=driver_state_ok,
                )
        finally:
            self._startup_sequence_active = False
            self._startup_sequence_complete_mono = time.monotonic()

    def _shutdown_cleanup(self) -> None:
        # Stop lifecycle workers BEFORE we tear down sockets — any
        # in-flight worker may still call _publish_manager_event
        # (queued) or have a pending reply to push. Drain both queues
        # afterwards so anything produced during the cancel/wait window
        # goes out on the still-open sockets.
        _safe_call(
            lambda: self._lifecycle_executor.shutdown(
                wait=True, cancel_futures=True
            )
        )
        _safe_call(self._drain_lifecycle_replies)
        _safe_call(self._drain_lifecycle_events)

        self._federation_hub.close()
        # Per-iteration default-arg captures (``dh=dev_handle``) bind
        # each lambda to its own handle — without this every lambda
        # would see the loop's final value when ``_safe_call`` invokes
        # it. Explicit per-loop names disambiguate the device-handle
        # and process-handle types for mypy.
        for dev_handle in self._devices.values():

            def _stop_drv(dh: "DeviceHandle" = dev_handle) -> None:
                self.stop_driver(dh.spec.device_id)

            _safe_call(_stop_drv)
        for proc_handle in self._processes.values():

            def _stop_proc(ph: "ProcessHandle" = proc_handle) -> None:
                self._stop_process_handle(ph)

            _safe_call(_stop_proc)
        self._drain_supervisor_logs(max_items=5000)
        self._flush_stale_supervisor_blocks(force=True)
        journal = self._command_journal
        if journal is not None:
            _safe_call(lambda: journal.close(timeout_s=2.0))
        self._close_manager_log_sink_file()

        _safe_close_socket(self._registry_rep)
        _safe_close_socket(self._sub)
        _safe_close_socket(self._process_hb_sub)
        _safe_close_socket(self._process_data_sub)
        _safe_close_socket(self._internal_rpc)
        _safe_close_socket(self._external_pub)
        _safe_call(self._ctx.term)
        _safe_call(self._process_guard.close)
