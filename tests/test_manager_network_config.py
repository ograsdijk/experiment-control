# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils.config_parsing import ConfigError
from experiment_control.utils.manager_network import (
    connect_dealer,
    resolve_manager_network,
    resolve_tcp_endpoint,
)


class ResolveTcpEndpointTests(unittest.TestCase):
    def test_ipv4_literal_passes_through(self) -> None:
        self.assertEqual(
            resolve_tcp_endpoint("tcp://10.10.222.12:6002"), "tcp://10.10.222.12:6002"
        )

    def test_wildcard_passes_through(self) -> None:
        self.assertEqual(resolve_tcp_endpoint("tcp://0.0.0.0:7000"), "tcp://0.0.0.0:7000")

    def test_localhost_resolves_to_ipv4_loopback(self) -> None:
        # Must prefer IPv4 (the stack binds IPv4), not ::1.
        self.assertEqual(resolve_tcp_endpoint("tcp://localhost:6000"), "tcp://127.0.0.1:6000")

    def test_unresolvable_host_returns_none(self) -> None:
        self.assertIsNone(resolve_tcp_endpoint("tcp://TODO_BRISTOL_WAVEMETER_HOST:6412"))

    def test_malformed_host_returns_none_not_raises(self) -> None:
        # getaddrinfo raises UnicodeError (NOT OSError) for these; resolve must
        # normalize to None, never let it escape onto the warmup thread/poll loop.
        self.assertIsNone(resolve_tcp_endpoint("tcp://" + "x" * 64 + ":6000"))
        self.assertIsNone(resolve_tcp_endpoint("tcp://a..b:6000"))

    def test_empty_host_returns_none(self) -> None:
        self.assertIsNone(resolve_tcp_endpoint("tcp://:5555"))

    def test_non_tcp_or_malformed_returns_none(self) -> None:
        self.assertIsNone(resolve_tcp_endpoint("not-an-endpoint"))
        self.assertIsNone(resolve_tcp_endpoint("tcp://hostonly"))


class _FakeSock:
    def __init__(self) -> None:
        self.opts: dict[int, int] = {}
        self.connected: str | None = None

    def setsockopt(self, opt: int, val: int) -> None:
        self.opts[opt] = val

    def connect(self, endpoint: str) -> None:
        self.connected = endpoint


class _FakeCtx:
    def __init__(self) -> None:
        self.last: _FakeSock | None = None

    def socket(self, _type: int) -> _FakeSock:
        self.last = _FakeSock()
        return self.last


class ConnectDealerTests(unittest.TestCase):
    def test_sets_timeouts_and_connects_to_resolved_ip(self) -> None:
        ctx = _FakeCtx()
        sock = connect_dealer(ctx, "tcp://10.0.0.5:6000", timeout_ms=1500)
        self.assertIs(sock, ctx.last)
        self.assertEqual(sock.connected, "tcp://10.0.0.5:6000")
        self.assertEqual(sock.opts[zmq.LINGER], 0)
        self.assertEqual(sock.opts[zmq.RCVTIMEO], 1500)
        self.assertEqual(sock.opts[zmq.SNDTIMEO], 1500)


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
