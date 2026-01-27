# Manager Federation (Multi-Instance Device Access)

## Summary

This document defines a v1 federation design for running multiple manager instances on different hosts while exposing selected remote devices as if they were local on a "hub" instance.

Primary goal:
- Let existing clients (web UI, sequencer, process RPC users) interact with selected remote devices through one local manager/router endpoint.

Key v1 constraints:
- Keep native ZMQ RPC + PUB/SUB architecture.
- No stream/chunk forwarding in v1 (shared-memory descriptors are host-local).
- Do not re-export federated devices to other peers (no daisy-chain).
- Use existing manager PUB topic set; filter on receiving side.

---

## Goals

- Expose remote devices on the hub instance as mirrored local device IDs.
- Forward device commands and capability calls to remote owner instance.
- Relay remote telemetry/heartbeat/log/command-interceptor context for mirrored devices.
- Preserve existing protocol and UI/process behavior as much as possible.
- Enforce safe command policies for remote operations (deny dangerous actions by default).

## Non-Goals (v1)

- No `manager.chunk_ready` / stream data forwarding.
- No cross-host shared-memory transport.
- No automatic federation mesh routing.
- No per-peer bandwidth-optimized telemetry exporter in v1.

---

## Terms

- Leaf manager: remote instance that owns physical devices.
- Hub manager: local instance that mirrors remote devices and serves local clients.
- Mirrored device: a hub-side alias mapped to a leaf-side `device_id`.
- Owner peer: the leaf manager responsible for a mirrored device.

---

## High-Level Architecture

For each configured peer, hub manager creates:

- RPC client to peer router RPC endpoint (DEALER -> remote ROUTER).
- SUB connection to peer manager PUB endpoint.

Hub manager keeps a mapping:
- `local_device_id -> (peer_id, remote_device_id)`

Hub router/manager behavior:
- Local device commands for mirrored devices are forwarded to owner peer.
- Local device status for mirrored devices is synthesized from relayed remote events.
- Remote events are rewritten to local mirrored `device_id` when surfaced locally.

---

## Device Identity and Mapping

Mirrored IDs must be explicit and collision-free.

Recommended naming:
- `peer_alias.device_id` (example: `lab2.psu`).

Required behavior:
- A mirrored ID cannot overlap a locally owned device ID.
- One mirrored ID maps to exactly one owner peer and one remote device ID.
- Mapping is static from config in v1.

---

## Command Forwarding Rules

When hub receives `type: command`:

1. If `device_id` is local: current behavior (local handling).
2. If `device_id` is mirrored:
   - Resolve `(peer_id, remote_device_id)`.
   - Enforce outbound policy/ACL.
   - Forward to owner peer as normal `type: command` with remote `device_id`.
   - Return peer response unchanged except optional metadata wrapping.

Mirrored command behavior must preserve:
- Interlock errors (`INTERCEPTOR_*`) from remote.
- Interceptor modifications (if reflected in error/details/event payloads).

---

## Remote Command Policy (ACL)

Federation must support per-peer command restrictions.

Default-safe posture:
- Allow normal device actions only if explicitly permitted.
- Deny lifecycle/admin operations by default.

Policy categories:
- Device actions (`type: command`, action-level allow/deny).
- Device lifecycle (`device.connect`, `device.disconnect`, `device.driver.start/stop/restart`).
- Manager/process admin (`manager.shutdown`, `process.*`, other management RPC).

Recommended policy evaluation order:
1. Explicit deny list.
2. Explicit allow list.
3. Category default.
4. Global default deny for unmatched operations.

Both sides should enforce policy:
- Hub outbound policy (before forwarding).
- Leaf inbound federation policy (defense in depth).

---

## Interlock, Follower, and Watchdog Semantics

Authority model:
- Leaf manager/interceptors are authoritative for leaf-owned devices.

Implications:
- If remote interlock blocks command, hub returns same failure (with code/details).
- If remote rule transforms params, command executes with transformed params on leaf.
- If remote follower/watchdog sends follow-up actions, those remain leaf-owned actions.

Observability at hub:
- Hub relays remote event/log topics for mapped devices so operators can see:
  - `manager.command`
  - `manager.log`
  - `manager.command_interceptor.error`
  - `manager.command_interceptor.modified`

