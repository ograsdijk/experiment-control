# Manager Federation (Multi-Instance Device Access)

## Summary

This document defines a v1 federation design for running multiple manager instances on different hosts while exposing selected remote devices as if they were local on a "hub" instance.

Primary goal:
- Let existing clients (web UI, sequencer, process RPC users) interact with selected remote devices through one local manager/router endpoint.

Key v1 constraints:
- Keep native ZMQ RPC + PUB/SUB architecture.
- Preserve the existing local split of responsibilities:
  - `device_router` remains the external command ingress for device RPC.
  - `manager` remains the authority for device inventory, status, telemetry caches, and config/schema surfaces.
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

- A peer metadata client to the remote router/manager RPC endpoints (for initial config/schema fetches and lifecycle/admin requests that are allowed by policy).
- A dedicated SUB connection to that peer's manager PUB endpoint.

For each mirrored device, hub router creates:

- A dedicated command-forwarding lane (worker + outbound socket) for that mirrored device.
- Forwarding stays serial per mirrored device, matching the current local "one command at a time per device" behavior.

Hub manager keeps a mapping:
- `local_device_id -> (peer_id, remote_device_id)`

Hub router/manager behavior:
- `device_router` detects mirrored `device_id`s for external `type: command` requests.
- `device_router` forwards mirrored device commands through the dedicated mirrored-device lane to the owner peer's router RPC endpoint.
- `manager` owns mirrored device inventory, status synthesis, telemetry caches, and config/schema surfaces.
- Remote events are rewritten to local mirrored `device_id` when surfaced locally.

Implementation note:
- Do not use one shared blocking outbound lane for all mirrored devices on a peer. A slow remote device would otherwise stall unrelated mirrored devices.

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
   - `device_router` resolves `(peer_id, remote_device_id)`.
   - `device_router` uses the mirrored device's dedicated forwarding lane.
   - Enforce outbound federation policy/ACL before forwarding.
   - Forward to owner peer as normal `type: command` with remote `device_id`.
   - Include federation context metadata (`origin_instance_id`, `hop_count`).
   - Return peer response unchanged except optional metadata wrapping.

Mirrored command behavior must preserve:
- Interlock errors (`INTERCEPTOR_*`) from remote.
- Interceptor modifications (if reflected in error/details/event payloads).

Lifecycle/admin RPCs:
- Requests such as `device.connect`, `device.disconnect`, `device.driver.start/stop/restart`, and `device.recover` are handled by hub manager for mirrored devices.
- These are denied by default and only forwarded to the owner peer when federation policy explicitly allows them.

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

Important distinction:
- Hub federation policy is coarse routing policy ("may this remote operation be attempted?").
- Normal device interlocks remain leaf-authoritative and should not be re-applied on the hub for mirrored devices.

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

Implementation note:
- For mirrored devices, the hub should not run its normal local device command-interceptor chain and then let the leaf run the leaf chain again.
- Double-applying interlocks would duplicate rewrites/rejections and create non-local behavior.

---

## Event Relay and Local Re-Publish

Hub subscribes to remote manager PUB and relays selected topics.

For mirrored devices:
- Rewrite remote `device_id` to local mirrored ID in relayed payloads.
- Preserve original timing fields when present.
- Include origin metadata (`peer_id`, `remote_device_id`) for diagnostics.

Core topics to relay in v1:
- `manager.telemetry_update`
- `manager.heartbeat`
- `manager.log`
- `manager.command`
- `manager.command_interceptor.error`
- `manager.command_interceptor.modified`

Implementation note:
- Use one SUB socket per peer, not one SUB socket connected to multiple peers.
- The relay path must always know which peer a message came from for correct metadata, health tracking, and diagnostics.
- Payload rewriting must handle nested command/interceptor structures, not just top-level `device_id`.

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
- Mark mirrored devices with `source_kind=federated`, `is_remote=true`, and `owner_peer_id`.
- Exportable device list for federation includes local-owned devices only.
- Forwarding logic rejects forwarding for devices whose `source_kind` is federated.

Additional guard:
- Include federation metadata (`origin_instance_id`, `hop_count`) in forwarded context.
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

Surface contract for mirrored devices:
- Mirrored devices must appear in:
  - `device.list_status`
  - `list_devices`
  - `device.config.get`
  - `device.config.list`
  - `telemetry.schema.list`
- This keeps FastAPI/UI/processes (for example HDF writer) behavior aligned with truly local devices.

Timeouts/retries:
- Per-peer RPC timeout and retry/backoff config.

---

## Config Sketch (Draft)

```yaml
federation:
  enabled: true
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
- `instance_id` already provides the local runtime identity; a separate `federation.manager_id`
  is usually unnecessary in this codebase.
- Field names are draft and can be adjusted to match existing config schema style.
- Action wildcard matching rules should be documented precisely in implementation.

---

## Compatibility Notes

- Existing UI and sequencer should work with mirrored devices once they appear in `device.list_status` and `capabilities`.
- Existing UI/process helpers that depend on `device.config.list` and `telemetry.schema.list`
  should also work once mirrored entries are included in those responses.
- No stream-capable workflows should target mirrored devices in v1.
- HDF writer behavior for mirrored telemetry/log events should be validated explicitly.

---

## Phased Implementation Plan

1. Federation config schema + validation.
2. Hub manager peer links:
   - per-peer metadata/RPC client
   - per-peer PUB subscriber
3. Mirrored device registry:
   - static mapping
   - explicit remote markers (`source_kind`, `is_remote`, `owner_peer_id`, `remote_device_id`)
4. Metadata mirroring:
   - `device.config.*`
   - `telemetry.schema.list`
   - optional capability cache
5. Hub router mirrored-device forwarding lanes (serial per mirrored device).
6. Event relay/rewrite for mapped devices.
7. Lifecycle/admin forwarding with explicit ACLs.
8. Non-redirection and hop guards.
9. Tests + protocol/startup docs updates.

---

## Test Matrix (Minimum)

- Command forwarding success for mirrored device.
- Independent mirrored-device queues: one slow mirrored device does not block another.
- Blocked command by local ACL.
- Blocked command by remote interlock; error propagated.
- Remote interceptor transform reflected in command event/log context.
- Peer outage -> mirrored command fails closed.
- Mirrored devices not exported to downstream peers.
- Relay filter only publishes mapped mirrored devices locally.
- Mirrored devices appear in `device.config.list` and `telemetry.schema.list`.
- No `manager.chunk_ready` forwarding for mirrored devices.

