from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import zmq

from ..utils.zmq_helpers import json_dumps, safe_json_loads
from .config import FederationConfig, FederationPeerConfig

Json = dict[str, Any]

_MIRRORED_LIFECYCLE_TYPES = {
    "device.connect",
    "device.disconnect",
    "device.driver.start",
    "device.driver.stop",
    "device.driver.restart",
    "device.recover",
}


@dataclass
class PeerRuntime:
    config: FederationPeerConfig
    sub_sock: zmq.Socket | None = None
    last_event_recv_mono: float | None = None
    last_rpc_ok_mono: float | None = None
    last_error: str | None = None


@dataclass
class MirroredDeviceRuntime:
    peer_id: str
    local_id: str
    remote_device_id: str
    config_payload: Json | None = None
    schema_entry: Json | None = None
    capabilities: Json | None = None
    last_hb_payload: Json | None = None
    last_hb_recv_mono: float | None = None
    last_error: str | None = None


class FederationHub:
    def __init__(
        self,
        *,
        ctx: zmq.Context,
        poller: zmq.Poller,
        manager: Any,
        config: FederationConfig,
        instance_id: str,
    ) -> None:
        self._ctx = ctx
        self._poller = poller
        self._manager = manager
        self._config = config
        self._instance_id = str(instance_id or "").strip() or "unknown"
        self._peers: dict[str, PeerRuntime] = {}
        self._mirrors: dict[str, MirroredDeviceRuntime] = {}
        self._socket_to_peer: dict[zmq.Socket, str] = {}
        self._last_liveness: dict[str, str] = {}

        for peer in config.peers:
            self._peers[peer.peer_id] = PeerRuntime(config=peer)
            for mirror in peer.mirror_devices:
                self._mirrors[mirror.local_id] = MirroredDeviceRuntime(
                    peer_id=peer.peer_id,
                    local_id=mirror.local_id,
                    remote_device_id=mirror.remote_device_id,
                )

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled and self._peers)

    def is_mirrored_device(self, device_id: str) -> bool:
        return str(device_id or "") in self._mirrors

    def mirrored_device_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._mirrors))

    def activate(self) -> None:
        if not self.enabled:
            return
        for peer_id, peer_rt in self._peers.items():
            if peer_rt.sub_sock is None:
                sub = self._ctx.socket(zmq.SUB)
                sub.setsockopt(zmq.LINGER, 0)
                sub.setsockopt(zmq.RCVTIMEO, 0)
                for topic in peer_rt.config.relay.topics:
                    sub.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
                sub.connect(peer_rt.config.manager_pub)
                self._poller.register(sub, zmq.POLLIN)
                peer_rt.sub_sock = sub
                self._socket_to_peer[sub] = peer_id
            self._refresh_peer_metadata(peer_rt)

    def close(self) -> None:
        for sock, _peer_id in list(self._socket_to_peer.items()):
            try:
                self._poller.unregister(sock)
            except Exception:
                pass
            try:
                sock.close(0)
            except Exception:
                pass
        self._socket_to_peer.clear()
        for peer_rt in self._peers.values():
            peer_rt.sub_sock = None

    def mirror_route_entries(self) -> list[Json]:
        if not self.enabled:
            return []
        out: list[Json] = []
        for peer in self._config.peers:
            for mirror in peer.mirror_devices:
                out.append(
                    {
                        "local_id": mirror.local_id,
                        "peer_id": peer.peer_id,
                        "remote_device_id": mirror.remote_device_id,
                        "peer_router_rpc": peer.router_rpc,
                        "rpc_timeout_ms": peer.rpc_timeout_ms,
                        "allow_device_actions": list(peer.policy.allow_device_actions),
                        "deny_device_actions": list(peer.policy.deny_device_actions),
                        "allow_lifecycle_ops": bool(peer.policy.allow_lifecycle_ops),
                        "allow_admin_ops": bool(peer.policy.allow_admin_ops),
                        "origin_instance_id": self._instance_id,
                    }
                )
        return out

    def handle_poll_events(self, events: dict[zmq.Socket, int]) -> None:
        if not self.enabled:
            return
        for sock, peer_id in list(self._socket_to_peer.items()):
            if events.get(sock) != zmq.POLLIN:
                continue
            peer_rt = self._peers.get(peer_id)
            if peer_rt is None:
                continue
            self._drain_peer_events(peer_rt)

    def check_timeouts(self, now_mono: float) -> None:
        if not self.enabled:
            return
        for local_id, mirror in self._mirrors.items():
            peer_rt = self._peers.get(mirror.peer_id)
            if peer_rt is None:
                continue
            hb_age_s: float | None = None
            liveness = "OFFLINE"
            if mirror.last_hb_recv_mono is not None:
                hb_age_s = now_mono - mirror.last_hb_recv_mono
                if hb_age_s <= peer_rt.config.event_stale_s:
                    payload = mirror.last_hb_payload or {}
                    if bool(payload.get("device_reachable", False)):
                        liveness = "ONLINE"
                    else:
                        liveness = "DISCONNECTED"
            if self._last_liveness.get(local_id) != liveness:
                self._last_liveness[local_id] = liveness
                self._manager._publish_manager_event(
                    "manager.liveness",
                    {"device_id": local_id, "liveness": liveness, "age_s": hb_age_s},
                )

    def list_devices_snapshot(self) -> list[Json]:
        out: list[Json] = []
        for local_id in sorted(self._mirrors):
            mirror = self._mirrors[local_id]
            peer_rt = self._peers[mirror.peer_id]
            out.append(
                {
                    "device_id": local_id,
                    "registered": True,
                    "rpc_endpoint": peer_rt.config.router_rpc,
                    "pub_endpoint": peer_rt.config.manager_pub,
                    "capabilities": mirror.capabilities,
                    "source_kind": "federated",
                    "is_remote": True,
                    "owner_peer_id": mirror.peer_id,
                    "remote_device_id": mirror.remote_device_id,
                }
            )
        return out

    def device_status_snapshot(self, device_id: str) -> Json:
        mirror = self._mirrors.get(str(device_id or ""))
        if mirror is None:
            raise KeyError(f"Unknown device_id {device_id!r}")
        peer_rt = self._peers[mirror.peer_id]
        now_mono = time.monotonic()
        hb_age_s: float | None = None
        liveness = "OFFLINE"
        payload = mirror.last_hb_payload or {}
        if mirror.last_hb_recv_mono is not None:
            hb_age_s = now_mono - mirror.last_hb_recv_mono
            if hb_age_s <= peer_rt.config.event_stale_s:
                if bool(payload.get("device_reachable", False)):
                    liveness = "ONLINE"
                else:
                    liveness = "DISCONNECTED"

        telemetry_age_s: float | None = None
        latest_ts = self._manager._telemetry_last_bundle_ts.get(mirror.local_id)
        if latest_ts is not None:
            telemetry_age_s = now_mono - latest_ts.t_mono

        return {
            "device_id": mirror.local_id,
            "registered": True,
            "rpc_endpoint": peer_rt.config.router_rpc,
            "pub_endpoint": peer_rt.config.manager_pub,
            "liveness": liveness,
            "hb_age_s": hb_age_s,
            "telemetry_age_s": telemetry_age_s,
            "driver_state": payload.get("driver_state"),
            "device_state": payload.get("device_state"),
            "device_reachable": payload.get("device_reachable"),
            "last_error": mirror.last_error or payload.get("last_error") or peer_rt.last_error,
            "driver_process": {
                "state": "FEDERATED",
                "pid": None,
                "restart_count": 0,
                "last_exit_code": None,
                "last_error": peer_rt.last_error,
            },
            "source_kind": "federated",
            "is_remote": True,
            "owner_peer_id": mirror.peer_id,
            "remote_device_id": mirror.remote_device_id,
        }

    def device_config_get(self, device_id: str) -> Json | None:
        mirror = self._mirrors.get(str(device_id or ""))
        if mirror is None:
            return None
        return dict(mirror.config_payload or self._placeholder_config(mirror))

    def device_config_list(self) -> list[Json]:
        out: list[Json] = []
        for local_id in sorted(self._mirrors):
            mirror = self._mirrors[local_id]
            out.append(dict(mirror.config_payload or self._placeholder_config(mirror)))
        return out

    def telemetry_schema_devices(self) -> list[Json]:
        out: list[Json] = []
        for local_id in sorted(self._mirrors):
            mirror = self._mirrors[local_id]
            item = mirror.schema_entry
            if item is None:
                item = {
                    "device_id": mirror.local_id,
                    "signals": [],
                    "dtypes": [],
                    "units": [],
                    "source_kind": "federated",
                    "is_remote": True,
                    "owner_peer_id": mirror.peer_id,
                    "remote_device_id": mirror.remote_device_id,
                }
            out.append(dict(item))
        return out

    def forward_device_request(self, req: Json) -> Json | None:
        device_id = str(req.get("device_id", ""))
        mirror = self._mirrors.get(device_id)
        if mirror is None:
            return None

        rtype = str(req.get("type", ""))
        peer_rt = self._peers[mirror.peer_id]
        policy = peer_rt.config.policy
        if rtype == "command":
            action = str(req.get("action", ""))
            if not policy.allows_device_action(action):
                err = {
                    "code": "federation_acl_denied",
                    "message": (
                        f"federation policy denied mirrored command {device_id!r}.{action}"
                    ),
                }
                mirror.last_error = str(err.get("message"))
                return {"ok": False, "error": err}
        elif rtype in _MIRRORED_LIFECYCLE_TYPES:
            if not policy.allow_lifecycle_ops:
                err = {
                    "code": "federation_acl_denied",
                    "message": (
                        f"federation policy denied mirrored lifecycle request {rtype!r}"
                    ),
                }
                mirror.last_error = str(err.get("message"))
                return {"ok": False, "error": err}
        else:
            return None

        outbound = dict(req)
        outbound["device_id"] = mirror.remote_device_id
        outbound["federation"] = self._next_federation_meta(req.get("federation"))
        resp = self._rpc_call(peer_rt, outbound)
        if resp is None:
            mirror.last_error = "peer unavailable"
            peer_rt.last_error = "peer unavailable"
            return {
                "ok": False,
                "error": {
                    "code": "peer_unavailable",
                    "message": f"peer {mirror.peer_id!r} unavailable",
                },
            }
        peer_rt.last_rpc_ok_mono = time.monotonic()
        peer_rt.last_error = None
        mirror.last_error = None
        result = resp.get("result")
        if (
            rtype == "command"
            and str(req.get("action", "")) == "capabilities"
            and bool(resp.get("ok"))
            and isinstance(result, dict)
        ):
            mirror.capabilities = dict(result)
        return resp

    def update_capabilities(self, device_id: str, capabilities: Json) -> None:
        mirror = self._mirrors.get(str(device_id or ""))
        if mirror is None:
            raise KeyError(f"Unknown mirrored device_id {device_id!r}")
        mirror.capabilities = dict(capabilities)

    def _refresh_peer_metadata(self, peer_rt: PeerRuntime) -> None:
        config_resp = self._rpc_call(peer_rt, {"type": "device.config.list"})
        schema_resp = self._rpc_call(peer_rt, {"action": "manager.telemetry.schema.list"})

        config_items: list[Json] = []
        if (
            isinstance(config_resp, dict)
            and bool(config_resp.get("ok"))
            and isinstance(config_resp.get("result"), list)
        ):
            config_items = [item for item in config_resp.get("result", []) if isinstance(item, dict)]

        schema_items: list[Json] = []
        if isinstance(schema_resp, dict) and bool(schema_resp.get("ok")):
            result = schema_resp.get("result")
            if isinstance(result, dict) and isinstance(result.get("devices"), list):
                schema_items = [item for item in result.get("devices", []) if isinstance(item, dict)]

        config_by_remote = {
            str(item.get("device_id", "")): item
            for item in config_items
            if str(item.get("device_id", ""))
        }
        schema_by_remote = {
            str(item.get("device_id", "")): item
            for item in schema_items
            if str(item.get("device_id", ""))
        }

        for mirror in self._mirrors.values():
            if mirror.peer_id != peer_rt.config.peer_id:
                continue
            remote_config = config_by_remote.get(mirror.remote_device_id)
            if remote_config is not None:
                mirror.config_payload = self._rewrite_config_payload(mirror, remote_config)
            elif mirror.config_payload is None:
                mirror.config_payload = self._placeholder_config(mirror)
            remote_schema = schema_by_remote.get(mirror.remote_device_id)
            if remote_schema is not None:
                mirror.schema_entry = self._rewrite_schema_entry(mirror, remote_schema)
        if peer_rt.config.warm_capabilities_on_startup:
            self._warm_peer_capabilities(peer_rt)
        if config_resp is None or schema_resp is None:
            peer_rt.last_error = "metadata fetch failed"

    def _warm_peer_capabilities(self, peer_rt: PeerRuntime) -> None:
        for mirror in self._mirrors.values():
            if mirror.peer_id != peer_rt.config.peer_id:
                continue
            resp = self._rpc_call(
                peer_rt,
                {
                    "type": "command",
                    "device_id": mirror.remote_device_id,
                    "action": "capabilities",
                    "params": {},
                },
            )
            if isinstance(resp, dict) and bool(resp.get("ok")):
                result = resp.get("result")
                if isinstance(result, dict):
                    mirror.capabilities = dict(result)

    def _rewrite_config_payload(self, mirror: MirroredDeviceRuntime, payload: Json) -> Json:
        out = dict(payload)
        out["device_id"] = mirror.local_id
        out["source_kind"] = "federated"
        out["is_remote"] = True
        out["owner_peer_id"] = mirror.peer_id
        out["remote_device_id"] = mirror.remote_device_id
        return out

    def _rewrite_schema_entry(self, mirror: MirroredDeviceRuntime, payload: Json) -> Json:
        out = dict(payload)
        out["device_id"] = mirror.local_id
        out["source_kind"] = "federated"
        out["is_remote"] = True
        out["owner_peer_id"] = mirror.peer_id
        out["remote_device_id"] = mirror.remote_device_id
        return out

    def _placeholder_config(self, mirror: MirroredDeviceRuntime) -> Json:
        return {
            "version": 1,
            "device_id": mirror.local_id,
            "yaml_text": "",
            "device_metadata": {},
            "stream_metadata": {},
            "telemetry_calls": [],
            "stream_calls": [],
            "run_meta_calls": [],
            "source_kind": "federated",
            "is_remote": True,
            "owner_peer_id": mirror.peer_id,
            "remote_device_id": mirror.remote_device_id,
        }

    def _drain_peer_events(self, peer_rt: PeerRuntime) -> None:
        sock = peer_rt.sub_sock
        if sock is None:
            return
        while True:
            try:
                topic_b, payload_b = sock.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception as e:
                peer_rt.last_error = str(e)
                break
            topic = topic_b.decode("utf-8", errors="ignore")
            payload = safe_json_loads(payload_b)
            if not isinstance(payload, dict):
                continue
            peer_rt.last_event_recv_mono = time.monotonic()
            peer_rt.last_error = None
            self._relay_event(peer_rt, topic, payload)

    def _relay_event(self, peer_rt: PeerRuntime, topic: str, payload: Json) -> None:
        local_id = self._resolve_local_id(peer_rt, topic, payload)
        if local_id is None:
            # No matching mirrored device. If `only_mirrored_devices=True`
            # (the default), drop. Otherwise relay the event verbatim so
            # peer-level events (system logs etc.) reach the local bus.
            if peer_rt.config.relay.only_mirrored_devices:
                return
            self._relay_event_verbatim(peer_rt, topic, payload)
            return
        mirror = self._mirrors.get(local_id)
        if mirror is None:
            return

        rewritten = self._rewrite_payload(
            topic=topic,
            payload=payload,
            local_id=local_id,
            peer_id=peer_rt.config.peer_id,
            remote_device_id=mirror.remote_device_id,
            include_origin_meta=peer_rt.config.relay.include_origin_meta,
        )
        if topic == "manager.telemetry_update":
            self._manager._ingest_telemetry(rewritten)
            return
        if topic == "manager.heartbeat":
            mirror.last_hb_payload = dict(rewritten)
            mirror.last_hb_recv_mono = time.monotonic()
            self._manager._ingest_heartbeat(rewritten)
            return
        if topic == "manager.log":
            self._manager._emit_log_from_payload(rewritten, default_topic=topic)
            return
        self._manager._publish_manager_event(topic, rewritten)

    def _relay_event_verbatim(
        self, peer_rt: PeerRuntime, topic: str, payload: Json
    ) -> None:
        """Relay a peer event with no associated mirrored device.

        Reached only when `relay.only_mirrored_devices=False`. The payload is
        passed through with origin metadata stamped with the immediate peer's
        identity (no `device_id` rewriting, since there's no local mirror to
        map to). Heartbeat/telemetry topics are dropped here because they would
        otherwise inject orphan state into the manager's caches.

        Origin-metadata keys (`source_kind`, `is_remote`, `owner_peer_id`) are
        always (re)assigned with this hub's view of the immediate peer, never
        carried over from the peer-supplied payload. Otherwise a peer could
        spoof `is_remote=False` / `source_kind="local"` / a different peer's
        `owner_peer_id` to defeat the federation trust boundary used by
        downstream consumers (e.g. hdf_writer._is_remote_config,
        influx_writer._handle_device_config). This mirrors the unconditional
        assignment used by every other emitter in this file (see
        `_rewrite_payload`, the device-snapshot helpers, etc.).
        """
        if topic in {"manager.heartbeat", "manager.telemetry_update"}:
            return
        out = dict(payload)
        # Strip any pre-existing origin-meta from the peer's payload; we
        # rewrite below (or, with include_origin_meta=False, leave the keys
        # absent so consumers can't be deceived).
        for spoofable_key in ("source_kind", "is_remote", "owner_peer_id"):
            out.pop(spoofable_key, None)
        if peer_rt.config.relay.include_origin_meta:
            out["source_kind"] = "federated"
            out["is_remote"] = True
            out["owner_peer_id"] = peer_rt.config.peer_id
        if topic == "manager.log":
            self._manager._emit_log_from_payload(out, default_topic=topic)
            return
        self._manager._publish_manager_event(topic, out)

    def _resolve_local_id(
        self, peer_rt: PeerRuntime, topic: str, payload: Json
    ) -> str | None:
        remote_id = None
        if isinstance(payload.get("device_id"), str) and str(payload.get("device_id")).strip():
            remote_id = str(payload.get("device_id")).strip()
        elif topic == "manager.command_interceptor.error":
            error_raw = payload.get("error")
            if isinstance(error_raw, dict) and isinstance(error_raw.get("device_id"), str):
                remote_id = str(error_raw.get("device_id")).strip()
            cmd_raw = payload.get("command")
            if not remote_id and isinstance(cmd_raw, dict) and isinstance(cmd_raw.get("device_id"), str):
                remote_id = str(cmd_raw.get("device_id")).strip()
        elif topic == "manager.command_interceptor.modified":
            before_raw = payload.get("before")
            after_raw = payload.get("after")
            if isinstance(before_raw, dict) and isinstance(before_raw.get("device_id"), str):
                remote_id = str(before_raw.get("device_id")).strip()
            if not remote_id and isinstance(after_raw, dict) and isinstance(after_raw.get("device_id"), str):
                remote_id = str(after_raw.get("device_id")).strip()

        if not remote_id:
            return None

        for mirror in self._mirrors.values():
            if (
                mirror.peer_id == peer_rt.config.peer_id
                and mirror.remote_device_id == remote_id
            ):
                return mirror.local_id
        return None

    @staticmethod
    def _rewrite_payload(
        *,
        topic: str,
        payload: Json,
        local_id: str,
        peer_id: str,
        remote_device_id: str,
        include_origin_meta: bool,
    ) -> Json:
        out = dict(payload)

        def _rewrite_device_id(container: object) -> None:
            if not isinstance(container, dict):
                return
            raw = container.get("device_id")
            if isinstance(raw, str) and raw == remote_device_id:
                container["device_id"] = local_id

        _rewrite_device_id(out)
        for key in ("error", "command", "before", "after"):
            child = out.get(key)
            if isinstance(child, dict):
                child_copy = dict(child)
                _rewrite_device_id(child_copy)
                out[key] = child_copy

        if include_origin_meta:
            out["source_kind"] = "federated"
            out["is_remote"] = True
            out["owner_peer_id"] = peer_id
            out["remote_device_id"] = remote_device_id
        return out

    def _rpc_call(self, peer_rt: PeerRuntime, payload: Json) -> Json | None:
        sock = self._ctx.socket(zmq.DEALER)
        sock.setsockopt(zmq.LINGER, 0)
        timeout_ms = int(peer_rt.config.rpc_timeout_ms)
        sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
        sock.setsockopt(zmq.SNDTIMEO, timeout_ms)
        sock.connect(peer_rt.config.router_rpc)
        try:
            sock.send(json_dumps(payload))
            raw = sock.recv()
        except Exception as e:
            peer_rt.last_error = str(e)
            return None
        finally:
            try:
                sock.close(0)
            except Exception:
                pass

        resp = safe_json_loads(raw)
        if not isinstance(resp, dict):
            peer_rt.last_error = "invalid response"
            return None
        return resp

    def _next_federation_meta(self, raw: object) -> Json:
        hop_count = 0
        if isinstance(raw, dict):
            try:
                hop_count = int(raw.get("hop_count", 0))
            except Exception:
                hop_count = 0
        return {
            "origin_instance_id": self._instance_id,
            "hop_count": hop_count + 1,
        }