---

## Event Relay and Local Re-Publish

Hub subscribes to remote manager PUB and relays selected topics.

For mirrored devices:
- Rewrite remote `device_id` to local mirrored ID in relayed payloads.
- Preserve original timing fields when present.
- Optionally include origin metadata (`peer_id`, `remote_device_id`) for diagnostics.

Core topics to relay in v1:
- `manager.telemetry_update`
- `manager.heartbeat`
- `manager.log`
- `manager.command`
- `manager.command_interceptor.error`
- `manager.command_interceptor.modified`

---

## Telemetry Filtering in v1

v1 uses existing manager PUB topic format (for example `manager.telemetry_update`).

Consequence:
- SUB filtering by `device_id` cannot reduce network traffic at transport level.
- Hub receives all subscribed remote telemetry topic traffic, then filters locally.

Still useful:
- Config can limit which mirrored devices are accepted and re-published locally.
- This reduces local processing and UI noise, but not wire bandwidth.

Future (optional):
- Add federation-specific filtered exporter topics (device-scoped prefixes) for network reduction.

---

## Loop Prevention and Non-Redirection

Hard rule for v1:
- Federated (mirrored) devices are never re-exported to another peer.

Enforcement:
- Mark mirrored devices with `source_kind=federated` and `owner_peer_id`.
- Exportable device list for federation includes local-owned devices only.
- Forwarding logic rejects forwarding for devices whose `source_kind` is federated.

Additional guard:
- Include federation metadata (`origin_manager_id`, `hop_count`) in forwarded context.
- Reject if hop count is non-zero for outbound federation operations.

---

## Status and Failure Handling

Peer health should be tracked and exposed:
- RPC connectivity status.
- Last event receive timestamp.
- Last error.

Mirrored device behavior on peer outage:
- Commands fail closed with explicit federation/peer-unavailable errors.
- Device status becomes offline/unavailable with reason.
- No automatic fallback to other peers.

Timeouts/retries:
- Per-peer RPC timeout and retry/backoff config.

---

## Config Sketch (Draft)

```yaml
federation:
  enabled: true
  manager_id: lab1
  peers:
    - peer_id: lab2
      router_rpc: tcp://10.0.0.22:6000
      manager_pub: tcp://10.0.0.22:6001
      allow_reexport: false
      mirror_devices:
        - local_id: lab2.psu
          remote_device_id: psu
        - local_id: lab2.hv
          remote_device_id: hv
      policy:
        allow_device_actions: ["set_*", "get", "capabilities"]
        deny_device_actions: ["shutdown", "disconnect", "restart*"]
        allow_lifecycle_ops: false
        allow_admin_ops: false
      relay:
        topics:
          - manager.telemetry_update
          - manager.heartbeat
          - manager.log
          - manager.command
          - manager.command_interceptor.error
          - manager.command_interceptor.modified
        only_mirrored_devices: true
```

Notes:
- Field names are draft and can be adjusted to match existing config schema style.
- Action wildcard matching rules should be documented precisely in implementation.

---

## Compatibility Notes

- Existing UI and sequencer should work with mirrored devices once they appear in `device.list_status` and `capabilities`.
- No stream-capable workflows should target mirrored devices in v1.
- HDF writer behavior for mirrored telemetry/log events should be validated explicitly.

---

## Phased Implementation Plan

1. Federation config schema + validation.
2. Peer link manager (RPC + PUB subscriber) in hub manager.
3. Mirrored device registry and status synthesis.
4. Command forwarding path with ACL enforcement.
5. Event relay/rewrite for mapped devices.
6. Non-redirection and hop guards.
7. Tests + protocol/startup docs updates.

---

## Test Matrix (Minimum)

- Command forwarding success for mirrored device.
- Blocked command by local ACL.
- Blocked command by remote interlock; error propagated.
- Remote interceptor transform reflected in command event/log context.
- Peer outage -> mirrored command fails closed.
- Mirrored devices not exported to downstream peers.
- Relay filter only publishes mapped mirrored devices locally.
- No `manager.chunk_ready` forwarding for mirrored devices.

