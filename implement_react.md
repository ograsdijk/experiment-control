# Web-Based DAQ GUI Architecture (Gateway + React UI)

This document describes the implementation of a web-based GUI for a Python DAQ system using:

* **ZeroMQ** for internal DAQ communication
* **FastAPI** as a gateway (ZMQ <-> web protocols)
* **React** for the browser UI
* **Mantine** for UI components + modals + notifications
* **uPlot** for fast live time-series plots
* **WebSockets** for live telemetry
* **HTTP** for device introspection and command execution

Scope: **slow telemetry only** (Hz-level scalar data such as pressure, temperature, status, setpoints).
High-rate waveform data and shared memory are intentionally **out of scope**.

---

## 1. Overall Architecture

```
DAQ / Acquisition & Control Code
  |- PUB: telemetry (slow scalar data)
  `- ROUTER / DEALER: command routing + introspection RPC
           |
           v
FastAPI Gateway (static IP on lab LAN)
  |- ZMQ SUB (telemetry)
  |- ZMQ REQ/DEALER (router external RPC)
  |- WebSocket: telemetry fanout
  |- HTTP API: devices, capabilities, call
  `- (optional) serves React static files
           |
           v
React UI (browser)
  |- one WebSocket connection
  |- device cards: latest values + pinned commands
  |- modals for command inputs/confirmation
  |- notifications (success/failure/connectivity)
  |- UI-owned ring buffers for plotted signals
  `- live updating time-series line plot (aka "strip chart") with optional multi-trace overlay
