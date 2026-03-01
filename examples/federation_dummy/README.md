# Federation Dummy Web UI Example

This example runs two local stacks on one machine:

- `leaf`: owns two real dummy devices, `dummy1` and `dummy2`
- `hub`: owns one local dummy device, `hub_local`, and mirrors only `leaf:dummy1` as `leaf.dummy1`

The hub can then serve the Web UI through FastAPI so you can verify that mirrored
devices appear alongside local devices.

## What You Should See

In the hub Web UI:

- `hub_local` should appear as a local device
- `leaf.dummy1` should appear as a mirrored device
- `dummy2` should not appear on the hub, because it is not mirrored

## Quick Start

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File examples/federation_dummy/start_federation_dummy_web.ps1
```

Then open:

`http://127.0.0.1:8010`

This starts:

- the leaf stack
- the hub stack
- the hub FastAPI gateway with the React UI

Stop it with `Ctrl+C`.

## Manual Start (Separate Terminals)

Leaf stack:

```powershell
powershell -ExecutionPolicy Bypass -File examples/federation_dummy/start_federation_dummy_leaf.ps1
```

Hub stack:

```powershell
powershell -ExecutionPolicy Bypass -File examples/federation_dummy/start_federation_dummy_hub.ps1
```

Hub Web UI:

```powershell
powershell -ExecutionPolicy Bypass -File examples/federation_dummy/start_federation_dummy_fastapi.ps1
```

Then open:

`http://127.0.0.1:8010`

## Optional Smoke Test

After the leaf and hub stacks are running:

```powershell
uv run python examples/federation_dummy/verify_federation.py
```

This checks:

- `leaf.dummy1` is visible from the hub
- `dummy2` is not visible from the hub
- mirrored `capabilities` works
- `set_temperature` forwards to the leaf
- mirrored telemetry updates on the hub

## Ports

Leaf:

- router RPC: `tcp://127.0.0.1:7300`
- manager PUB: `tcp://127.0.0.1:7301`

Hub:

- router RPC: `tcp://127.0.0.1:7600`
- manager PUB: `tcp://127.0.0.1:7601`

Web UI:

- FastAPI + React UI: `http://127.0.0.1:8010`
