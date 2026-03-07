from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..driver import DeviceRunner
from ..schemas.run_meta import run_meta_calls_from_json
from ..schemas.stream import stream_calls_from_json
from ..schemas.telemetry import telemetry_calls_from_json
from ..utils.process_lifecycle import configure_child_parent_guard


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control DeviceRunner process")

    p.add_argument("--registry", required=True, help="Manager registry endpoint (REP)")
    p.add_argument("--device-id", required=True)

    p.add_argument(
        "--device-class-path",
        required=True,
        help="Path to .py file containing device class",
    )
    p.add_argument(
        "--device-class-name", required=True, help="Class name of the device class"
    )
    p.add_argument(
        "--device-init-json",
        required=True,
        help="JSON dict kwargs for device class constructor",
    )

    p.add_argument("--telemetry-period-s", type=float, default=1.0)
    p.add_argument("--heartbeat-period-s", type=float, default=1.0)
    p.add_argument("--command-poll-period-s", type=float, default=0.01)
    p.add_argument("--instance-id", default=None)
    p.add_argument("--parent-pid", type=int, default=None)

    p.add_argument(
        "--telemetry-calls-json",
        required=True,
        help="JSON list describing telemetry calls (method/kwargs/outputs)",
    )
    p.add_argument(
        "--telemetry-calls-file",
        required=False,
        help="Path to JSON file describing telemetry calls",
    )

    p.add_argument(
        "--stream-calls-json",
        required=False,
        help="JSON list describing stream calls (method/kwargs/outputs)",
    )
    p.add_argument(
        "--stream-calls-file",
        required=False,
        help="Path to JSON file describing stream calls",
    )

    p.add_argument(
        "--run-meta-calls-json",
        required=False,
        help="JSON list describing run metadata calls (method/kwargs/outputs)",
    )
    p.add_argument(
        "--run-meta-calls-file",
        required=False,
        help="Path to JSON file describing run metadata calls",
    )

    return p.parse_args(argv)


def _load_json_arg(json_arg: str | None, file_arg: str | None, *, name: str) -> Any:
    if file_arg:
        path = Path(file_arg).expanduser().resolve()
        raw_text = path.read_text(encoding="utf-8")
        return json.loads(raw_text)
    if json_arg is None:
        raise TypeError(f"{name} is required")
    return json.loads(json_arg)


def main(argv: list[str] | None = None) -> None:
    try:
        ns = _parse_args(sys.argv[1:] if argv is None else argv)
        configure_child_parent_guard(parent_pid=ns.parent_pid)
        if ns.instance_id:
            os.environ.setdefault(
                "EXPERIMENT_CONTROL_INSTANCE_ID", str(ns.instance_id).strip()
            )

        device_init_kwargs = json.loads(ns.device_init_json)
        if not isinstance(device_init_kwargs, dict):
            raise TypeError("--device-init-json must decode to a JSON object/dict")

        telemetry_calls_raw = _load_json_arg(
            ns.telemetry_calls_json,
            ns.telemetry_calls_file,
            name="telemetry calls",
        )
        telemetry_calls = telemetry_calls_from_json(telemetry_calls_raw)

        stream_calls_raw = (
            _load_json_arg(
                ns.stream_calls_json,
                ns.stream_calls_file,
                name="stream calls",
            )
            if (ns.stream_calls_json or ns.stream_calls_file)
            else None
        )
        stream_calls = stream_calls_from_json(stream_calls_raw)

        run_meta_calls_raw = (
            _load_json_arg(
                ns.run_meta_calls_json,
                ns.run_meta_calls_file,
                name="run meta calls",
            )
            if (ns.run_meta_calls_json or ns.run_meta_calls_file)
            else None
        )
        run_meta_calls = run_meta_calls_from_json(run_meta_calls_raw)

        driver = DeviceRunner(
            device_id=ns.device_id,
            device_class_path=str(Path(ns.device_class_path)),
            device_class_name=ns.device_class_name,
            device_init_kwargs=device_init_kwargs,
            registry_endpoint=ns.registry,
            telemetry_calls=telemetry_calls,
            stream_calls=stream_calls,
            run_meta_calls=run_meta_calls,
            telemetry_period_s=ns.telemetry_period_s,
            heartbeat_period_s=ns.heartbeat_period_s,
            command_poll_period_s=ns.command_poll_period_s,
        )
        driver.run()
    except Exception as e:
        msg = str(e)
        sys.stderr.write(f"[start_driver] error: {msg}\n")
        if os.environ.get("START_DRIVER_TRACEBACK") == "1":
            raise
        sys.exit(2)


if __name__ == "__main__":
    main()
