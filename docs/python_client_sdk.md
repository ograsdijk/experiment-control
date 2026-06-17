# Python Client SDK (direct ZMQ)

This SDK is for Python scripts that need to control a running stack directly over
the router RPC endpoint, without FastAPI.

## Import

```python
from experiment_control.client import StackClient
```

## Connect

```python
# From a stack YAML (resolves manager networking like run_stack, local connects):
with StackClient.from_stack_yaml("examples/dummy_stack/stack.yaml") as ec:
    print(ec.devices.list_status())

# Or from explicit endpoints:
with StackClient.from_endpoints(
    router_rpc="tcp://127.0.0.1:6100",
    manager_pub="tcp://127.0.0.1:6101",
) as ec:
    ...
```

## Namespaces

`ec` exposes one typed facade per subsystem. Every method takes optional
`timeout_ms=` / `retries=` and returns the decoded `result` (raising on failure -
see Error behavior).

| Namespace | Targets | Examples |
|-----------|---------|----------|
| `ec.devices` | device drivers | `list_status`, `call`, `get`, `set`, `capabilities`, `connect`, `disconnect`, `start`, `restart` |
| `ec.processes` | managed processes (generic) | `list_status`, `get_status`, `start`, `stop`, `restart`, `call`, `capabilities` |
| `ec.sequencer` | sequencer process | `load`, `start`, `pause`, `resume`, `stop`, `status`, `validate`, `preflight`, `library_list/reload/load` |
| `ec.hdf` | hdf_writer process | `status`, `rotate`, `writing_start`, `writing_stop`, `devices_get/enable/disable` |
| `ec.stream_analysis` | stream_analysis process | `status`, `operators`, `workspace_list/get/put/delete/reset/clear/snapshot/validate`, `workspace_store_status/save/reload` |
| `ec.influx` | influx_writer process | `status`, `enable`, `disable`, `flush`, `devices_get/enable/disable` |
| `ec.interlock` | interlock process | `list`, `status`, `load`, `enable/disable`, `enable_rule/disable_rule`, `enable_all/disable_all` |
| `ec.watchdog` | watchdog process | `status`, `enable/disable`, `enable_all/disable_all`, `clear_latch` |
| `ec.manager` | manager itself | `identity`, `telemetry_snapshot`, `log_tail`, `cleanup_orphans`, `shutdown`, `command_journal_status/tail` |
| `ec.wait` | polling helpers | `manager_ready`, `process_rpc_ready`, `process_state`, `sequencer_stopped`, `hdf_open` |

For any action without a typed wrapper, use the generic escape hatch:

```python
ec.processes.call("<process_id>", "<action>", {"param": 1})
ec.rpc({"type": "manager.processes.rpc", "process_id": "...", "request": {...}})
```

## Device commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    ec.devices.call("freq1", "set_frequency_hz", {"frequency_hz": 8.2e6})

    freq1 = ec.device("freq1")          # bound handle, reuses the transport
    print(freq1.capabilities())
```

## Process + sequencer commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    ec.processes.start("sequencer")
    ec.wait.process_rpc_ready("sequencer", probe_action="sequencer.status", timeout_s=10.0)

    ec.sequencer.load(path="examples/dummy_frequency_trace_sequencer/sequence_frequency_sweep.yaml")
    ec.sequencer.start()
    print(ec.sequencer.status())
```

## HDF commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    ec.processes.start("hdf_writer")
    ec.wait.process_rpc_ready("hdf_writer", probe_action="hdf.status")
    ec.hdf.rotate(filename="run_001.h5")
```

## Interlock commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    print(ec.interlock.list())
    ec.interlock.disable_rule("dummy_interlocks", "limit_temperature_setpoint")
    ec.interlock.enable_all()
    ec.interlock.load(path="rules/interlock_dummy.yaml", replace=True)
```

## Watchdog commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    print(ec.watchdog.status())
    ec.watchdog.clear_latch(all=True)
    ec.watchdog.disable("overtemp_guard")
```

## Influx writer commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    print(ec.influx.status())
    ec.influx.devices_disable(["trace1"])
    ec.influx.flush()
```

## Stream analysis commands

```python
with StackClient.from_stack_yaml("stack.yaml") as ec:
    print(ec.stream_analysis.workspace_list())
    snap = ec.stream_analysis.workspace_snapshot(workspace_id="workspace-1")
    ec.stream_analysis.workspace_reset(workspace_id="workspace-1")
```

## Events (PUB/SUB)

```python
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

Use the `call_raw(...)` variant on a facade (or `ec.rpc(payload, expect_ok=False)`)
to get the raw `{ok, result, error}` envelope instead of raising.
