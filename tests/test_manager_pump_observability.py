from __future__ import annotations

import queue
from types import SimpleNamespace
from unittest import mock

import pytest
import zmq

import experiment_control.manager as manager_module
from experiment_control.manager import Manager


class _Clock:
    def __init__(self) -> None:
        self.wall = 100.0
        self.process_cpu = 20.0
        self.thread_cpu = 10.0

    def monotonic(self) -> float:
        return self.wall

    def process_time(self) -> float:
        return self.process_cpu

    def thread_time(self) -> float:
        return self.thread_cpu

    def advance(self, wall_s: float) -> None:
        self.wall += wall_s
        self.process_cpu += wall_s * 0.25
        self.thread_cpu += wall_s * 0.20


def _drain(q: queue.Queue[object]) -> int:
    count = 0
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return count
        count += 1


def _make_manager(
    clock: _Clock, *, internal_rpc_duration_s: float
) -> tuple[Manager, list[tuple[str, dict[str, object]]], list[str]]:
    manager = object.__new__(Manager)
    published: list[tuple[str, dict[str, object]]] = []
    order: list[str] = []
    durations = {
        "supervisor_logs_pre": 0.01,
        "poll": 0.02,
        "registry": 0.03,
        "driver_pub": 0.04,
        "process_hb": 0.05,
        "process_data": 0.06,
        "federation": 0.07,
        "internal_rpc": internal_rpc_duration_s,
        "supervisor_logs_post": 0.09,
        "lifecycle_replies": 0.10,
        "lifecycle_events": 0.11,
        "check_timeouts": 0.12,
    }

    manager._last_pump_start_mono = None
    manager._last_pump_end_mono = None
    manager._last_pump_duration_s = None
    manager._last_pump_gap_s = None
    manager._last_loop_stall_mono = None
    manager._last_loop_stall_duration_s = None
    manager._manager_loop_stall_warn_s = 1.0
    manager._loop_stall_count = 0
    manager._slow_pump_threshold_s = 1.0
    manager._slow_pump_count = 0
    manager._manager_driver_pub_drain_cap_hit_total = 0
    manager._manager_process_hb_drain_cap_hit_total = 0
    manager._manager_process_data_drain_cap_hit_total = 0

    class _Socket:
        def __init__(self, readable_after: bool) -> None:
            self.readable_after = readable_after

        def getsockopt(self, option: int) -> int:
            assert option == zmq.EVENTS
            return zmq.POLLIN if self.readable_after else 0

    manager._registry_rep = object()
    manager._sub = _Socket(True)
    manager._process_hb_sub = _Socket(False)
    manager._process_data_sub = _Socket(True)
    manager._internal_rpc = _Socket(True)
    ready = {
        manager._registry_rep: zmq.POLLIN,
        manager._sub: zmq.POLLIN,
        manager._process_hb_sub: zmq.POLLIN,
        manager._process_data_sub: zmq.POLLIN,
        manager._internal_rpc: zmq.POLLIN,
    }

    def phase(name: str, result: object = None) -> object:
        order.append(name)
        clock.advance(durations[name])
        return result

    manager._supervisor_log_queue = queue.Queue()
    manager._lifecycle_reply_queue = queue.Queue()
    manager._lifecycle_event_queue = queue.Queue()
    for item in ("log-1", "log-2"):
        manager._supervisor_log_queue.put(item)
    manager._lifecycle_reply_queue.put("reply")
    manager._lifecycle_event_queue.put("event")

    supervisor_calls = 0

    def drain_supervisor_logs() -> int:
        nonlocal supervisor_calls
        name = "supervisor_logs_pre" if supervisor_calls == 0 else "supervisor_logs_post"
        supervisor_calls += 1
        phase(name)
        return _drain(manager._supervisor_log_queue)

    manager._drain_supervisor_logs = drain_supervisor_logs
    manager._poller = SimpleNamespace(poll=lambda _timeout: phase("poll", ready))
    manager._handle_registry = lambda: phase("registry")
    def drain_driver_pub() -> object:
        manager._manager_driver_pub_drain_cap_hit_total += 1
        return phase("driver_pub", 3)

    def drain_process_hb() -> object:
        manager._manager_process_hb_drain_cap_hit_total += 1
        return phase("process_hb", 4)

    def drain_process_data() -> object:
        manager._manager_process_data_drain_cap_hit_total += 1
        return phase("process_data", 5)

    manager._handle_driver_pub = drain_driver_pub
    manager._handle_process_pub = drain_process_hb
    manager._handle_process_data_pub = drain_process_data
    manager._federation_hub = SimpleNamespace(
        handle_poll_events=lambda _events: phase("federation", 6)
    )
    manager._handle_internal_rpc = lambda: phase("internal_rpc")

    def drain_replies() -> int:
        phase("lifecycle_replies")
        return _drain(manager._lifecycle_reply_queue)

    def drain_events() -> int:
        phase("lifecycle_events")
        return _drain(manager._lifecycle_event_queue)

    manager._drain_lifecycle_replies = drain_replies
    manager._drain_lifecycle_events = drain_events
    manager._check_timeouts = lambda: phase("check_timeouts")
    manager._publish_manager_event = lambda topic, payload: published.append(
        (topic, payload)
    )
    return manager, published, order