```

Key design principles:

* Browsers never speak ZMQ
* The gateway owns all ZMQ sockets
* Telemetry is **pushed**, not polled
* Plot state lives entirely in the UI (per user/session)
* Commands are executed through a single, safe command path

---

## 2. FastAPI Gateway

The gateway is a **single Python service** that bridges ZMQ to browser-friendly protocols.

### 2.1 Responsibilities

The gateway **does**:

* subscribe to telemetry via ZMQ SUB
* forward telemetry to browsers via WebSockets
* provide device discovery and capability introspection over HTTP
* accept command calls over HTTP and forward them to the DAQ command bus over ZMQ
* optionally keep *latest values* for a snapshot endpoint

The gateway **does not**:

* manage plots or per-user plot state
* maintain ring buffers for plots (v1)
* store historical data (v1)
* implement UI logic

---

### 2.2 Telemetry Path (ZMQ -> WebSocket)

* One ZMQ SUB socket subscribes to telemetry topics
* Messages are forwarded to all connected WebSocket clients
* Optional throttling/coalescing may be applied (recommended if telemetry can burst)

Telemetry messages are assumed to already include timestamps attached by the acquisition/control code.

Current manager PUB format (single device + multiple signals):

```json
{
  "device_id": "pump1",
  "signals": {
    "pressure": {
      "value": 1.3e-6,
      "units": "mbar",
      "quality": "OK",
      "ts": {"t_wall": 1736201345.123, "t_mono": 81234.56}
    }
  },
  "ts": {"t_wall": 1736201345.120, "t_mono": 81234.50}
}
```

The gateway forwards this shape as-is; the client flattens it into per-signal samples for cards and plots.

### 2.3 WebSocket API

#### Endpoint

```
GET /ws/telemetry
```

#### Behavior

* One persistent connection per browser tab
* Telemetry is pushed continuously
* No polling required
* One-to-many fanout

This is the **browser-side equivalent of SUB**.

---

### 2.4 Commands: HTTP -> ZMQ (safe serialization)

ZMQ `REQ` sockets are strict (send/recv alternation) and not re-entrant.
Therefore the gateway must ensure command calls are safe under concurrency.

The gateway connects to the router external RPC endpoint for all commands and introspection.

Implementation pattern:

* A single command worker owns the ZMQ REQ socket
* HTTP requests enqueue work to an internal async queue
* Worker executes command -> waits for reply -> returns result
* Timeouts are enforced at the gateway level
* Multiple client requests are serialized through the queue (v1)

### 2.5 Device Discovery and Introspection

The gateway maps HTTP endpoints directly to the existing control RPC types
via the router external RPC socket.

Suggested endpoints:

#### List devices

```
GET /api/devices
```

RPC mapping:

```
{"type": "device.list_status"}
```

Returns device IDs and basic metadata.

#### Device capabilities (introspection)

```
GET /api/devices/{device_id}/capabilities
```

RPC mapping:

```
{"type": "command", "device_id": "...", "action": "capabilities", "params": {}}
```

Returns a list of callable functions/methods, and if available:

* parameter names
* types
* defaults
* docstrings / help text

If only method names are available, the UI can still provide an advanced "call" dialog with minimal typing support.

### 2.6 Execute a command call

#### Endpoint

```
POST /api/devices/{device_id}/call
```

Payload example:

```json
{
  "action": "set_current",
  "params": {"current": 2.5}
}
```

RPC mapping:

```json
{
  "type": "command",
  "device_id": "...",
  "action": "set_current",
  "params": {"current": 2.5}
}
```

Response example:

```json
{
  "ok": true,
  "result": null,
  "ts": 1736201345.456
}
```

Recommended error shape:

```json
{
  "ok": false,
  "error": {
    "code": "timeout|unknown_device|interceptor_rejected|device_error",
    "message": "human readable message",
    "details": {}
  }
}
```

Notes:

* Enforce a timeout (e.g. 1-5 s depending on device class)
* Return structured errors for timeouts and device failures

### 2.7 Optional Snapshot Endpoint

To populate the UI immediately on load (instead of waiting for first telemetry packets):

```
GET /api/state
```

Returns latest known values per device (no history).
This is optional but improves UX.

---

### 2.8 Hosting on the Lab LAN

* Gateway runs on a **static IP**
* Bind to `0.0.0.0`
* Users access via:

  ```
  http://daq-gateway:8000/
  ```

Serving UI + API from the same origin avoids CORS issues.

---

## 3. React UI (Mantine + uPlot)

The React UI is a browser application that renders:

* device cards for monitoring/control
* live updating time-series line plots
* modals for command confirmation/inputs
* notifications for non-blocking feedback

### 3.1 Responsibilities

The React UI:

* opens **one WebSocket** to receive live telemetry
* maintains `latestByDevice` state for device cards
* maintains ring buffers for plotted signals
* renders live updating time-series plots (rolling window)
* calls HTTP endpoints for:

  * device list
  * device capabilities
  * command execution

---

## 4. UI Layout

```
+----------------------------------------------------------+
| Top Bar: connection status, DAQ state (optional), profile |
+------------------------+---------------------------------+
| Device Cards (LHS)     | Plot Workspace (RHS)            |
|                        |                                 |
| - Card per device      | - One live updating time-series |
|   - key telemetry      |   plot by default               |
|   - pinned commands    | - Optional multi-trace overlay  |
|   - advanced commands  | - Trace picker                  |
|                        | - Time window controls          |
+------------------------+---------------------------------+
```

### 4.1 Top Bar

* Gateway connection indicator (WS connected / disconnected)
* Optional DAQ run state (if available)
* Optional profile selector (future)

### 4.2 Device Cards (Left)

Each card shows:

* a small set of key telemetry fields (initially configurable via local "favorites")
* pinned command buttons
* an "Advanced" section to call any method (from introspection)
* per-field "plot" action to add a signal to the active plot

---

## 5. Command UX (Modals + Notifications)

### 5.1 Modal-first command interaction

Default rule:

* **All commands open a modal**
* Modal can be:

  * confirmation-only (no inputs)
  * input form (one or more inputs)

This avoids accidental execution and keeps device cards compact.

Pinned commands:

* appear as buttons on device cards
* open a modal
* execute on confirm/submit

Advanced commands:

* selected from introspection list
* open a modal
* render an auto-generated form if parameter metadata exists
* otherwise fall back to an argument entry UI (e.g. JSON/array entry)

### 5.2 Notifications (Mantine Notifications)

Use notifications for:

* command success/failure
* timeout warnings
* gateway connection lost/restored
* non-blocking informational messages

Persisted fault states should be shown on device cards, not as repeated toasts.

---

## 6. Live Updating Time-Series Line Plot (Rolling Window)

The default plot is a live updating time-series line plot with a **rolling time window** (commonly called a "strip chart" in control-room terminology). It is just a standard line plot with "live" semantics.

### 6.1 Plot model

A plot contains one or more traces:

```ts
Plot {
  id
  title
  traces: Trace[]
  timeWindowS
  autoscale
}

