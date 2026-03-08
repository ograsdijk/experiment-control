# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.driver import DeviceRunner
from experiment_control.types import TelemetryCall, TelemetryOut, TelemetryQuality
from tests._temp_utils import repo_temp_dir

_DRIVER_CODE = """
class TelemetryPropertyDevice:
    def __init__(self) -> None:
        self.link_voltage_v = 24.5
        self._reads = 0

    def connect(self) -> None:
        return

    def disconnect(self) -> None:
        return

    @property
    def temperature_c(self) -> float:
        self._reads += 1
        return 20.0 + self._reads

    @property
    def status(self) -> dict[str, object]:
        return {"pressure_torr": 2.0e-5, "mode": "ok"}

    def scaled_value(self, scale: float = 1.0) -> float:
        return 3.0 * float(scale)

    def identity(self) -> dict[str, object]:
        return {"serial": "SIM-001", "model": "TelemetryPropertyDevice"}

    def __getattr__(self, name: str):
        if name == "dynamic_value":
            return 11.0
        if name == "dynamic_callable":
            return lambda scale=1.0: 4.0 * float(scale)
        raise AttributeError(name)
"""


class DeviceRunnerTelemetryPropertyTests(unittest.TestCase):
    def _build_runner(self, telemetry_calls: list[TelemetryCall]) -> DeviceRunner:
        ctx = repo_temp_dir("driver-telemetry-props")
        base = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        driver_path = Path(base) / "device.py"
        driver_path.write_text(_DRIVER_CODE, encoding="utf-8")
        runner = DeviceRunner(
            device_id="dev",
            device_class_path=str(driver_path),
            device_class_name="TelemetryPropertyDevice",
            device_init_kwargs={},
            registry_endpoint="tcp://127.0.0.1:5555",
            telemetry_calls=telemetry_calls,
        )
        self.addCleanup(runner.ctx.term)
        self.addCleanup(runner.disconnect_ipc)
        return runner

    def test_property_reads_and_extractors(self) -> None:
        runner = self._build_runner(
            [
                TelemetryCall(
                    method="status",
                    outputs=[
                        TelemetryOut(
                            signal="system_pressure_torr", kind="key", ref="pressure_torr"
                        ),
                        TelemetryOut(signal="mode", kind="key", ref="mode"),
                    ],
                )
            ]
        )
        got = runner.read_telemetry()
        self.assertEqual(got["system_pressure_torr"]["quality"], TelemetryQuality.OK)
        self.assertEqual(got["mode"]["quality"], TelemetryQuality.OK)
        self.assertEqual(got["mode"]["value"], "ok")

    def test_plain_attribute_reads(self) -> None:
        runner = self._build_runner(
            [TelemetryCall(method="link_voltage_v", outputs=[TelemetryOut(signal="link_v")])]
        )
        got = runner.read_telemetry()
        self.assertEqual(got["link_v"]["quality"], TelemetryQuality.OK)
        self.assertAlmostEqual(got["link_v"]["value"], 24.5)

    def test_property_with_kwargs_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not accept kwargs"):
            _ = self._build_runner(
                [
                    TelemetryCall(
                        method="temperature_c",
                        kwargs={"unused": 1},
                        outputs=[TelemetryOut(signal="temperature_c")],
                    )
                ]
            )

    def test_missing_member_is_marked_missing(self) -> None:
        runner = self._build_runner(
            [TelemetryCall(method="unknown_member", outputs=[TelemetryOut(signal="missing")])]
        )
        got = runner.read_telemetry()
        self.assertEqual(got["missing"]["quality"], TelemetryQuality.MISSING)

    def test_method_with_kwargs_still_supported(self) -> None:
        runner = self._build_runner(
            [
                TelemetryCall(
                    method="scaled_value",
                    kwargs={"scale": 2.5},
                    outputs=[TelemetryOut(signal="scaled")],
                )
            ]
        )
        got = runner.read_telemetry()
        self.assertEqual(got["scaled"]["quality"], TelemetryQuality.OK)
        self.assertAlmostEqual(got["scaled"]["value"], 7.5)

    def test_dynamic_attribute_via_getattr_supported(self) -> None:
        runner = self._build_runner(
            [
                TelemetryCall(
                    method="dynamic_value",
                    outputs=[TelemetryOut(signal="dynamic_value")],
                ),
                TelemetryCall(
                    method="dynamic_callable",
                    kwargs={"scale": 2.0},
                    outputs=[TelemetryOut(signal="dynamic_callable")],
                ),
            ]
        )
        got = runner.read_telemetry()
        self.assertEqual(got["dynamic_value"]["quality"], TelemetryQuality.OK)
        self.assertAlmostEqual(got["dynamic_value"]["value"], 11.0)
        self.assertEqual(got["dynamic_callable"]["quality"], TelemetryQuality.OK)
        self.assertAlmostEqual(got["dynamic_callable"]["value"], 8.0)

    def test_identity_rpc_action(self) -> None:
        runner = self._build_runner([])
        resp = runner._handle_rpc_request(
            {"id": "req-1", "action": "identity", "params": {}}
        )
        self.assertEqual(resp.get("status"), "OK")
        result = resp.get("result", {})
        self.assertEqual(result.get("serial"), "SIM-001")


if __name__ == "__main__":
    unittest.main()
