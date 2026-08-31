from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from experiment_control._manager.device_routing import _route_command
from experiment_control._manager.models import Liveness
from experiment_control._manager.process_supervision import (
    _auto_reconnect_rpc_timeout_ms,
    maybe_restart_device_driver,
    stop_driver,
)
from experiment_control.federation.config import DEFAULT_FEDERATION_RELAY_TOPICS
from experiment_control.federation.hub import _mirrored_device_liveness
from experiment_control.manager import Manager


class StagedHeartbeatSecondReviewTests(unittest.TestCase):
    def test_explicit_stop_cancels_pending_automatic_restart(self) -> None:
        handle = SimpleNamespace(
            process=None,
            driver_pid=None,
            driver_process_state="FAILED",
            driver_next_restart_t_mono=50.0,
            driver_restart_count=0,
            rpc_endpoint=None,
            pub_endpoint=None,
            spec=SimpleNamespace(driver_max_restarts=3, device_id="dev"),
        )
        manager = SimpleNamespace(
            _devices={"dev": handle},
            _heartbeat_timeout_s=3.0,
            _last_liveness={},
            _publish_manager_event=mock.Mock(),
            _publish_driver_event=mock.Mock(),
            _close_device_rpc=mock.Mock(),
            start_driver=mock.Mock(),
        )

        stop_driver(manager, "dev")
        self.assertIsNone(handle.driver_next_restart_t_mono)
        self.assertEqual(str(handle.driver_process_state), "STOPPED")

        maybe_restart_device_driver(manager, "dev", handle, 100.0)
        manager.start_driver.assert_not_called()

    def test_explicit_stop_liveness_preserves_heartbeat_generation(self) -> None:
        handle = SimpleNamespace(
            process=None,
            driver_pid=None,
            driver_heartbeat_pid=123,
            driver_process_state="FAILED",
            driver_next_restart_t_mono=None,
            rpc_endpoint=None,
            pub_endpoint=None,
            spec=SimpleNamespace(
                driver_max_restarts=3,
                device_id="dev",
                driver_heartbeat_hard_timeout_s=12.0,
            ),
        )
        manager = SimpleNamespace(
            _devices={"dev": handle},
            _heartbeat_timeout_s=3.0,
            _heartbeat_hard_timeout_s=10.0,
            _last_liveness={},
            _publish_manager_event=mock.Mock(),
            _publish_driver_event=mock.Mock(),
            _close_device_rpc=mock.Mock(),
        )

        stop_driver(manager, "dev")

        topic, payload = manager._publish_manager_event.call_args.args
        self.assertEqual(topic, "manager.liveness")
        self.assertEqual(payload["heartbeat_pid"], 123)
        self.assertEqual(payload["heartbeat_hard_timeout_s"], 12.0)

    def _capability_manager(self, handle: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(
            _devices={"dev": handle},
            _federation_hub=SimpleNamespace(is_mirrored_device=lambda _did: False),
            _heartbeat_timeout_s=3.0,
            _normalize_command_source=lambda **kwargs: ("test", "test"),
            _normalize_id=lambda value: None if value is None else str(value),
            _device_heartbeat_age_s=lambda item, now: Manager._device_heartbeat_age_s(
                item, now
            ),
            _driver_is_stopped=lambda _item: False,
            _apply_command_interceptors=mock.Mock(
                side_effect=lambda cmd, **kwargs: (True, cmd, None)
            ),
            _build_manager_command_payload=lambda **kwargs: dict(kwargs),
            _publish_manager_event=mock.Mock(),
            _call_device_rpc=mock.Mock(),
        )

    def test_capabilities_use_running_since_before_first_heartbeat(self) -> None:
        cached = {"version": 1, "members": []}
        handle = SimpleNamespace(
            last_hb_recv_mono=None,
            driver_running_since_mono=0.0,
            driver_process_state="RUNNING",
            capabilities=cached,
            rpc_endpoint="tcp://127.0.0.1:12345",
        )
        manager = self._capability_manager(handle)

        with mock.patch(
            "experiment_control._manager.device_routing.time.monotonic",
            return_value=10.0,
        ):
            resp = _route_command(
                manager,
                {"device_id": "dev", "action": "capabilities", "params": {}},
            )

        self.assertEqual(resp, {"status": "OK", "result": cached})
        manager._call_device_rpc.assert_not_called()
        manager._apply_command_interceptors.assert_called_once()
        manager._publish_manager_event.assert_called_once()
        self.assertEqual(
            manager._publish_manager_event.call_args.args[0], "manager.command"
        )

    def test_cached_capabilities_still_obey_interceptor_rejection(self) -> None:
        handle = SimpleNamespace(
            last_hb_recv_mono=0.0,
            driver_running_since_mono=0.0,
            driver_process_state="RUNNING",
            capabilities={"version": 1, "members": []},
            rpc_endpoint="tcp://127.0.0.1:12345",
        )
        manager = self._capability_manager(handle)
        manager._apply_command_interceptors = mock.Mock(
            return_value=(False, None, "blocked by test")
        )

        with mock.patch(
            "experiment_control._manager.device_routing.time.monotonic",
            return_value=10.0,
        ):
            resp = _route_command(
                manager,
                {"device_id": "dev", "action": "capabilities", "params": {}},
            )

        self.assertFalse(resp["ok"])
        manager._call_device_rpc.assert_not_called()
        manager._apply_command_interceptors.assert_called_once()

    def test_stale_capabilities_rewrite_cannot_rpc_same_blocked_driver(self) -> None:
        handle = SimpleNamespace(
            last_hb_recv_mono=0.0,
            driver_running_since_mono=0.0,
            driver_process_state="RUNNING",
            capabilities={"version": 1, "members": []},
            rpc_endpoint="tcp://127.0.0.1:12345",
        )
        manager = self._capability_manager(handle)
        manager._apply_command_interceptors = mock.Mock(
            return_value=(
                True,
                {"device_id": "dev", "action": "identity", "params": {}},
                None,
            )
        )

        with mock.patch(
            "experiment_control._manager.device_routing.time.monotonic",
            return_value=10.0,
        ):
            resp = _route_command(
                manager,
                {"device_id": "dev", "action": "capabilities", "params": {}},
            )

        self.assertFalse(resp["ok"])
        manager._call_device_rpc.assert_not_called()

    def test_failed_driver_with_late_fresh_heartbeat_stays_offline(self) -> None:
        manager = object.__new__(Manager)
        manager._heartbeat_timeout_s = 3.0
        handle = SimpleNamespace(
            last_hb_recv_mono=99.5,
            driver_running_since_mono=None,
            driver_process_state="FAILED",
            last_hb=SimpleNamespace(device_reachable=True),
            spec=SimpleNamespace(driver_heartbeat_hard_timeout_s=None),
        )
        manager._heartbeat_hard_timeout_s = 10.0
        manager._devices = {"dev": handle}
        manager._last_liveness = {}
        manager._publish_manager_event = mock.Mock()

        manager._update_device_liveness(100.0)

        self.assertEqual(manager._last_liveness["dev"], Liveness.OFFLINE)
        topic, payload = manager._publish_manager_event.call_args.args
        self.assertEqual(topic, "manager.liveness")
        self.assertEqual(payload["liveness"], Liveness.OFFLINE)

    def test_federated_stale_uses_owner_hard_timeout(self) -> None:
        peer_rt = SimpleNamespace(config=SimpleNamespace(event_stale_s=3.0))
        mirror = SimpleNamespace(
            last_hb_recv_mono=96.0,
            last_hb_payload={"device_reachable": True},
            last_liveness="STALE",
            last_liveness_hard_timeout_s=10.0,
        )

        liveness, age_s = _mirrored_device_liveness(peer_rt, mirror, 100.0)
        self.assertEqual(liveness, "STALE")
        self.assertEqual(age_s, 4.0)

        liveness, age_s = _mirrored_device_liveness(peer_rt, mirror, 107.0)
        self.assertEqual(liveness, "OFFLINE")
        self.assertEqual(age_s, 11.0)

    def test_federated_stale_without_first_heartbeat_still_reaches_offline(self) -> None:
        peer_rt = SimpleNamespace(config=SimpleNamespace(event_stale_s=3.0))
        mirror = SimpleNamespace(
            last_hb_recv_mono=None,
            last_hb_payload=None,
            last_liveness="STALE",
            last_liveness_hard_timeout_s=10.0,
            last_liveness_recv_mono=100.0,
            last_liveness_age_s=3.5,
        )

        liveness, age_s = _mirrored_device_liveness(peer_rt, mirror, 101.0)
        self.assertEqual(liveness, "STALE")
        self.assertEqual(age_s, 4.5)

        liveness, age_s = _mirrored_device_liveness(peer_rt, mirror, 107.0)
        self.assertEqual(liveness, "OFFLINE")
        self.assertEqual(age_s, 10.5)

    def test_federated_fresh_heartbeat_recovers_when_liveness_event_is_lost(self) -> None:
        peer_rt = SimpleNamespace(config=SimpleNamespace(event_stale_s=3.0))
        mirror = SimpleNamespace(
            last_hb_recv_mono=104.0,
            last_hb_payload={"pid": 11, "device_reachable": True},
            last_liveness="STALE",
            last_liveness_hard_timeout_s=10.0,
            last_liveness_recv_mono=100.0,
            last_liveness_age_s=3.5,
            last_liveness_heartbeat_pid=11,
        )

        liveness, age_s = _mirrored_device_liveness(peer_rt, mirror, 104.1)

        self.assertEqual(liveness, "ONLINE")
        self.assertAlmostEqual(age_s, 0.1)

    def test_federated_late_heartbeat_cannot_revive_failed_generation(self) -> None:
        peer_rt = SimpleNamespace(config=SimpleNamespace(event_stale_s=3.0))
        mirror = SimpleNamespace(
            last_hb_recv_mono=101.0,
            last_hb_payload={"pid": 11, "device_reachable": True},
            last_liveness="OFFLINE",
            last_liveness_hard_timeout_s=10.0,
            last_liveness_recv_mono=100.0,
            last_liveness_age_s=10.0,
            last_liveness_heartbeat_pid=11,
        )

        liveness, _age_s = _mirrored_device_liveness(peer_rt, mirror, 101.1)

        self.assertEqual(liveness, "OFFLINE")

    def test_federated_new_generation_heartbeat_recovers_lost_online_event(self) -> None:
        peer_rt = SimpleNamespace(config=SimpleNamespace(event_stale_s=3.0))
        mirror = SimpleNamespace(
            last_hb_recv_mono=101.0,
            last_hb_payload={"pid": 12, "device_reachable": False},
            last_liveness="OFFLINE",
            last_liveness_hard_timeout_s=10.0,
            last_liveness_recv_mono=100.0,
            last_liveness_age_s=10.0,
            last_liveness_heartbeat_pid=11,
        )

        liveness, age_s = _mirrored_device_liveness(peer_rt, mirror, 101.1)

        self.assertEqual(liveness, "DISCONNECTED")
        self.assertAlmostEqual(age_s, 0.1)

    def test_auto_reconnect_rpc_is_bounded_by_soft_heartbeat_deadline(self) -> None:
        manager = SimpleNamespace(
            _heartbeat_timeout_s=3.0,
            _device_heartbeat_age_s=lambda _handle, _now: 2.75,
        )

        timeout_ms = _auto_reconnect_rpc_timeout_ms(
            manager,
            SimpleNamespace(),
            60_000,
        )

        self.assertGreater(timeout_ms, 0)
        self.assertLessEqual(timeout_ms, 250)

    def test_auto_reconnect_rpc_stops_after_soft_heartbeat_deadline(self) -> None:
        manager = SimpleNamespace(
            _heartbeat_timeout_s=3.0,
            _device_heartbeat_age_s=lambda _handle, _now: 3.01,
        )

        with self.assertRaisesRegex(TimeoutError, "heartbeat became stale"):
            _auto_reconnect_rpc_timeout_ms(manager, SimpleNamespace(), 60_000)

    def test_federation_default_relay_includes_liveness(self) -> None:
        self.assertIn("manager.liveness", DEFAULT_FEDERATION_RELAY_TOPICS)


if __name__ == "__main__":
    unittest.main()
