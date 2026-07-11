from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import zmq

from ..utils.manager_network import connect_dealer, resolve_tcp_endpoint
from ..utils.zmq_helpers import json_dumps, recv_json, safe_json_loads, send_json
from .config import FederationConfig, FederationPeerConfig, FederationPolicy

Json = dict[str, Any]

_MIRRORED_LIFECYCLE_TYPES = {
    "device.connect",
    "device.disconnect",
    "device.driver.start",
    "device.driver.stop",
    "device.driver.restart",
    "device.recover",
}

# How long _rpc_call (forward path, on the poll loop) reuses a peer's resolved
# IP before re-resolving — bounds getaddrinfo to once/TTL instead of per command,
# and picks up DNS changes on the next miss.
_RESOLVE_CACHE_TTL_S = 60.0

# How often the warmup thread re-fetches an already-warmed peer's metadata, so
# config/schema drift after a peer restart is eventually picked up.
_METADATA_REFRESH_S = 300.0


@dataclass
class PeerRuntime:
    config: FederationPeerConfig
    sub_sock: zmq.Socket | None = None
    last_event_recv_mono: float | None = None
    last_rpc_ok_mono: float | None = None
    last_error: str | None = None
    # Set on the main thread once the background warmup thread has delivered
    # this peer's metadata (config + schema). Until then mirrored devices are
    # served from placeholder config/schema.
    metadata_warmed: bool = False
    # Cache of the resolved router_rpc endpoint for the forward path
    # (_rpc_call), so a hostname peer isn't getaddrinfo'd on every command.
    # None until first resolve; expires after _RESOLVE_CACHE_TTL_S. Owned by
    # whichever thread runs _rpc_call for this peer (main thread for process
    # forwards, the lifecycle executor for device forwards, serialised by
    # rpc_lock below) -- never read/written concurrently.
    resolved_endpoint: str | None = None
    resolved_expiry_mono: float = 0.0
    # Persistent DEALER used by _rpc_call (F10: was reconnected per call).
    # Guarded by rpc_lock so device forwards (lifecycle executor threads) and
    # process forwards (main thread) can't interleave send/recv on it.
    rpc_sock: zmq.Socket | None = None
    rpc_sock_endpoint: str | None = None
    rpc_lock: threading.Lock = field(default_factory=threading.Lock)


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


