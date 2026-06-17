"""Verify stream chunk delivery against a running dummy_stream_cli stack.

Start the stack first (in another terminal):

    python -m experiment_control.cli.run_stack examples/dummy_stream_cli/stack.yaml

then run this script:

    python examples/dummy_stream_cli/verify_stream_chunks.py

It triggers `trace1.acquire_trace`, waits for the matching `manager.chunk_ready`
event, reads the payload back out of the shared-memory ring, and checks the
shape/dtype. Exits non-zero if no valid chunk arrives. This is the SHM-reader
demonstration that used to live in the imperative `dummy_sequencer` example.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import zmq

from experiment_control.shm.shm_ring import ShmRingReader
from experiment_control.utils.config_parsing import optional_dict, require_dict
from experiment_control.utils.manager_network import resolve_manager_network
from experiment_control.utils.yaml_helpers import load_yaml_file
from experiment_control.utils.zmq_helpers import json_dumps, json_loads

Json = dict[str, Any]

DEVICE_ID = "trace1"
STREAM = "trace"


def _manager_endpoints(stack_path: Path) -> tuple[str, str]:
    raw = load_yaml_file(stack_path)
    raw_obj = require_dict(raw, path=[])
    manager_raw = optional_dict(raw_obj.get("manager"), path=["manager"])
    net = resolve_manager_network(manager_raw)
    return net.local_rpc_connect, net.local_pub_connect


def _rpc(sock: zmq.Socket, req: Json) -> Json:
    sock.send(json_dumps(req))
    resp = json_loads(sock.recv())
    return resp if isinstance(resp, dict) else {"ok": False, "error": "bad response"}


def main() -> int:
    stack_path = Path(__file__).resolve().parent / "stack.yaml"
    manager_rpc, manager_pub = _manager_endpoints(stack_path)

    ctx = zmq.Context.instance()

    rpc_sock = ctx.socket(zmq.DEALER)
    rpc_sock.setsockopt(zmq.LINGER, 0)
    rpc_sock.setsockopt(zmq.RCVTIMEO, 2000)
    rpc_sock.connect(manager_rpc)

    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.LINGER, 0)
    sub.setsockopt(zmq.RCVTIMEO, 200)
    sub.setsockopt(zmq.SUBSCRIBE, b"manager.chunk_ready")
    sub.connect(manager_pub)

    # Give the SUB connection a moment to establish before triggering acquisition.
    time.sleep(0.3)

    reader: ShmRingReader | None = None
    try:
        _rpc(
            rpc_sock,
            {
                "type": "command",
                "device_id": DEVICE_ID,
                "action": "stream__acquire_trace",
                "params": {},
            },
        )

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                topic_b, payload_b = sub.recv_multipart()
            except zmq.Again:
                continue
            if topic_b != b"manager.chunk_ready":
                continue

            msg = json_loads(payload_b)
            if not isinstance(msg, dict):
                continue
            if (
                str(msg.get("device_id")) != DEVICE_ID
                or str(msg.get("stream")) != STREAM
                or not msg.get("shm_name")
                or msg.get("seq") is None
            ):
                continue

            reader = ShmRingReader.attach(str(msg["shm_name"]))
            ev = reader.read_event(int(msg["seq"]))
            if ev is None:
                print("chunk_ready received but event not found in shm")
                continue

            arr = np.frombuffer(ev["payload"], dtype=reader.layout.dtype).reshape(
                reader.layout.shape
            )
            print(
                f"OK: device={DEVICE_ID} stream={STREAM} seq={msg['seq']} "
                f"dtype={arr.dtype} shape={arr.shape} mean={float(arr.mean()):.3f}"
            )
            return 0

        print(f"FAIL: no {DEVICE_ID}.{STREAM} chunk within timeout", file=sys.stderr)
        return 1
    finally:
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass
        rpc_sock.close(0)
        sub.close(0)


if __name__ == "__main__":
    raise SystemExit(main())
