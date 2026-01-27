# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils.config_parsing import ConfigError
from experiment_control.utils.manager_network import resolve_manager_network


class ManagerNetworkConfigTests(unittest.TestCase):
    def test_compact_schema_derives_loopback_connect_endpoints(self) -> None:
        cfg = resolve_manager_network(
            {
                "bind_host": "0.0.0.0",
                "external": {"rpc_port": 7000, "pub_port": 7001},
                "internal_ports": {"registry": 5555, "rpc": 6002, "heartbeat_base": 7100},
            }
        )
        self.assertEqual(cfg.external_rpc_bind, "tcp://0.0.0.0:7000")
        self.assertEqual(cfg.external_pub_bind, "tcp://0.0.0.0:7001")
        self.assertEqual(cfg.local_rpc_connect, "tcp://127.0.0.1:7000")
        self.assertEqual(cfg.local_pub_connect, "tcp://127.0.0.1:7001")
        self.assertEqual(cfg.registry_bind, "tcp://127.0.0.1:5555")
        self.assertEqual(cfg.internal_rpc_bind, "tcp://127.0.0.1:6002")
        self.assertEqual(cfg.process_hb_bind_base, "tcp://127.0.0.1:7100")

    def test_advertise_host_overrides_public_hints(self) -> None:
        cfg = resolve_manager_network(
            {
                "bind_host": "0.0.0.0",
                "advertise_host": "laser-lock-1.local",
                "external": {"rpc_port": 7000, "pub_port": 7001},
            }
        )
        self.assertEqual(cfg.public_rpc_hint, "tcp://laser-lock-1.local:7000")
        self.assertEqual(cfg.public_pub_hint, "tcp://laser-lock-1.local:7001")

    def test_legacy_endpoints_still_supported(self) -> None:
        cfg = resolve_manager_network(
            {
                "external_rpc_bind": "tcp://10.10.222.31:7000",
                "external_pub_bind": "tcp://10.10.222.31:7001",
                "registry_bind": "tcp://127.0.0.1:7555",
                "internal_rpc_bind": "tcp://127.0.0.1:7602",
                "process_hb_bind_base": "tcp://127.0.0.1:7700",
            }
        )
        self.assertEqual(cfg.external_rpc_bind, "tcp://10.10.222.31:7000")
        self.assertEqual(cfg.external_pub_bind, "tcp://10.10.222.31:7001")
        self.assertEqual(cfg.local_rpc_connect, "tcp://127.0.0.1:7000")
        self.assertEqual(cfg.local_pub_connect, "tcp://127.0.0.1:7001")
        self.assertEqual(cfg.public_rpc_hint, "tcp://10.10.222.31:7000")
        self.assertEqual(cfg.public_pub_hint, "tcp://10.10.222.31:7001")
        self.assertEqual(cfg.registry_bind, "tcp://127.0.0.1:7555")
        self.assertEqual(cfg.internal_rpc_bind, "tcp://127.0.0.1:7602")
        self.assertEqual(cfg.process_hb_bind_base, "tcp://127.0.0.1:7700")

    def test_invalid_port_raises(self) -> None:
        with self.assertRaises(ConfigError):
            _ = resolve_manager_network(
                {"external": {"rpc_port": 70000, "pub_port": 7001}}
            )


if __name__ == "__main__":
    unittest.main()
