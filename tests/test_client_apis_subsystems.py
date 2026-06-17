# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.client.apis.influx import InfluxAPI
from experiment_control.client.apis.interlock import InterlockAPI
from experiment_control.client.apis.stream_analysis import StreamAnalysisAPI
from experiment_control.client.apis.watchdog import WatchdogAPI


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def rpc(
        self,
        payload: dict[str, Any],
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
        expect_ok: bool = True,
    ) -> Any:
        self.calls.append({"payload": payload, "expect_ok": expect_ok})
        return {"ok": True}

    def _last(self) -> dict[str, Any]:
        return self.calls[-1]["payload"]


class SubsystemFacadeTests(unittest.TestCase):
    def test_interlock_payloads(self) -> None:
        c = _FakeClient()
        api = InterlockAPI(c)  # type: ignore[arg-type]
        api.status()
        p = c._last()
        self.assertEqual(p["type"], "manager.processes.rpc")
        self.assertEqual(p["process_id"], "interlock")
        self.assertEqual(p["request"]["type"], "interlock.status")

        api.disable_rule("ruleset_a", "rule_1")
        self.assertEqual(
            c._last()["request"],
            {
                "type": "interlock.disable_rule",
                "params": {"interceptor_id": "ruleset_a", "rule_id": "rule_1"},
            },
        )

        api.load(path="rules/x.yaml", replace=False, enable=True)
        self.assertEqual(
            c._last()["request"]["params"],
            {"path": "rules/x.yaml", "replace": False, "enable": True},
        )

    def test_interlock_load_requires_exactly_one_source(self) -> None:
        api = InterlockAPI(_FakeClient())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            api.load()
        with self.assertRaises(ValueError):
            api.load(path="a.yaml", text="b")

    def test_watchdog_clear_latch_params(self) -> None:
        c = _FakeClient()
        api = WatchdogAPI(c)  # type: ignore[arg-type]
        api.clear_latch(all=True)
        self.assertEqual(c._last()["request"]["params"], {"all": True})
        api.clear_latch(watchdog_id="wd1", rule="overtemp")
        self.assertEqual(
            c._last()["request"]["params"], {"watchdog_id": "wd1", "rule": "overtemp"}
        )
        self.assertEqual(c._last()["process_id"], "watchdog")

    def test_influx_devices_enable(self) -> None:
        c = _FakeClient()
        api = InfluxAPI(c)  # type: ignore[arg-type]
        api.devices_enable(["dummy1", "dummy2"])
        p = c._last()
        self.assertEqual(p["process_id"], "influx_writer")
        self.assertEqual(p["request"]["type"], "influx.devices.enable")
        self.assertEqual(p["request"]["params"], {"device_ids": ["dummy1", "dummy2"]})

    def test_stream_analysis_payloads(self) -> None:
        c = _FakeClient()
        api = StreamAnalysisAPI(c)  # type: ignore[arg-type]
        api.workspace_get("ws-1")
        self.assertEqual(c._last()["request"]["type"], "stream_analysis.workspace.get")
        self.assertEqual(c._last()["request"]["params"], {"workspace_id": "ws-1"})

        api.workspace_put({"id": "ws-1"}, expected_revision=3)
        self.assertEqual(
            c._last()["request"]["params"],
            {"workspace": {"id": "ws-1"}, "expected_revision": 3},
        )

        api.workspace_reset()
        self.assertEqual(c._last()["request"]["params"], {})
        self.assertEqual(c._last()["process_id"], "stream_analysis")

    def test_custom_process_id(self) -> None:
        c = _FakeClient()
        api = InterlockAPI(c, process_id="interlock_b")  # type: ignore[arg-type]
        api.status()
        self.assertEqual(c._last()["process_id"], "interlock_b")


if __name__ == "__main__":
    unittest.main()
