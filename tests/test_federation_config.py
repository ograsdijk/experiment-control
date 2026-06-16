# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.federation import parse_federation_config
from experiment_control.utils.config_parsing import ConfigError


class FederationConfigTests(unittest.TestCase):
    def test_minimal_config_parses_with_defaults(self) -> None:
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
            local_device_ids={"psu_local"},
            manager_raw={},
        )
        self.assertTrue(cfg.enabled)
        self.assertEqual(len(cfg.peers), 1)
        peer = cfg.peers[0]
        self.assertEqual(peer.peer_id, "lab2")
        self.assertEqual(peer.rpc_timeout_ms, 1500)
        self.assertEqual(peer.metadata_rpc_timeout_ms, 1500)
        self.assertEqual(peer.event_stale_s, 3.0)
        self.assertFalse(peer.warm_capabilities_on_startup)
        self.assertEqual(peer.policy.allow_device_actions, ("*",))
        self.assertFalse(peer.policy.allow_lifecycle_ops)
        self.assertEqual(peer.mirror_devices[0].local_id, "lab2.psu")

    def test_warm_capabilities_on_startup_true_parses(self) -> None:
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
        self.assertTrue(cfg.peers[0].warm_capabilities_on_startup)

    def test_metadata_rpc_timeout_override_and_manager_default(self) -> None:
        # Per-peer override wins; otherwise the manager-level default applies;
        # otherwise 500 ms. Crucially independent of device_rpc_timeout_ms so a
        # large device timeout can't be inherited by the startup metadata warmup.
        cfg = parse_federation_config(
            {
                "peers": [
                    {
                        "peer_id": "lab2",
                        "router_rpc": "tcp://10.0.0.22:6000",
                        "manager_pub": "tcp://10.0.0.22:6001",
                        "metadata_rpc_timeout_ms": 250,
                        "mirror_devices": [
                            {"local_id": "lab2.psu", "remote_device_id": "psu"}
                        ],
                    },
                    {
                        "peer_id": "lab3",
                        "router_rpc": "tcp://10.0.0.23:6000",
                        "manager_pub": "tcp://10.0.0.23:6001",
                        "mirror_devices": [
                            {"local_id": "lab3.psu", "remote_device_id": "psu"}
                        ],
                    },
                ]
            },
            local_device_ids=set(),
            manager_raw={
                "device_rpc_timeout_ms": 10000,
                "federation_metadata_rpc_timeout_ms": 700,
            },
        )
        by_id = {p.peer_id: p for p in cfg.peers}
        self.assertEqual(by_id["lab2"].metadata_rpc_timeout_ms, 250)
        self.assertEqual(by_id["lab3"].metadata_rpc_timeout_ms, 700)
        # The full device RPC timeout still flows to rpc_timeout_ms (used for
        # live federated commands), unaffected by the short metadata timeout.
        self.assertEqual(by_id["lab3"].rpc_timeout_ms, 10000)

    def test_collision_with_local_device_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            parse_federation_config(
                {
                    "peers": [
                        {
                            "peer_id": "lab2",
                            "router_rpc": "tcp://10.0.0.22:6000",
                            "manager_pub": "tcp://10.0.0.22:6001",
                            "mirror_devices": [
                                {"local_id": "psu", "remote_device_id": "psu"}
                            ],
                        }
                    ]
                },
                local_device_ids={"psu"},
                manager_raw={},
            )

    def test_allow_reexport_true_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            parse_federation_config(
                {
                    "peers": [
                        {
                            "peer_id": "lab2",
                            "router_rpc": "tcp://10.0.0.22:6000",
                            "manager_pub": "tcp://10.0.0.22:6001",
                            "allow_reexport": True,
                            "mirror_devices": [
                                {"local_id": "lab2.psu", "remote_device_id": "psu"}
                            ],
                        }
                    ]
                },
                local_device_ids=set(),
                manager_raw={},
            )


if __name__ == "__main__":
    unittest.main()