@dataclass
class MirroredProcessRuntime:
    """Hub-side state for a federated PROCESS (RPC forwarding + telemetry relay).

    Distinct from MirroredDeviceRuntime: processes are reached via
    ``manager.processes.rpc`` (not device commands) and publish on the
    ``manager.process_telemetry_update`` channel, kept separate from device
    telemetry. ``schema_entry`` is warmed from the peer's
    ``manager.process_telemetry.schema.list`` so the local HDF writer can
    create ``/process_telemetry/<local_id>`` datasets.
    """

    peer_id: str
    local_id: str
    remote_process_id: str
    schema_entry: Json | None = None
    last_error: str | None = None
    # Last relayed `manager.process.heartbeat` payload + manager-side receive
    # time, mirroring MirroredDeviceRuntime, so a mirrored process gets real
    # heartbeat-based liveness (see list_processes_snapshot).
    last_hb_payload: Json | None = None
    last_hb_recv_mono: float | None = None


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
        # ``ctx`` (the manager's shared context) is intentionally NOT stored:
        # every federation socket lives on the dedicated _fed_ctx below, so a bad
        # peer host can never block the manager's I/O thread. See _ensure_fed_ctx.
        self._poller = poller
        self._manager = manager
        self._config = config
        self._instance_id = str(instance_id or "").strip() or "unknown"
        self._peers: dict[str, PeerRuntime] = {}
        self._mirrors: dict[str, MirroredDeviceRuntime] = {}
        self._process_mirrors: dict[str, MirroredProcessRuntime] = {}
        self._socket_to_peer: dict[zmq.Socket, str] = {}
        self._last_liveness: dict[str, str] = {}

        # Dedicated zmq context for ALL federation peer sockets (relay SUBs and
        # peer RPC DEALERs). DEFENSE-IN-DEPTH: the PRIMARY guard against a bad
        # peer host starving heartbeats is pre-resolution — every connect() is
        # handed an IP from resolve_tcp_endpoint(), never a hostname, so libzmq's
        # I/O thread never blocks on DNS. This separate context (separate I/O
        # thread) is kept as a second line of defense so that even if some future
        # path connects a hostname directly, it degrades only federation, never
        # the local stack's heartbeat/driver sockets on the main context. The
        # poller is shared (zmq_poll works across contexts). Created lazily on
        # activate() so a disabled-federation manager allocates nothing.
        self._fed_ctx: zmq.Context | None = None
        self._fed_ctx_closed = False

        # Background metadata warmup. The thread does all peer metadata RPCs so
        # an unreachable/slow peer can never block the manager poll loop; the
        # main thread applies results from this queue.
        self._warmup_thread: threading.Thread | None = None
        self._warmup_stop = threading.Event()
        # (peer_id, config_resp, schema_resp, proc_schema_resp, caps, error).
        # error is None on success; set to a reason string on failure so the
        # main thread can surface it via peer_rt.last_error
        # (device_status_snapshot). proc_schema_resp is the peer's
        # manager.process_telemetry.schema.list (for mirrored processes).
        self._warmup_results: "queue.Queue[tuple[str, Json | None, Json | None, Json | None, dict[str, Json], str | None]]" = (
            queue.Queue()
        )

        for peer in config.peers:
            self._peers[peer.peer_id] = PeerRuntime(config=peer)
            for mirror in peer.mirror_devices:
                self._mirrors[mirror.local_id] = MirroredDeviceRuntime(
                    peer_id=peer.peer_id,
                    local_id=mirror.local_id,
                    remote_device_id=mirror.remote_device_id,
                )
            for process in peer.mirror_processes:
                self._process_mirrors[process.local_id] = MirroredProcessRuntime(
                    peer_id=peer.peer_id,
                    local_id=process.local_id,
                    remote_process_id=process.remote_process_id,
                )

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled and self._peers)

    def is_mirrored_device(self, device_id: str) -> bool:
        return str(device_id or "") in self._mirrors

    def mirrored_device_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._mirrors))

    def is_mirrored_process(self, process_id: str) -> bool:
        return str(process_id or "") in self._process_mirrors

    def mirrored_process_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._process_mirrors))

    def _ensure_fed_ctx(self) -> zmq.Context:
        if self._fed_ctx_closed:
            # Don't resurrect a terminated context after close() (e.g. a late
            # federated command racing shutdown) — that would leak a context.
            raise RuntimeError("federation hub is closed")
        if self._fed_ctx is None:
            self._fed_ctx = zmq.Context()
        return self._fed_ctx

    def activate(self) -> None:
        if not self.enabled:
            return
        fed_ctx = self._ensure_fed_ctx()
        for peer_id, peer_rt in self._peers.items():
            if peer_rt.sub_sock is None:
                sub = fed_ctx.socket(zmq.SUB)
                sub.setsockopt(zmq.LINGER, 0)
                sub.setsockopt(zmq.RCVTIMEO, 0)
                for topic in peer_rt.config.relay.topics:
                    sub.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
                sub.connect(peer_rt.config.manager_pub)
                self._poller.register(sub, zmq.POLLIN)
                peer_rt.sub_sock = sub
                self._socket_to_peer[sub] = peer_id
            # NB: do not reset peer_rt.metadata_warmed here — activate() runs
            # twice (startup_sequence then run_forever) and clobbering it would
            # leave already-warmed peers stuck unwarmed (the guard below means
            # the warmup thread won't re-run to fix it).
        # Do NOT fetch peer metadata on this (poll-loop) thread: a blocking RPC
        # to an unreachable peer would stall manager/TUI startup. Run the
        # best-effort warmup on a background thread instead; results are applied
        # by _drain_warmup_results() (called from check_timeouts()). Until a peer
        # is warmed its mirrored devices are served from placeholder
        # config/schema (see device_config_list / telemetry_schema_devices).
        if self._warmup_thread is None:
            self._warmup_stop.clear()
            self._warmup_thread = threading.Thread(
                target=self._warmup_loop, name="federation-warmup", daemon=True
            )
            self._warmup_thread.start()

    def close(self) -> None:
        self._warmup_stop.set()
        thread = self._warmup_thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._warmup_thread = None
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
            # Safe without rpc_lock: the manager's lifecycle executor (the
            # only other thread that can touch rpc_sock) is fully drained
            # before close() is called (see Manager._shutdown_cleanup).
            self._close_rpc_socket(peer_rt)
        # Tear down the federation context last, after the warmup thread is
        # joined (its DEALERs closed) and the SUBs above are closed.
        self._fed_ctx_closed = True
        if self._fed_ctx is not None:
            try:
                self._fed_ctx.term()
            except Exception:
                pass
            self._fed_ctx = None

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
        self._drain_warmup_results()
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

    def list_processes_snapshot(self) -> list[Json]:
        """Snapshot of mirrored PROCESSES for ``manager.processes.list``.

        Parallels ``list_devices_snapshot`` but shaped like a process entry so the
        UIs render it as a (federated) process row. Liveness is derived from the
        relayed ``manager.process.heartbeat`` (``last_hb_recv_mono``) vs the peer's
        ``event_stale_s``, mirroring device liveness. Supervised fields the local
        manager can't know (restart count, exit code) are neutral defaults;
        ``state``/``pid`` come from the last heartbeat when present.
        """
        out: list[Json] = []
        now_mono = time.monotonic()
        for local_id in sorted(self._process_mirrors):
            mirror = self._process_mirrors[local_id]
            peer_rt = self._peers[mirror.peer_id]
            payload = mirror.last_hb_payload or {}
            hb_age_s: float | None = None
            liveness = "OFFLINE"
            if mirror.last_hb_recv_mono is not None:
                hb_age_s = now_mono - mirror.last_hb_recv_mono
                if hb_age_s <= peer_rt.config.event_stale_s:
                    liveness = "ONLINE"
            out.append(
                {
                    "process_id": local_id,
                    "registered": True,
                    "rpc_endpoint": peer_rt.config.router_rpc,
                    "state": payload.get("state")
                    or payload.get("process_state")
                    or "FEDERATED",
                    "liveness": liveness,
                    "pid": payload.get("pid"),
                    "hb_age_s": hb_age_s,
                    "restart_count": 0,
                    "last_exit_code": None,
                    "last_error": mirror.last_error or peer_rt.last_error,
                    "source_kind": "federated",
                    "is_remote": True,
                    "owner_peer_id": mirror.peer_id,
                    "remote_process_id": mirror.remote_process_id,
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
        latest_recv_mono = self._manager._telemetry_last_recv_mono.get(mirror.local_id)
        latest_ts = self._manager._telemetry_last_bundle_ts.get(mirror.local_id)
        if latest_recv_mono is not None:
            telemetry_age_s = now_mono - latest_recv_mono
        elif latest_ts is not None:
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

    def process_telemetry_schema_processes(self) -> list[Json]:
        """Schema entries for mirrored PROCESSES (parallel to
        telemetry_schema_devices). Tagged ``source_kind: "process"`` and
        ``is_remote: True`` so HDF records them and consumers keep the
        device/process distinction. Placeholder (empty signals) until warmed."""
        out: list[Json] = []
        for local_id in sorted(self._process_mirrors):
            mirror = self._process_mirrors[local_id]
            item = mirror.schema_entry
            if item is None:
                item = {
                    "process_id": mirror.local_id,
                    "signals": [],
                    "dtypes": [],
                    "units": [],
                    "source_kind": "process",
                    "is_remote": True,
                    "owner_peer_id": mirror.peer_id,
                    "remote_process_id": mirror.remote_process_id,
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

    def forward_process_request(self, req: Json) -> Json | None:
        """Forward a ``manager.processes.rpc`` for a mirrored process to its
        owning peer's router. Returns None if ``process_id`` is not a mirror
        (caller falls through to local dispatch). The inner request action
        (``request.type``, e.g. ``mw.retune``) is gated by the peer's
        ``allow_process_actions`` ACL (default deny-all)."""
        process_id = str(req.get("process_id", ""))
        mirror = self._process_mirrors.get(process_id)
        if mirror is None:
            return None

        request = req.get("request")
        action = ""
        if isinstance(request, dict):
            action = str(request.get("type", ""))
        peer_rt = self._peers[mirror.peer_id]
        if not self._process_action_allowed(peer_rt.config.policy, action):
            err = {
                "code": "federation_acl_denied",
                "message": (
                    f"federation policy denied mirrored process rpc "
                    f"{process_id!r}.{action}"
                ),
            }
            mirror.last_error = str(err.get("message"))
            return {"ok": False, "error": err}

        outbound = dict(req)
        outbound["process_id"] = mirror.remote_process_id
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
        if action == "process.capabilities" and bool(resp.get("ok")):
            self._annotate_process_capabilities(peer_rt, resp.get("result"))
        return resp

    @staticmethod
    def _process_action_allowed(policy: FederationPolicy, action: str) -> bool:
        """Whether a federated process RPC action is permitted.

        ``process.capabilities`` is read-only introspection (no side effects) and
        is required for clients to render a mirrored process's action set, so it is
        ALWAYS allowed; every other action obeys ``allow_process_actions``.
        """
        if action == "process.capabilities":
            return True
        return policy.allows_process_action(action)

    def _annotate_process_capabilities(
        self, peer_rt: PeerRuntime, result: Any
    ) -> None:
        """Tag each capability member with ``federation_allowed`` per the peer ACL
        so clients can grey out actions this federation link won't permit."""
        if not isinstance(result, dict):
            return
        members = result.get("members")
        if not isinstance(members, list):
            return
        policy = peer_rt.config.policy
        for member in members:
            if isinstance(member, dict):
                member["federation_allowed"] = self._process_action_allowed(
                    policy, str(member.get("name", ""))
                )

    def _drain_warmup_results(self) -> None:
        """Apply any metadata the background warmup thread has delivered.

        Runs on the manager poll-loop thread (from check_timeouts) and never
        does I/O, so it cannot block.
        """
        while True:
            try:
                peer_id, config_resp, schema_resp, proc_schema_resp, caps, error = (
                    self._warmup_results.get_nowait()
                )
            except queue.Empty:
                break
            peer_rt = self._peers.get(peer_id)
            if peer_rt is None:
                continue
            if error is not None:
                # Surface why a mirror still shows placeholder metadata (e.g. an
                # unresolvable/unreachable peer host) without crashing anything.
                peer_rt.last_error = error
                continue
            self._apply_peer_metadata(
                peer_rt, config_resp, schema_resp, proc_schema_resp, caps
            )
            peer_rt.metadata_warmed = True
            peer_rt.last_rpc_ok_mono = time.monotonic()
            peer_rt.last_error = None

    def _apply_peer_metadata(
        self,
        peer_rt: PeerRuntime,
        config_resp: Json | None,
        schema_resp: Json | None,
        proc_schema_resp: Json | None,
        caps: dict[str, Json],
    ) -> None:
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
            cap = caps.get(mirror.remote_device_id)
            if isinstance(cap, dict):
                mirror.capabilities = dict(cap)

        # Mirrored-process telemetry schemas (warmed from the peer's
        # manager.process_telemetry.schema.list).
        proc_schema_items: list[Json] = []
        if isinstance(proc_schema_resp, dict) and bool(proc_schema_resp.get("ok")):
            presult = proc_schema_resp.get("result")
            if isinstance(presult, dict) and isinstance(presult.get("processes"), list):
                proc_schema_items = [
                    item for item in presult.get("processes", []) if isinstance(item, dict)
                ]
        proc_schema_by_remote = {
            str(item.get("process_id", "")): item
            for item in proc_schema_items
            if str(item.get("process_id", ""))
        }
        for pmirror in self._process_mirrors.values():
            if pmirror.peer_id != peer_rt.config.peer_id:
                continue
            remote_schema = proc_schema_by_remote.get(pmirror.remote_process_id)
            if remote_schema is not None:
                pmirror.schema_entry = self._rewrite_process_schema_entry(
                    pmirror, remote_schema
                )

    def _rewrite_process_schema_entry(
        self, mirror: MirroredProcessRuntime, payload: Json
    ) -> Json:
        out = dict(payload)
        out["process_id"] = mirror.local_id
        out["source_kind"] = "process"
        out["is_remote"] = True
        out["owner_peer_id"] = mirror.peer_id
        out["remote_process_id"] = mirror.remote_process_id
        return out

    # ---- background warmup thread (own zmq context; never touches manager
    # ---- sockets or mirror/peer state) -----------------------------------

    def _warmup_loop(self) -> None:
        try:
            ctx = self._ensure_fed_ctx()
        except RuntimeError:
            # Hub was closed before this thread got going — exit cleanly.
            return
        peer_cfgs = [peer_rt.config for peer_rt in self._peers.values()]
        backoff = {
            cfg.peer_id: max(float(cfg.reconnect_backoff_s), 0.001)
            for cfg in peer_cfgs
        }
        next_attempt = {cfg.peer_id: 0.0 for cfg in peer_cfgs}
        # Runs until close(); a warmed peer is re-fetched every _METADATA_REFRESH_S
        # so config/schema drift after a peer restart is eventually picked up.
        while not self._warmup_stop.is_set():
            for cfg in peer_cfgs:
                if self._warmup_stop.is_set():
                    break
                if time.monotonic() < next_attempt[cfg.peer_id]:
                    continue
                config_resp, schema_resp, proc_schema_resp, caps, error = (
                    self._fetch_peer_metadata(ctx, cfg)
                )
                if error is None:
                    self._warmup_results.put(
                        (cfg.peer_id, config_resp, schema_resp, proc_schema_resp, caps, None)
                    )
                    next_attempt[cfg.peer_id] = time.monotonic() + _METADATA_REFRESH_S
                    backoff[cfg.peer_id] = max(float(cfg.reconnect_backoff_s), 0.001)
                else:
                    self._warmup_results.put(
                        (cfg.peer_id, None, None, None, {}, error)
                    )
                    next_attempt[cfg.peer_id] = time.monotonic() + backoff[cfg.peer_id]
                    backoff[cfg.peer_id] = min(
                        backoff[cfg.peer_id] * 2.0,
                        float(cfg.reconnect_backoff_max_s),
                    )
            self._warmup_stop.wait(0.2)

    def _fetch_peer_metadata(
        self, ctx: zmq.Context, cfg: FederationPeerConfig
    ) -> tuple[Json | None, Json | None, Json | None, dict[str, Json], str | None]:
        # Resolve the peer host to an IP HERE (on the warmup thread) and connect
        # only to the IP. Handing a hostname to connect() would make libzmq do a
        # synchronous DNS lookup on this context's I/O thread; an unresolvable
        # host would block it and delay every other peer's warmup. Returns a
        # final element: an error reason (None on success) surfaced as last_error.
        resolved = resolve_tcp_endpoint(cfg.router_rpc)
        if resolved is None:
            return None, None, None, {}, f"unresolvable peer host {cfg.router_rpc!r}"
        timeout_ms = int(cfg.metadata_rpc_timeout_ms)
        sock = connect_dealer(ctx, resolved, timeout_ms=timeout_ms)
        caps: dict[str, Json] = {}
        try:
            config_resp = self._dealer_rpc(
                sock, {"type": "device.config.list"}, timeout_ms=timeout_ms
            )
            schema_resp = self._dealer_rpc(
                sock, {"action": "manager.telemetry.schema.list"}, timeout_ms=timeout_ms
            )
            # Process telemetry schema is best-effort: only fetched/used when
            # this peer mirrors processes. A None result leaves process mirrors
            # on placeholder schema (no HDF datasets until warmed).
            proc_schema_resp: Json | None = None
            if cfg.mirror_processes:
                proc_schema_resp = self._dealer_rpc(
                    sock,
                    {"action": "manager.process_telemetry.schema.list"},
                    timeout_ms=timeout_ms,
                )
            if config_resp is None or schema_resp is None:
                return (
                    config_resp,
                    schema_resp,
                    proc_schema_resp,
                    caps,
                    "metadata fetch failed (peer unreachable)",
                )
            if cfg.warm_capabilities_on_startup:
                for mirror in cfg.mirror_devices:
                    if self._warmup_stop.is_set():
                        break
                    resp = self._dealer_rpc(
                        sock,
                        {
                            "type": "command",
                            "device_id": mirror.remote_device_id,
                            "action": "capabilities",
                            "params": {},
                        },
                        timeout_ms=timeout_ms,
                    )
                    if (
                        isinstance(resp, dict)
                        and bool(resp.get("ok"))
                        and isinstance(resp.get("result"), dict)
                    ):
                        caps[mirror.remote_device_id] = dict(resp["result"])
            return config_resp, schema_resp, proc_schema_resp, caps, None
        except Exception as exc:
            return None, None, None, caps, f"metadata fetch error: {exc}"
        finally:
            try:
                sock.close(0)
            except Exception:
                pass

    def _dealer_rpc(
        self, sock: zmq.Socket, payload: Json, *, timeout_ms: int
    ) -> Json | None:
        # Poll in short steps checking the stop event rather than one blocking
        # recv(timeout_ms), so close() can interrupt the warmup thread promptly
        # (otherwise join()+ctx.term() could hang for up to timeout_ms).
        try:
            send_json(sock, payload)
        except Exception:
            return None
        deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
        while not self._warmup_stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            step_ms = int(min(100.0, max(1.0, remaining * 1000.0)))
            try:
                if not sock.poll(step_ms, zmq.POLLIN):
                    continue
                resp = recv_json(sock)
            except Exception:
                return None
            return resp if isinstance(resp, dict) else None
        return None

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
        if topic == "manager.process_telemetry_update":
            self._relay_process_telemetry(peer_rt, payload)
            return
        if topic == "manager.process.heartbeat":
            self._relay_process_heartbeat(peer_rt, payload)
            return
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

    def _process_mirror_for_remote(
        self, peer_rt: PeerRuntime, remote_id: str
    ) -> MirroredProcessRuntime | None:
        """Find the mirrored-process runtime for a peer's remote process id."""
        for mirror in self._process_mirrors.values():
            if (
                mirror.peer_id == peer_rt.config.peer_id
                and mirror.remote_process_id == remote_id
            ):
                return mirror
        return None

    def _relay_process_telemetry(self, peer_rt: PeerRuntime, payload: Json) -> None:
        """Relay a peer's ``manager.process_telemetry_update`` for a mirrored
        process. Maps ``process_id`` remote->local, stamps process/origin meta
        (``is_remote: True`` so the hub's Influx writer skips it while HDF still
        records it), and re-publishes locally so the manager rebroadcasts it to
        the HDF writer and the sequencer's client cache."""
        remote_id = str(payload.get("process_id", "")).strip()
        if not remote_id:
            return
        mirror = self._process_mirror_for_remote(peer_rt, remote_id)
        if mirror is None:
            return
        out = dict(payload)
        out["process_id"] = mirror.local_id
        # `is_remote` is a federation trust-boundary flag: the hub's Influx
        # writer uses it to avoid double-writing federated data the owner
        # instance already records. Unlike devices (which carry this via a
        # separate device_config channel), a mirrored process has no such
        # channel, so stamp it UNCONDITIONALLY here (and overwrite any value a
        # peer may have spoofed). `include_origin_meta` only gates the
        # descriptive origin fields.
        out["is_remote"] = True
        out["source_kind"] = "process"
        if peer_rt.config.relay.include_origin_meta:
            out["owner_peer_id"] = peer_rt.config.peer_id
            out["remote_process_id"] = remote_id
        self._manager._publish_manager_event("manager.process_telemetry_update", out)

    def _relay_process_heartbeat(self, peer_rt: PeerRuntime, payload: Json) -> None:
        """Relay a peer's ``manager.process.heartbeat`` for a mirrored process.

        Stores the manager-side receive time + a trimmed payload on the mirror so
        ``list_processes_snapshot`` can derive real liveness (ONLINE/OFFLINE),
        mirroring the device-heartbeat path. Unlike a device it is NOT re-ingested
        into the manager (there is no local process handle for a mirror), and the
        owner's internal endpoints are dropped — only state/pid/metrics/timestamps
        are retained."""
        remote_id = str(payload.get("process_id", "")).strip()
        if not remote_id:
            return
        mirror = self._process_mirror_for_remote(peer_rt, remote_id)
        if mirror is None:
            return
        kept: Json = {
            k: payload[k]
            for k in ("state", "process_state", "pid", "metrics", "ts")
            if k in payload
        }
        kept["process_id"] = mirror.local_id
        kept["is_remote"] = True
        kept["source_kind"] = "process"
        if peer_rt.config.relay.include_origin_meta:
            kept["owner_peer_id"] = peer_rt.config.peer_id
            kept["remote_process_id"] = remote_id
        mirror.last_hb_payload = kept
        mirror.last_hb_recv_mono = time.monotonic()

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

    def _resolve_router_cached(self, peer_rt: PeerRuntime) -> str | None:
        # TTL cache (see PeerRuntime.resolved_endpoint) so a hostname peer
        # isn't getaddrinfo'd on every forwarded command (IP literals are an
        # instant passthrough either way). Re-resolves after the TTL, picking
        # up DNS drift; a None (unresolvable) result is cached too so a bad
        # host isn't re-probed per command. Only called while holding
        # peer_rt.rpc_lock.
        now = time.monotonic()
        if now < peer_rt.resolved_expiry_mono:
            return peer_rt.resolved_endpoint
        peer_rt.resolved_endpoint = resolve_tcp_endpoint(peer_rt.config.router_rpc)
        peer_rt.resolved_expiry_mono = now + _RESOLVE_CACHE_TTL_S
        return peer_rt.resolved_endpoint

    def _close_rpc_socket(self, peer_rt: PeerRuntime) -> None:
        sock = peer_rt.rpc_sock
        if sock is None:
            return
        try:
            sock.close(0)
        except Exception:
            pass
        peer_rt.rpc_sock = None
        peer_rt.rpc_sock_endpoint = None

    def _ensure_rpc_socket(self, peer_rt: PeerRuntime, resolved: str) -> zmq.Socket:
        # Persistent per-peer DEALER (F10: used to be opened/closed per call,
        # adding a TCP connect to every mirrored command). Reused across calls;
        # torn down and reconnected if the resolved endpoint changes or a call
        # fails/times out (see _rpc_call) -- a DEALER has no request/reply
        # correlation here, so a socket that may still have a late reply
        # in flight for a timed-out call must never be reused for the next one.
        if peer_rt.rpc_sock is not None and peer_rt.rpc_sock_endpoint == resolved:
            return peer_rt.rpc_sock
        self._close_rpc_socket(peer_rt)
        sock = connect_dealer(
            self._ensure_fed_ctx(), resolved, timeout_ms=int(peer_rt.config.rpc_timeout_ms)
        )
        peer_rt.rpc_sock = sock
        peer_rt.rpc_sock_endpoint = resolved
        return sock

    def _rpc_call(self, peer_rt: PeerRuntime, payload: Json) -> Json | None:
        # Callers: forward_device_request (F10: now runs on the manager's
        # lifecycle executor, not the poll loop -- see internal_rpc.py's
        # federation-forward dispatch) and forward_process_request (still on
        # the poll loop). rpc_lock serialises both against this peer's shared
        # persistent socket so they can never interleave send/recv on it.
        with peer_rt.rpc_lock:
            # Pre-resolve (cached) so an unresolvable peer host fails fast and
            # never makes libzmq block the I/O thread on DNS.
            resolved = self._resolve_router_cached(peer_rt)
            if resolved is None:
                peer_rt.last_error = f"unresolvable peer host {peer_rt.config.router_rpc!r}"
                return None
            timeout_ms = int(peer_rt.config.rpc_timeout_ms)
            try:
                sock = self._ensure_rpc_socket(peer_rt, resolved)
            except RuntimeError as e:  # hub closed during shutdown
                peer_rt.last_error = str(e)
                return None
            # Pump manager subscriptions while waiting so an unreachable peer
            # can't starve process heartbeats during the (up to rpc_timeout_ms)
            # wait -- a no-op off the main thread (_pump_manager_subscriptions
            # checks thread identity), which is the common case now that device
            # forwards run on the lifecycle executor; the poll loop keeps
            # pumping on its own in parallel. Falls back to a plain recv when
            # no pump is available (e.g. test stubs).
            pump = getattr(self._manager, "_pump_manager_subscriptions", None)
            try:
                if callable(pump):
                    from .._manager.rpc_calls import _blocking_call_with_pump

                    resp: Json | None = _blocking_call_with_pump(
                        sock,
                        json_dumps(payload),
                        timeout_ms=timeout_ms,
                        response_filter=lambda _r: True,
                        pump_fn=pump,
                    )
                else:
                    send_json(sock, payload)
                    resp = recv_json(sock)
            except Exception as e:
                peer_rt.last_error = str(e)
                # Don't reuse a socket that just failed/timed out -- a late
                # reply for this call could otherwise be misdelivered as the
                # response to a later, unrelated call on the same DEALER.
                self._close_rpc_socket(peer_rt)
                return None

            if not isinstance(resp, dict):
                # recv_json() (the no-pump fallback) swallows a timeout/ZMQError
                # into None instead of raising -- same "don't reuse" reasoning
                # as the exception branch above applies here too.
                peer_rt.last_error = "invalid response"
                self._close_rpc_socket(peer_rt)
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

