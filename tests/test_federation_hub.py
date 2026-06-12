# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.federation import parse_federation_config
from experiment_control.federation.hub import FederationHub


class FederationHubCapabilityTests(unittest.TestCase):
    def test_cached_capabilities_surface_in_list_devices(self) -> None:
        cfg = parse_federation_config(
            {
                "peers": [
                    {
                        "peer_id": "lab2",
                        "router_rpc": "tcp://10.0.0.22:6000",
                        "manager_pub": "tcp://10.0.0.22:6001",
                        "mirror_devices": [
                            {"local_id": "lab2.psu", "remote_device_id": "psu"}
                        ],
                    }
                ]
            },
            local_device_ids=set(),
            manager_raw={},
        )
        hub = FederationHub(
            ctx=zmq.Context.instance(),
            poller=zmq.Poller(),
            manager=object(),
            config=cfg,
            instance_id="lab1",
        )

        hub.update_capabilities(
            "lab2.psu",
            {"version": 1, "members": [{"name": "get"}, {"name": "set_voltage"}]},
        )

        devices = hub.list_devices_snapshot()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_id"], "lab2.psu")
        self.assertTrue(devices[0]["is_remote"])
        self.assertEqual(devices[0]["capabilities"]["version"], 1)
        members = devices[0]["capabilities"]["members"]
        self.assertEqual([m["name"] for m in members], ["get", "set_voltage"])

    def test_federated_status_uses_numeric_restart_count(self) -> None:
        cfg = parse_federation_config(
            {
                "peers": [
                    {
                        "peer_id": "lab2",
                        "router_rpc": "tcp://10.0.0.22:6000",
                        "manager_pub": "tcp://10.0.0.22:6001",
                        "mirror_devices": [
                            {"local_id": "lab2.psu", "remote_device_id": "psu"}
                        ],
                    }
                ]
            },
            local_device_ids=set(),
            manager_raw={},
        )
        hub = FederationHub(
            ctx=zmq.Context.instance(),
            poller=zmq.Poller(),
            manager=SimpleNamespace(_telemetry_last_bundle_ts={}),
            config=cfg,
            instance_id="lab1",
        )

        status = hub.device_status_snapshot("lab2.psu")

        self.assertEqual(status["driver_process"]["state"], "FEDERATED")
        self.assertEqual(status["driver_process"]["restart_count"], 0)

    def test_warm_capabilities_on_startup_fetches_remote_capabilities(self) -> None:
        cfg = parse_federation_config(
            {
                "peers": [
                    {
                        "peer_id": "lab2",
                        "router_rpc": "tcp://10.0.0.22:6000",
                        "manager_pub": "tcp://10.0.0.22:6001",
                        "warm_capabilities_on_startup": True,
                        "mirror_devices": [
                            {"local_id": "lab2.psu", "remote_device_id": "psu"}
                        ],
                    }
                ]
            },
            local_device_ids=set(),
            manager_raw={},
        )
        hub = FederationHub(
            ctx=zmq.Context.instance(),
            poller=zmq.Poller(),
            manager=object(),
            config=cfg,
            instance_id="lab1",
        )
        peer_rt = hub._peers["lab2"]
        calls: list[dict] = []

        def _fake_rpc_call(_peer_rt: object, payload: dict) -> dict:
            calls.append(dict(payload))
            if payload.get("type") == "device.config.list":
                return {
                    "ok": True,
                    "result": [
                        {
                            "device_id": "psu",
                            "yaml_text": "",
                            "device_metadata": {},
                            "stream_metadata": {},
                            "telemetry_calls": [],
                            "stream_calls": [],
                            "run_meta_calls": [],
                        }
                    ],
                }
            if payload.get("action") == "manager.telemetry.schema.list":
                return {
                    "ok": True,
                    "result": {"devices": [{"device_id": "psu", "signals": [], "dtypes": [], "units": []}]},
                }
            if payload.get("action") == "capabilities":
                return {
                    "ok": True,
                    "result": {"version": 1, "members": [{"name": "get"}]},
                }
            return {"ok": False, "error": "unexpected"}

        hub._rpc_call = _fake_rpc_call  # type: ignore[method-assign]
        hub._refresh_peer_metadata(peer_rt)

        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[2]["action"], "capabilities")
        devices = hub.list_devices_snapshot()
        self.assertEqual(devices[0]["capabilities"]["version"], 1)


if __name__ == "__main__":
    unittest.main()
