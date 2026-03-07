from __future__ import annotations

import copy
import multiprocessing
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import zmq

from experiment_control.driver import StreamCall, StreamOut, TelemetryCall, TelemetryOut
from experiment_control.manager import DeviceSpec, Manager, ProcessSpec, RestartPolicy
from experiment_control.shm.shm_ring import ShmRingReader
from experiment_control.utils.zmq_helpers import json_dumps, json_loads

Json = dict[str, Any]


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

dummy_driver_path = (
    repo_root / "src" / "experiment_control" / "drivers" / "dummy_driver.py"
)
dummy_driver_name = "DummyDriver"

dummy_spec = DeviceSpec(
    "dummy1",
    dummy_driver_path,
    dummy_driver_name,
    {"port": 12345},
    telemetry_calls,
)

trace_driver_path = (
    repo_root / "src" / "experiment_control" / "drivers" / "dummy_trace_driver.py"
)
trace_driver_name = "DummyTraceDriver"
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


def run_manager(*, start_writer: bool) -> None:
    man = Manager(auto_connect_on_register=True)

    man.add_device(dummy_spec)
    man.add_device(copy.deepcopy(trace_spec))

    # Sequencer as a managed process.
    man.add_process(
        ProcessSpec(
            process_id="sequencer",
            argv=[
                sys.executable,
                "-m",
                "experiment_control.sequencer",
                "--manager-rpc",
                "tcp://127.0.0.1:6000",
                "--manager-pub",
                "tcp://127.0.0.1:6001",
                "--process-id",
                "sequencer",
                "--rpc-timeout-ms",
                "2000",
            ],
            restart_policy=RestartPolicy.NEVER,
            heartbeat_timeout_s=3.0,
            shutdown_timeout_s=3.0,
        )
    )

    if start_writer:
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
    man.run_forever()


def _rpc(sock: zmq.Socket, req: Json) -> Json:
    sock.send(json_dumps(req))
    raw = sock.recv()
    resp = json_loads(raw)
    if not isinstance(resp, dict):
        return {"ok": False, "error": "bad response"}
    return resp


