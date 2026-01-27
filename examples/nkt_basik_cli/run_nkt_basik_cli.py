import subprocess
import sys
import time
from pathlib import Path

import zmq
from experiment_control.utils.zmq_helpers import json_dumps
from experiment_control.tui_manager import ManagerTUI


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

    app = ManagerTUI()
    try:
        app.run()
    finally:
        _shutdown_manager()
        if manager_proc.poll() is None:
            manager_proc.wait(timeout=5.0)
        if manager_proc.poll() is None:
            manager_proc.terminate()
            manager_proc.wait(timeout=3.0)

