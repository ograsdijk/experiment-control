import subprocess
import sys
import threading
import time
from pathlib import Path

import zmq
from experiment_control.utils.zmq_helpers import json_dumps
from experiment_control._tui.app import ManagerTUI


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


def _shutdown_manager() -> None:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, 1000)
    sock.connect("tcp://127.0.0.1:6000")
    try:
        sock.send(json_dumps({"type": "manager.shutdown"}))
        sock.recv()
    except Exception:
        pass
    sock.close(0)


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[2]
    stack_path = Path(__file__).resolve().parent / "stack.yaml"

    manager_proc = subprocess.Popen(
        [sys.executable, "-m", "experiment_control.cli.run_stack", str(stack_path)],
        cwd=repo_root,
    )

    time.sleep(1.0)

    stop_event = threading.Event()
    stream_thread = threading.Thread(
        target=_stream_acquire_loop, args=(stop_event,), daemon=True
    )
    stream_thread.start()

    app = ManagerTUI()
    try:
        app.run()
    finally:
        stop_event.set()
        stream_thread.join(timeout=2.0)
        _shutdown_manager()
        if manager_proc.poll() is None:
            manager_proc.wait(timeout=5.0)
        if manager_proc.poll() is None:
            manager_proc.terminate()
            manager_proc.wait(timeout=3.0)