def _wait_for_process_rpc_ready(
    sock: zmq.Socket,
    *,
    process_id: str,
    request: Json,
    timeout_s: float = 10.0,
) -> str:
    deadline = time.monotonic() + timeout_s
    last_state = None
    last_error = None
    while time.monotonic() < deadline:
        snap = _rpc(sock, {"type": "process.get", "process_id": process_id})
        snap_result = snap.get("result") if isinstance(snap, dict) else None
        if isinstance(snap_result, dict):
            last_state = snap_result.get("state")
            last_error = snap_result.get("last_error")

        probe = _rpc(
            sock,
            {
                "type": "process.rpc",
                "process_id": process_id,
                "request": request,
            },
        )
        if isinstance(probe, dict) and probe.get("ok", False):
            return "ready"

        err = probe.get("error") if isinstance(probe, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        if code in {"process_rpc_not_ready", "process_not_running"}:
            time.sleep(0.1)
            continue
        if code:
            raise RuntimeError(
                f"process.rpc probe failed: code={code!r} state={last_state!r} last_error={last_error!r}"
            )

        time.sleep(0.1)

    raise TimeoutError(
        f"process {process_id!r} did not become RPC-ready (state={last_state!r}, last_error={last_error!r})"
    )


def _wait_for_process_exit(
    sock: zmq.Socket, *, process_id: str, timeout_s: float = 5.0
) -> str:
    deadline = time.monotonic() + timeout_s
    last_state = None
    while time.monotonic() < deadline:
        snap = _rpc(sock, {"type": "process.get", "process_id": process_id})
        snap_result = snap.get("result") if isinstance(snap, dict) else None
        if isinstance(snap_result, dict):
            last_state = str(snap_result.get("state"))
            if last_state in {"STOPPED", "EXITED", "FAILED", "CRASHLOOP"}:
                return last_state
        time.sleep(0.1)
    return last_state or "unknown"


def main() -> None:
    seq_default = Path(__file__).with_name("sequence_temperature_and_trace.yaml")
    seq_path = Path(sys.argv[1]) if len(sys.argv) > 1 else seq_default

    start_writer = bool(int(sys.argv[2])) if len(sys.argv) > 2 else True

    manager_proc = multiprocessing.Process(
        target=run_manager, kwargs={"start_writer": start_writer}, daemon=True
    )
    manager_proc.start()

    time.sleep(1.0)

    ctx = zmq.Context.instance()

    # Manager RPC
    rpc_sock = ctx.socket(zmq.DEALER)
    rpc_sock.setsockopt(zmq.LINGER, 0)
    rpc_sock.setsockopt(zmq.RCVTIMEO, 2000)
    rpc_sock.connect("tcp://127.0.0.1:6000")

    # Subscribe to chunk_ready so we can read the stream payload back from SHM.
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.LINGER, 0)
    sub.setsockopt(zmq.RCVTIMEO, 200)
    sub.setsockopt(zmq.SUBSCRIBE, b"manager.chunk_ready")
    sub.connect("tcp://127.0.0.1:6001")

    try:
        if start_writer:
            _rpc(rpc_sock, {"type": "process.start", "process_id": "hdf_writer"})
            _wait_for_process_rpc_ready(
                rpc_sock,
                process_id="hdf_writer",
                request={"type": "hdf.status", "params": {}},
                timeout_s=10.0,
            )

        _rpc(rpc_sock, {"type": "process.start", "process_id": "sequencer"})
        _wait_for_process_rpc_ready(
            rpc_sock,
            process_id="sequencer",
            request={"type": "sequencer.status", "params": {}},
            timeout_s=10.0,
        )

        load_req = {
            "type": "process.rpc",
            "process_id": "sequencer",
            "request": {"type": "sequencer.load", "params": {"path": str(seq_path)}},
        }
        print(_rpc(rpc_sock, load_req))

        start_req = {
            "type": "process.rpc",
            "process_id": "sequencer",
            "request": {"type": "sequencer.start", "params": {}},
        }
        print(_rpc(rpc_sock, start_req))

        # Wait for chunk_ready events until the sequencer stops.
        got_stream = False
        reader: ShmRingReader | None = None

        t_start = time.monotonic()
        end_state: str | None = None
        while time.monotonic() - t_start < 300.0:
            # Poll sequencer status
            status_resp = _rpc(
                rpc_sock,
                {
                    "type": "process.rpc",
                    "process_id": "sequencer",
                    "request": {"type": "sequencer.status", "params": {}},
                },
            )
            result = (
                status_resp.get("result") if isinstance(status_resp, dict) else None
            )
            if isinstance(result, dict):
                state = str(result.get("state"))
                if state in {"STOPPED", "ERROR"}:
                    end_state = state
                    break

            # Try to receive a stream chunk descriptor
            try:
                topic_b, payload_b = sub.recv_multipart()
            except zmq.Again:
                continue

            if topic_b != b"manager.chunk_ready":
                continue

            msg = json_loads(payload_b)
            if not isinstance(msg, dict):
                continue

            device_id = str(msg.get("device_id"))
            stream = str(msg.get("stream"))
            shm_name = msg.get("shm_name")
            seq = msg.get("seq")
            if (
                device_id != "trace1"
                or stream != "trace"
                or not shm_name
                or seq is None
            ):
                continue

            # Read the exact seq payload from the shm ring
            reader = ShmRingReader.attach(str(shm_name))
            ev = reader.read_event(int(seq))
            if ev is None:
                print("chunk_ready received but event not found in shm")
                continue

            arr = np.frombuffer(ev["payload"], dtype=reader.layout.dtype).reshape(
                reader.layout.shape
            )
            print(
                f"Read stream chunk: device={device_id} stream={stream} seq={seq} "
                f"dtype={arr.dtype} shape={arr.shape} mean={float(arr.mean()):.3f}"
            )
            got_stream = True
            continue

        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass

        if end_state in {"STOPPED", "ERROR"}:
            time.sleep(5.0)

        if not got_stream:
            print("Did not receive a trace1.trace chunk before sequencer stopped")

    finally:
        # Best-effort cleanup
        try:
            _rpc(rpc_sock, {"type": "process.stop", "process_id": "sequencer"})
        except Exception:
            pass
        if start_writer:
            try:
                _rpc(rpc_sock, {"type": "process.stop", "process_id": "hdf_writer"})
            except Exception:
                pass
            _wait_for_process_exit(rpc_sock, process_id="hdf_writer", timeout_s=5.0)

        for dev in ("trace1", "dummy1"):
            try:
                _rpc(rpc_sock, {"type": "device.driver.stop", "device_id": dev})
            except Exception:
                pass

        try:
            _rpc(rpc_sock, {"type": "manager.shutdown"})
        except Exception:
            pass

        rpc_sock.close(0)
        sub.close(0)

        if manager_proc.is_alive():
            manager_proc.join(timeout=5.0)
        if manager_proc.is_alive():
            manager_proc.terminate()
            manager_proc.join(timeout=3.0)


if __name__ == "__main__":
    main()
