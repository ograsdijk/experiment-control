# Manager Federation (Current Behavior)

## Summary

Federation lets one local manager instance mirror selected devices from one or more remote manager instances so they appear alongside local devices.

Current implementation:
- Host-to-host transport is ZMQ only.
- Remote device commands are forwarded router-to-router (`device_router` to remote `device_router`).
- Remote telemetry/status/log events are relayed manager-to-manager (remote manager PUB to local manager SUB).
- Mirrored devices are surfaced as normal local-looking device IDs with explicit remote markers.

This file documents the behavior that is implemented now, not a future design.

---

## Terms

- Hub: the local manager/router instance you connect to.
- Leaf: the remote manager/router instance that owns the physical device.
- Mirrored device: a hub-side alias for a leaf-side `device_id`.

---

## What It Does

Given a static config mapping:
- The hub `device_router` detects mirrored `device_id`s.
- Commands for mirrored devices are forwarded to the owning peer's `router_rpc`.
- The hub manager subscribes to the owning peer's `manager_pub`.
- Relayed remote events are rewritten to the mirrored local `device_id`.
- The hub manager merges mirrored devices into:
  - `manager.devices.list`
  - `device.list_status`
  - `device.config.get`
  - `device.config.list`
  - `manager.telemetry.schema.list` (action-routed)

Mirrored devices are tagged with:
- `source_kind: "federated"`
- `is_remote: true`
- `owner_peer_id`
- `remote_device_id`

Local devices are tagged with:
- `source_kind: "local"`
- `is_remote: false`

---

## Command Path

For a mirrored `type: "command"` request:

1. A client sends the request to the local `device_router`.
2. The local router matches the mirrored `device_id`.
3. The local router uses a dedicated worker thread for that mirrored device.
4. That worker forwards the request to the remote peer's `router_rpc`, rewriting:
   - local mirrored `device_id` -> remote `device_id`
   - adding `federation.origin_instance_id`
   - incrementing `federation.hop_count`
5. The remote router handles the request as a normal local device command.
6. The response is returned unchanged to the original caller.

Current concurrency model:
- One lazy worker thread per mirrored device.
- Each worker owns one outbound DEALER socket to the remote peer.
- Command execution is serial per mirrored device.
- One slow mirrored device does not block another mirrored device.

---

## Lifecycle Requests

The hub manager also handles mirrored lifecycle requests:
- `device.connect`
- `device.disconnect`
- `device.driver.start`
- `device.driver.stop`
- `device.driver.restart`
- `device.recover`

These are forwarded to the leaf only if `allow_lifecycle_ops: true` for that peer.

By default:
- Mirrored device commands are allowed.
- Mirrored lifecycle operations are denied.

---

## Event Relay

For each configured peer:
- The hub manager opens one dedicated SUB socket to that peer's `manager_pub`.
- The hub subscribes only to the configured relay topics.
- Incoming payloads are filtered to mapped mirrored devices.
- Payloads are rewritten to the mirrored local `device_id`.
- The rewritten events are published locally through the hub manager.

Currently relayed topics:
- `manager.telemetry_update`
- `manager.heartbeat`
- `manager.log`
- `manager.command`
- `manager.command_interceptor.error`
- `manager.command_interceptor.modified`

Implementation notes:
- `manager.telemetry_update` is ingested into the hub manager's normal telemetry cache.
- `manager.heartbeat` is ingested into the hub manager's normal heartbeat/liveness path.
- `manager.log` is re-emitted through the manager log path.
- Nested device references in interceptor payloads are rewritten, not just top-level `device_id`.

Not forwarded:
- `manager.chunk_ready`
- stream/shared-memory payloads

---

## Capabilities Caching

Mirrored device capabilities are cached on the hub manager.

Current behavior:
- If a mirrored `capabilities` command succeeds, the local router notifies the local manager to cache the returned payload.
- `manager.devices.list` then includes the cached `capabilities` for that mirrored device.
- There is no TTL yet. The cache stays until overwritten by a later successful `capabilities` call or until restart.

