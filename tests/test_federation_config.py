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
