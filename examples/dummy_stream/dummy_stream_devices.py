import copy
import multiprocessing
import sys
import threading
import time
from pathlib import Path

import zmq
from experiment_control.utils.zmq_helpers import json_dumps
from experiment_control.driver import StreamCall, StreamOut, TelemetryCall, TelemetryOut
from experiment_control.manager import DeviceSpec, Manager, ProcessSpec, RestartPolicy

telemetry_calls = [
    TelemetryCall(
        method="read_temperature",
        outputs=[
            TelemetryOut(
                signal="temperature",
                kind="scalar",
                units="C",
            ),
        ],
    ),
    TelemetryCall(
        method="read_voltage",
        outputs=[
            TelemetryOut(
                signal="voltage",
                kind="scalar",
                units="V",
            ),
        ],
    ),
]

stream_calls = [
    StreamCall(
        method="acquire_trace",
        outputs=[
            StreamOut(
                stream="trace",
                dtype="float64",
                shape=(5, 10_000),
                units="counts",
                ring_slots=256,
            )
        ],
    )
]

repo_root = Path(__file__).resolve().parents[2]

telemetry_driver_path = (
    repo_root / "src" / "experiment_control" / "drivers" / "dummy_driver.py"
)
telemetry_driver_name = "DummyDriver"
telemetry_spec = DeviceSpec(
    "dummy1",
    telemetry_driver_path,
    telemetry_driver_name,
    {"port": 12345},
    telemetry_calls,
)

trace_driver_path = (
    repo_root / "src" / "experiment_control" / "drivers" / "dummy_trace_driver.py"
)
trace_driver_name = "DummyTracedDriver"
trace_spec = DeviceSpec(
    "trace1",
    trace_driver_path,
    trace_driver_name,
    {"port": 23456},
    telemetry_calls=[],
    stream_calls=stream_calls,
    stream_metadata={
        "trace": {
            "channel_descriptions": ["PMT", "PD", "E", "B", "spare"],
            "channel_units": ["counts", "counts", "counts", "counts", "counts"],
        }
    },
)


def run_manager() -> None:
    man = Manager(auto_connect_on_register=True)

    # Add devices
    man.add_device(telemetry_spec)
    man.add_device(copy.deepcopy(trace_spec))

    # Attach telemetry writer as a MANAGED PROCESS
    man.add_process(
        ProcessSpec(
            process_id="hdf_writer",
            argv=[
                sys.executable,
                "-m",
                "experiment_control.processes.hdf_writer",
                "--out-dir",
                "data",
                "--manager-rpc",
                "tcp://127.0.0.1:6000",
                "--manager-pub",
                "tcp://127.0.0.1:6001",
                "--timezone",
                "America/Chicago",
                "--rcvhwm",
                "10000",
                "--write-every-s",
                "1.0",
                "--buffer-max-messages",
                "200000",
                "--flush-every-n",
                "200",
                "--flush-every-s",
                "2.0",
            ],
            restart_policy=RestartPolicy.NEVER,
            heartbeat_timeout_s=3.0,
            shutdown_timeout_s=3.0,
        )
    )

    man.startup_sequence(timeout_s=15.0, start_processes=False)
    man.start_process("hdf_writer")
    man.run_forever()


def _stream_acquire_loop(stop_event: threading.Event, period_s: float = 1.0) -> None:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, 1000)
    sock.connect("tcp://127.0.0.1:6000")

    while not stop_event.is_set():
        req = {
            "type": "command",
            "device_id": "trace1",
            "action": "stream__acquire_trace",
            "params": {},
        }
        try:
            sock.send(json_dumps(req))
            sock.recv()
        except Exception:
            pass
        stop_event.wait(period_s)

    sock.close(0)


def stop_all_managed() -> None:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, 1000)
    sock.connect("tcp://127.0.0.1:6000")

    def rpc(req: dict) -> None:
        try:
            sock.send(json_dumps(req))
            sock.recv()
        except Exception:
            pass

    rpc({"type": "device.driver.stop", "device_id": "trace1"})
    rpc({"type": "device.driver.stop", "device_id": "dummy1"})
    rpc({"type": "process.stop", "process_id": "hdf_writer"})
    rpc({"type": "manager.shutdown"})
    sock.close(0)


if __name__ == "__main__":
    manager_proc = multiprocessing.Process(target=run_manager, daemon=True)
    manager_proc.start()

    time.sleep(1.0)

    stop_event = threading.Event()
    stream_thread = threading.Thread(
        target=_stream_acquire_loop, args=(stop_event,), daemon=True
    )
    stream_thread.start()

    from experiment_control.tui_manager import ManagerTUI

    app = ManagerTUI()
    try:
        app.run()
    finally:
        stop_event.set()
        stream_thread.join(timeout=2.0)
        stop_all_managed()
        if manager_proc.is_alive():
            manager_proc.join(timeout=5.0)
        if manager_proc.is_alive():
            manager_proc.terminate()
            manager_proc.join(timeout=3.0)