Optional startup warm-up:
- If `warm_capabilities_on_startup: true` is set for a peer, the hub manager issues a normal `capabilities` RPC to each mirrored device on that peer during federation activation.
- This fills the cache before any client explicitly requests capabilities.

If `warm_capabilities_on_startup` is left at its default `false`:
- Capabilities are cached lazily on first successful `capabilities` call.

---

## Configuration

Minimal working example:

```yaml
federation:
  enabled: true
  peers:
    - peer_id: lab2
      router_rpc: tcp://10.0.0.22:6000
      manager_pub: tcp://10.0.0.22:6001
      mirror_devices:
        - local_id: lab2.psu
          remote_device_id: psu
        - local_id: lab2.hv
          remote_device_id: hv
```

Expanded example with active options:

```yaml
federation:
  enabled: true
  peers:
    - peer_id: lab2
      router_rpc: tcp://10.0.0.22:6000
      manager_pub: tcp://10.0.0.22:6001
      rpc_timeout_ms: 1500
      event_stale_s: 3.0
      warm_capabilities_on_startup: false

      mirror_devices:
        - local_id: lab2.psu
          remote_device_id: psu
        - local_id: lab2.hv
          remote_device_id: hv

      policy:
        allow_device_actions: ["*"]
        deny_device_actions: []
        allow_lifecycle_ops: false

      relay:
        topics:
          - manager.telemetry_update
          - manager.heartbeat
          - manager.log
          - manager.command
          - manager.command_interceptor.error
          - manager.command_interceptor.modified
        only_mirrored_devices: true
        include_origin_meta: true
```

Defaults that currently affect runtime:
- `enabled`: `false`
- `rpc_timeout_ms`: inherits `manager.device_rpc_timeout_ms`
- `event_stale_s`: inherits `manager.heartbeat_timeout_s`
- `warm_capabilities_on_startup`: `false`
- `policy.allow_device_actions`: `["*"]`
- `policy.deny_device_actions`: `[]`
- `policy.allow_lifecycle_ops`: `false`
- `relay.topics`: the six topics listed above
- `relay.only_mirrored_devices`: `true`
- `relay.include_origin_meta`: `true`

Pattern matching:
- `allow_device_actions` and `deny_device_actions` use `fnmatch.fnmatchcase` semantics.

Validation rules:
- `peer_id` must be unique.
- `local_id` must be unique across all mirrored devices.
- `local_id` must not collide with a locally configured device ID.
- Each `(peer_id, remote_device_id)` mapping must be unique.
- `router_rpc` and `manager_pub` must be `tcp://...` endpoints.
- `allow_reexport: true` is rejected.

Accepted but currently reserved:
- `reconnect_backoff_s`
- `reconnect_backoff_max_s`
- `policy.allow_admin_ops`

These are parsed and validated but do not currently change runtime behavior.

---

## Re-Export Guard

Mirrored devices are not re-exported.

Current enforcement:
- If a request arrives with `federation.hop_count > 0` and targets a mirrored device on that host, the router rejects it.

This prevents mirrored-device-to-mirrored-device forwarding chains.

---

## Current Limits

- No stream/shared-memory forwarding.
- No `manager.chunk_ready` forwarding.
- No separate leaf-side inbound federation ACL.
  - The leaf accepts forwarded requests the same way it accepts normal router requests.
  - Normal local driver checks and local command interceptors still apply.
- No cross-host forwarding for `manager.processes.*` or manager-control/admin RPC.
- Reconnect backoff settings are not yet used by the runtime.

---

## Compatibility Notes

- FastAPI is not part of the host-to-host transport path.
  - FastAPI can be a client of the local hub.
  - Federation itself runs underneath that, via ZMQ router/manager sockets.
- Existing UI/processes that consume `device.list_status`, `device.config.list`, and `manager.telemetry.schema.list` can see mirrored devices through the hub.
- HDF/other process behavior for mirrored telemetry now depends on the same manager-side inventory/config/schema surfaces as local devices.
