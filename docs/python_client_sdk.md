# Python Client SDK (direct ZMQ)

This SDK is for Python scripts that need to control a running stack directly over
the router RPC endpoint, without FastAPI.

## Import

```python
from experiment_control.client import StackClient
```

## Connect from stack YAML

```python
from experiment_control.client import StackClient

with StackClient.from_stack_yaml("examples/dummy_frequency_trace_sequencer/stack.yaml") as ec:
    devices = ec.devices.list_status()
    print(devices)
```

`from_stack_yaml(...)` resolves manager networking through the same logic as
`run_stack` and uses local connect endpoints (`tcp://127.0.0.1:<port>`).

## Device commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    ec.devices.call("freq1", "set_frequency_hz", {"frequency_hz": 8.2e6})

    freq1 = ec.device("freq1")
    caps = freq1.capabilities()
    print(caps)
```

`ec.device("...")` returns a bound handle that reuses the same transport socket.

## Process + sequencer commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    ec.processes.start("sequencer")
    ec.wait.process_rpc_ready("sequencer", probe_action="sequencer.status", timeout_s=10.0)

    ec.sequencer.load(path="examples/dummy_frequency_trace_sequencer/sequence_frequency_sweep.yaml")
    ec.sequencer.start()
    status = ec.sequencer.status()
    print(status)
```

## HDF commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    ec.processes.start("hdf_writer")
    ec.wait.process_rpc_ready("hdf_writer", probe_action="hdf.status")
    ec.hdf.rotate(filename="run_001.h5")
```

## Events (PUB/SUB)

```python
from experiment_control.client import StackClient

with StackClient.from_stack_yaml("stack.yaml") as ec:
    with ec.subscribe(["manager.telemetry_update", "manager.log"]) as sub:
        for msg in sub.iter(timeout_ms=100):
            print(msg.topic, msg.payload)
```

## Error behavior

High-level methods raise exceptions on command failures:

- `RpcTimeoutError`: no response before timeout.
- `RpcTransportError`: socket-level transport issue.
- `RpcResponseError`: stack returned `{ok:false}` or `status=ERROR`.
- `ProcessRpcNotReadyError`: process RPC endpoint not ready yet.