def _run_pump(manager: Manager, clock: _Clock, *, thread_clock: bool = True) -> None:
    thread_time = clock.thread_time if thread_clock else None
    with (
        mock.patch.object(manager_module.time, "monotonic", clock.monotonic),
        mock.patch.object(manager_module.time, "process_time", clock.process_time),
        mock.patch.object(manager_module.time, "thread_time", thread_time),
    ):
        manager._pump_once(poll_ms=0)


def test_normal_pump_emits_no_detailed_event_and_preserves_phase_order() -> None:
    clock = _Clock()
    manager, published, order = _make_manager(clock, internal_rpc_duration_s=0.08)

    _run_pump(manager, clock)

    assert [topic for topic, _ in published if topic == "manager.pump_slow"] == []
    assert order == [
        "supervisor_logs_pre",
        "poll",
        "registry",
        "driver_pub",
        "process_hb",
        "process_data",
        "federation",
        "internal_rpc",
        "supervisor_logs_post",
        "lifecycle_replies",
        "lifecycle_events",
        "check_timeouts",
    ]


def test_slow_pump_emits_one_breakdown_with_cpu_counts_and_depths() -> None:
    clock = _Clock()
    manager, published, _ = _make_manager(clock, internal_rpc_duration_s=1.20)

    _run_pump(manager, clock)

    slow_events = [payload for topic, payload in published if topic == "manager.pump_slow"]
    assert len(slow_events) == 1
    payload = slow_events[0]
    phases = payload["phase_durations_s"]
    assert phases["internal_rpc"] == pytest.approx(1.20)
    assert sum(phases.values()) == pytest.approx(payload["duration_s"])
    assert payload["phase_total_s"] == pytest.approx(payload["duration_s"])
    assert payload["unattributed_wall_s"] == pytest.approx(0.0)
    assert payload["process_cpu_s"] == pytest.approx(payload["duration_s"] * 0.25)
    assert payload["thread_cpu_s"] == pytest.approx(payload["duration_s"] * 0.20)
    assert payload["thread_cpu_clock_available"] is True
    assert payload["work_counts"] == {
        "supervisor_logs_pre": 2,
        "registry": 1,
        "driver_pub": 3,
        "process_hb": 4,
        "process_data": 5,
        "federation": 6,
        "internal_rpc": 1,
        "supervisor_logs_post": 0,
        "lifecycle_replies": 1,
        "lifecycle_events": 1,
        "check_timeouts": 1,
    }
    assert payload["queue_depths_before"] == {
        "supervisor_logs": 2,
        "lifecycle_replies": 1,
        "lifecycle_events": 1,
    }
    assert payload["queue_depths_after"] == {
        "supervisor_logs": 0,
        "lifecycle_replies": 0,
        "lifecycle_events": 0,
    }
    assert payload["poll_ready_count"] == 5
    assert payload["internal_rpc_ready"] is True
    assert payload["socket_backlog_after"] == {
        "driver_pub": True,
        "process_hb": False,
        "process_data": True,
        "internal_rpc": True,
    }
    assert payload["drain_cap_hits"] == {
        "driver_pub": True,
        "process_hb": True,
        "process_data": True,
    }


def test_missing_thread_clock_falls_back_to_process_cpu_measurement() -> None:
    clock = _Clock()
    manager, published, _ = _make_manager(clock, internal_rpc_duration_s=1.20)

    _run_pump(manager, clock, thread_clock=False)

    payload = next(payload for topic, payload in published if topic == "manager.pump_slow")
    assert payload["thread_cpu_s"] is None
    assert payload["thread_cpu_clock_available"] is False
    assert payload["process_cpu_s"] == pytest.approx(payload["duration_s"] * 0.25)
