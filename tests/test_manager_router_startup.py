import unittest
from types import SimpleNamespace
from unittest import mock

from experiment_control.manager import ManagedProcessState, Manager


class _ExitedPopen:
    def poll(self) -> int:
        return 1


class _LivePopen:
    def poll(self) -> None:
        return None


class ManagerRouterStartupTests(unittest.TestCase):
    def test_build_router_spec_scales_heartbeat_timeout_with_rpc_timeout(self) -> None:
        mgr = object.__new__(Manager)
        mgr._external_rpc_bind = "tcp://127.0.0.1:6110"  # type: ignore[attr-defined]
        mgr._device_rpc_timeout_ms = 10_000  # type: ignore[attr-defined]
        mgr._interceptor_rpc_timeout_ms = 500  # type: ignore[attr-defined]
        mgr._federation_hub = SimpleNamespace(  # type: ignore[attr-defined]
            mirror_route_entries=lambda: []
        )
        mgr._router_manager_worker_queue_max = 8192  # type: ignore[attr-defined]
        mgr._router_process_worker_queue_max = 8192  # type: ignore[attr-defined]
        mgr._router_device_worker_queue_max = 16384  # type: ignore[attr-defined]
        mgr._router_mirrored_worker_queue_max = 8192  # type: ignore[attr-defined]
        mgr._router_reply_queue_max = 32768  # type: ignore[attr-defined]
        mgr._router_inflight_max = 32768  # type: ignore[attr-defined]
        mgr._instance_id = "test-instance"  # type: ignore[attr-defined]
        mgr._internal_rpc_endpoint = "tcp://127.0.0.1:6200"  # type: ignore[attr-defined]
        mgr._external_pub_connect_local = "tcp://127.0.0.1:6201"  # type: ignore[attr-defined]
        mgr._router_process_id = "device_router"  # type: ignore[attr-defined]

        spec = Manager._build_router_spec(mgr)  # type: ignore[arg-type]
        self.assertGreaterEqual(spec.heartbeat_timeout_s, 12.0)

    def test_ensure_router_running_tolerates_transient_early_exit(self) -> None:
        handle = SimpleNamespace(
            state=ManagedProcessState.STARTING,
            popen=_ExitedPopen(),
            last_error="heartbeat stale",
            spec=SimpleNamespace(process_id="device_router"),
        )
        mgr = object.__new__(Manager)
        mgr._ensure_router_handle = mock.Mock(return_value=handle)  # type: ignore[attr-defined]
        mgr._start_process_handle = mock.Mock()  # type: ignore[attr-defined]

        steps = {"n": 0}

        def _pump_once(*, poll_ms: int) -> None:
            del poll_ms
            steps["n"] += 1
            if steps["n"] == 1:
                # Simulate supervisor observing early exit.
                handle.state = ManagedProcessState.FAILED
                handle.popen = None
                return
            if steps["n"] == 2:
                # Simulate restart in progress.
                handle.state = ManagedProcessState.STARTING
                handle.popen = _LivePopen()
                return
            handle.state = ManagedProcessState.RUNNING

        mgr._pump_once = mock.Mock(side_effect=_pump_once)  # type: ignore[attr-defined]
        mgr._drain_supervisor_logs = mock.Mock()  # type: ignore[attr-defined]
        mgr._flush_stale_supervisor_blocks = mock.Mock()  # type: ignore[attr-defined]

        Manager._ensure_router_running(  # type: ignore[arg-type]
            mgr,
            timeout_s=1.0,
            poll_ms=1,
        )
        self.assertGreaterEqual(steps["n"], 3)


if __name__ == "__main__":
    unittest.main()
