import copy
import multiprocessing
import sys
import time
from pathlib import Path

from experiment_control.driver import TelemetryCall, TelemetryOut
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

repo_root = Path(__file__).resolve().parents[2]
device_class_path = (
    repo_root / "src" / "experiment_control" / "drivers" / "dummy_driver.py"
)
device_class_name = "DummyDriver"
device_spec = DeviceSpec(
    "dummy1", device_class_path, device_class_name, {"port": 12345}, telemetry_calls
)


def run_manager() -> None:
    man = Manager(auto_connect_on_register=True)

    # Add devices
    man.add_device(device_spec)
    device_spec2 = copy.deepcopy(device_spec)
    device_spec2.device_id = "dummy2"
    man.add_device(device_spec2)

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
                # "--filename", "my_run.h5",  # optional
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

    # IMPORTANT: do NOT start processes by default
    man.startup_sequence(timeout_s=15.0, start_processes=False)
    man.start_process("hdf_writer")
    man.run_forever()


def stop_all_managed() -> None:
    import zmq
    from experiment_control.utils.zmq_helpers import json_dumps

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

    # Stop managed processes (telemetry writer) and all drivers
    rpc({"type": "process.stop", "process_id": "hdf_writer"})
    rpc({"type": "device.list_status"})
    rpc({"type": "device.driver.stop", "device_id": "dummy1"})
    rpc({"type": "device.driver.stop", "device_id": "dummy2"})
    rpc({"type": "manager.shutdown"})
    sock.close(0)


if __name__ == "__main__":
    manager_proc = multiprocessing.Process(target=run_manager, daemon=True)
    manager_proc.start()

    time.sleep(1.0)

    from experiment_control.tui_manager import ManagerTUI

    app = ManagerTUI()
    try:
        app.run()
    finally:
        stop_all_managed()
        if manager_proc.is_alive():
            manager_proc.terminate()
            manager_proc.join(timeout=3.0)
    # while True:
    #     try:
    #         time.sleep(1.0)
    #     except KeyboardInterrupt:
    #         print("Stopping all managed processes and drivers...")
    #         stop_all_managed()
    #         if manager_proc.is_alive():
    #             manager_proc.terminate()
    #             manager_proc.join(timeout=3.0)
    #         break


