from __future__ import annotations

import sys
import time

import zmq

from experiment_control.manager_client import ManagerClient


def _require_ok(resp: dict | None, *, label: str) -> dict:
    if not isinstance(resp, dict):
        raise RuntimeError(f"{label}: no response")
    if not resp.get("ok"):
        raise RuntimeError(f"{label}: {resp.get('error', 'request failed')}")
    return resp


def main() -> int:
    ctx = zmq.Context.instance()
    client = ManagerClient(
        ctx=ctx,
        manager_rpc="tcp://127.0.0.1:7600",
        manager_pub="tcp://127.0.0.1:7601",
        rpc_timeout_ms=1500,
        subscribe_telemetry=True,
    )
    try:
        resp = _require_ok(
            client.call({"type": "device.list_status"}),
            label="device.list_status",
        )
        result = resp.get("result", [])
        if not isinstance(result, list):
            raise RuntimeError("device.list_status: invalid result payload")
        device_ids = {
            str(item.get("device_id", ""))
            for item in result
            if isinstance(item, dict)
        }
        if "leaf.dummy1" not in device_ids:
            raise RuntimeError("leaf.dummy1 was not visible from the hub")
        if "hub_local" not in device_ids:
            raise RuntimeError("hub_local was not visible from the hub")
        if "dummy2" in device_ids:
            raise RuntimeError("dummy2 should not be visible on the hub")
        print("[verify] device visibility OK")

        _require_ok(
            client.call(
                {
                    "type": "command",
                    "device_id": "leaf.dummy1",
                    "action": "capabilities",
                    "params": {},
                }
            ),
            label="leaf.dummy1.capabilities",
        )
        print("[verify] mirrored capabilities OK")

        target_temperature = 42.5
        _require_ok(
            client.call(
                {
                    "type": "command",
                    "device_id": "leaf.dummy1",
                    "action": "set_temperature",
                    "params": {"temperature": target_temperature},
                }
            ),
            label="leaf.dummy1.set_temperature",
        )
        print("[verify] mirrored command forwarding OK")

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            resp = _require_ok(
                client.call({"type": "get_telemetry", "device_id": "leaf.dummy1"}),
                label="get_telemetry",
            )
            telemetry = resp.get("telemetry", {})
            if isinstance(telemetry, dict):
                sample = telemetry.get("temperature")
                if isinstance(sample, dict):
                    value = sample.get("value")
                    if isinstance(value, (int, float)) and abs(value - target_temperature) < 3.0:
                        print("[verify] mirrored telemetry relay OK")
                        print("[verify] PASS")
                        return 0
            time.sleep(0.25)

        raise RuntimeError("timed out waiting for mirrored temperature telemetry")
    except Exception as exc:
        print(f"[verify] FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