Trace {
  device
  signal
}
```

Default behavior:

* one plot panel
* one trace
* optional multi-trace overlay in the same plot

### 6.2 UI-owned ring buffers

Ring buffers live in the browser:

* one buffer per `(device, signal)` that is actively plotted
* created when trace added
* destroyed when trace removed
* fixed length based on `timeWindowS`

The ring buffer stores:

* `t_wall` (preferred for cross-device overlay)
* `value`

### 6.3 Telemetry ingestion

On each telemetry message:

1. update `latestByDevice`
2. if the signal is plotted, append to the corresponding ring buffer
3. redraw plot at a fixed cadence (e.g. 10-30 Hz) regardless of telemetry rate

This decouples telemetry arrival from rendering cost.

### 6.4 Time axis handling

Default: use acquisition-attached **wall time** (`t_wall`)
Optional: display as "seconds ago" (UI transform)

Because timestamps are aligned (mostly same host), multi-device overlays are straightforward.

### 6.5 Plot rendering (uPlot)

* uPlot is used for performance and smooth updates
* The plot is updated by calling `setData()` with arrays derived from ring buffers

---

## 7. Local Persistence and Default Profiles

### 7.1 Local persistence (per user)

The UI stores user preferences in browser storage (localStorage), such as:

* which telemetry fields are pinned to each device card
* which commands are pinned
* plot traces and time window

This persists across refresh and browser restarts, per origin.

### 7.2 YAML default profiles (backup / shared defaults)

As a future extension, the gateway can host YAML "profiles" that define default UI layouts.

Suggested endpoints:

* `GET /api/profiles` (list profile names)
* `GET /api/profiles/{name}` (profile content as JSON)

Profiles define defaults such as:

* device ordering
* which telemetry keys to display on cards
* which commands are pinned
* default plot traces and time windows

Local user changes can override these defaults.

---

## 8. Minimal Endpoint Set (v1)

Required:

* `WS /ws/telemetry`
* `GET /api/devices`
* `GET /api/devices/{device_id}/capabilities`
* `POST /api/devices/{device_id}/call`

Optional (recommended):

* `GET /api/state`
* `GET /health`

Optional later:

* `GET /api/profiles`
* `GET /api/profiles/{name}`

---

## 9. Summary

**Gateway (FastAPI)**

* ZMQ SUB telemetry -> WS fanout (client flattens per-signal)
* ZMQ REQ/DEALER (router external RPC) command/introspection -> HTTP API
* safe command serialization (single REQ owner + queue)
* minimal state (optional latest snapshot)

**React UI (Mantine + uPlot)**

* one WS telemetry connection
* device cards with telemetry + pinned commands
* modal-first command execution
* notifications for feedback
* UI-owned ring buffers for live updating time-series line plots
* optional multi-trace overlay

This provides a clean, extensible operator GUI without entangling DAQ logic, ZMQ semantics, and UI behavior.

## 10. React + Mantine UI Skeleton (v1)

This is a minimal layout and data-flow sketch aligned with Mantine + uPlot.

### 10.1 Layout

- AppShell
  - AppShell.Navbar (left): device cards list
    - ScrollArea -> Stack -> DeviceCard
  - AppShell.Main (right): plot workspace
    - Stack
      - PlotToolbar (time window, autoscale, trace picker)
      - PlotPanel (uPlot)
      - Optional TraceLegend
  - AppShell.Header: connection status + gateway info + selected device
  - Notifications provider at root

### 10.2 Client State Model

- devices: list from GET /api/devices
- capabilitiesByDevice: map from GET /api/devices/{id}/capabilities
- latestByDevice: { [deviceId]: { [signal]: { value, units, quality, ts } } }
- plotBuffers: { [deviceId]: { [signal]: RingBuffer } }
- selectedTraces: [{ deviceId, signal }]
- uiPrefs: pinned signals/commands, time window, autoscale

### 10.3 Telemetry Ingestion (WS)

On each WS message:

1) update latestByDevice[deviceId][signal]
2) if (deviceId, signal) is plotted, append to ring buffer
3) redraw plot on a fixed timer (10-30 Hz), not per message

### 10.4 Commands (Modals + Notifications)

- DeviceCard buttons open a Modal
- Modal submit -> POST /api/devices/{id}/call
- Show success/failure via Notifications

### 10.5 Suggested Mantine Components

- Card, Group, Badge, Text, Button, ActionIcon
- AppShell, Stack, Grid, ScrollArea
- Modal, TextInput, NumberInput, Select
- Notifications / showNotification
