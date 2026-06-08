# ruff: noqa: E402

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.client.protocol import ManagerProtocol
from experiment_control.manager_client import ManagerClient


class ManagerClientProtocolTests(unittest.TestCase):
    def test_manager_client_protocol_signatures_match(self) -> None:
        for name in ("call", "get_latest", "drain_telemetry", "publish_event"):
            with self.subTest(method=name):
                proto = inspect.signature(getattr(ManagerProtocol, name))
                actual = inspect.signature(getattr(ManagerClient, name))
                proto_params = [p for p in proto.parameters.values() if p.name != "self"]
                actual_params = [p for p in actual.parameters.values() if p.name != "self"]
                self.assertEqual(
                    [(p.name, p.kind) for p in proto_params],
                    [(p.name, p.kind) for p in actual_params],
                )
                self.assertEqual(
                    {
                        p.name: p.default
                        for p in proto_params
                        if p.default is not inspect.Parameter.empty
                    },
                    {
                        p.name: p.default
                        for p in actual_params
                        if p.default is not inspect.Parameter.empty
                    },
                )


if __name__ == "__main__":
    unittest.main()
