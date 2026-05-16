# ruff: noqa: E402

import asyncio
import importlib
import sys
import unittest
from pathlib import Path

from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    # NB: `import experiment_control.fastapi.app as ...` resolves to the FastAPI
    # *instance* re-exported by the package __init__ (which does
    # `from .app import app`, shadowing the submodule). Use importlib to grab
    # the actual submodule so we can reach module-level helpers like
    # `_is_transient_capabilities_failure` and `device_capabilities`.
    fastapi_app_module = importlib.import_module("experiment_control.fastapi.app")

    _FASTAPI_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment specific
    fastapi_app_module = None  # type: ignore[assignment]
    _FASTAPI_IMPORT_ERROR = exc


class _RouterStub:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, payload: dict, timeout_ms: int | None = None) -> dict:
        del timeout_ms
        self.calls.append(dict(payload))
        if self._responses:
            return self._responses.pop(0)
        return {"ok": False, "error": {"code": "no_stub_response"}}


def _request_stub() -> Request:
    return Request({"type": "http", "headers": []})


@unittest.skipIf(
    _FASTAPI_IMPORT_ERROR is not None,
    f"fastapi app import unavailable: {_FASTAPI_IMPORT_ERROR!r}",
)
class FastApiCapabilitiesRetryTests(unittest.TestCase):
    def test_device_capabilities_retries_once_on_transient_failure(self) -> None:
        router = _RouterStub(
            [
                {
                    "ok": False,
                    "error": {
                        "code": "device_rpc_timeout",
                        "message": "device RPC timed out after 1500 ms",
                        "transient": True,
                    },
                },
                {"ok": True, "result": {"version": 1, "members": [{"name": "get"}]}},
            ]
        )
        original_router = getattr(fastapi_app_module.app.state, "router", None)
        fastapi_app_module.app.state.router = router
        try:
            resp = asyncio.run(
                fastapi_app_module.device_capabilities("trace1", _request_stub())
            )
        finally:
            fastapi_app_module.app.state.router = original_router
        self.assertTrue(bool(resp.get("ok")))
        self.assertEqual(len(router.calls), 2)
        first_req_id = str(router.calls[0].get("request_id", ""))
        second_req_id = str(router.calls[1].get("request_id", ""))
        self.assertNotEqual(first_req_id, second_req_id)

    def test_device_capabilities_does_not_retry_non_transient_failure(self) -> None:
        router = _RouterStub(
            [
                {
                    "ok": False,
                    "error": {"code": "unknown_device", "message": "device is unknown"},
                }
            ]
        )
        original_router = getattr(fastapi_app_module.app.state, "router", None)
        fastapi_app_module.app.state.router = router
        try:
            resp = asyncio.run(
                fastapi_app_module.device_capabilities("trace1", _request_stub())
            )
        finally:
            fastapi_app_module.app.state.router = original_router
        self.assertFalse(bool(resp.get("ok")))
        self.assertEqual(len(router.calls), 1)

    def test_transient_capabilities_failure_detection_handles_legacy_error(self) -> None:
        self.assertTrue(
            fastapi_app_module._is_transient_capabilities_failure(
                {
                    "ok": False,
                    "error": {
                        "code": "error",
                        "message": "Resource temporarily unavailable",
                    },
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
