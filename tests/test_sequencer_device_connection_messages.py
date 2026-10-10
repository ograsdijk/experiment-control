from __future__ import annotations

import unittest

from experiment_control.sequencer.ast import parse_sequence
from tests.test_sequencer_requires_preconditions import _build_process, _FakeManager


class _DetailedManager(_FakeManager):
    def __init__(self, items: list[dict]) -> None:
        super().__init__(hdf_status=None, device_liveness={})
        self._items = items

    def call(self, payload, *, timeout_ms=None):  # noqa: ANN001
        if isinstance(payload, dict) and payload.get("type") == "device.list_status":
            return {"ok": True, "result": self._items}
        return super().call(payload, timeout_ms=timeout_ms)


def _spec(*devices: str):
    return parse_sequence(
        {
            "version": 1,
            "steps": [
                {"call": {"device": d, "action": "x", "params": {}}} for d in devices
            ],
        }
    )


def _start(items: list[dict], *devices: str) -> dict:
    process = _build_process(spec=_spec(*devices), manager=_DetailedManager(items))
    return process._rpc_sequencer_start({"params": {}})


class DeviceConnectionMessageTests(unittest.TestCase):
    def test_unknown_device_suggests_close_name(self) -> None:
        resp = _start([{"device_id": "freq1", "liveness": "ONLINE"}], "freqq1")
        self.assertFalse(resp["ok"])
        msg = resp["error"]["message"]
        self.assertIn("devices not connected:", msg)
        self.assertIn("freqq1 (not configured (did you mean 'freq1'?))", msg)

    def test_degraded_device_reports_state_and_last_error(self) -> None:
        resp = _start(
            [
                {
                    "device_id": "freq1",
                    "liveness": "ONLINE",
                    "device_state": "DEGRADED",
                    "last_error": "command 'x' failed: boom",
                }
            ],
            "freq1",
        )
        msg = resp["error"]["message"]
        self.assertIn("freq1 (configured but ONLINE/DEGRADED", msg)
        self.assertIn("last error: command 'x' failed: boom", msg)

    def test_offline_device(self) -> None:
        resp = _start([{"device_id": "freq1", "liveness": "OFFLINE"}], "freq1")
        self.assertIn("freq1 (configured but OFFLINE)", resp["error"]["message"])

    def test_healthy_device_starts(self) -> None:
        resp = _start(
            [{"device_id": "freq1", "liveness": "ONLINE", "device_state": "OK"}],
            "freq1",
        )
        self.assertTrue(resp["ok"])


if __name__ == "__main__":
    unittest.main()
