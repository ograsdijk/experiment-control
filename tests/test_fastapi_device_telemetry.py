# ruff: noqa: E402

import asyncio
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    # See test_fastapi_capabilities_retry.py: the package re-exports the app
    # instance, so grab the submodule via importlib.
    fastapi_app_module = importlib.import_module("experiment_control.fastapi.app")

    _FASTAPI_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment specific
    fastapi_app_module = None  # type: ignore[assignment]
    _FASTAPI_IMPORT_ERROR = exc


class _RouterStub:
    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[dict] = []

    async def request(self, payload: dict, timeout_ms: int | None = None) -> dict:
        del timeout_ms
        self.calls.append(dict(payload))
        return self._response


def _call(router: _RouterStub, device_id: str) -> dict:
    original_router = getattr(fastapi_app_module.app.state, "router", None)
    fastapi_app_module.app.state.router = router
    try:
        return asyncio.run(fastapi_app_module.device_telemetry_snapshot(device_id))
    finally:
        fastapi_app_module.app.state.router = original_router


@unittest.skipIf(
    _FASTAPI_IMPORT_ERROR is not None,
    f"fastapi app import unavailable: {_FASTAPI_IMPORT_ERROR!r}",
)
class DeviceTelemetrySnapshotTests(unittest.TestCase):
    def test_routes_single_device_get_and_wraps_result(self) -> None:
        signal = {"value": 1.5, "units": "V", "quality": "OK", "ts": {"t_mono_recv": 2.0}}
        router = _RouterStub({"ok": True, "telemetry": {"v": signal}})

        resp = _call(router, "synth")

        self.assertEqual(
            router.calls, [{"type": "manager.telemetry.get", "device_id": "synth"}]
        )
        self.assertEqual(
            resp, {"ok": True, "result": {"device_id": "synth", "telemetry": {"v": signal}}}
        )

    def test_unknown_device_returns_empty_telemetry(self) -> None:
        resp = _call(_RouterStub({"ok": True, "telemetry": {}}), "missing")

        self.assertEqual(resp["result"]["telemetry"], {})

    def test_router_error_passes_through(self) -> None:
        resp = _call(
            _RouterStub({"ok": False, "error": {"code": "timeout", "message": "slow"}}),
            "synth",
        )

        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "timeout")


if __name__ == "__main__":
    unittest.main()
