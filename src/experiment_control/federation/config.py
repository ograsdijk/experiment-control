from __future__ import annotations

import sys
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from ..utils.config_parsing import (
    ConfigError,
    normalize_list,
    optional_dict,
    require_dict,
    require_str,
)

Json = dict[str, Any]

DEFAULT_FEDERATION_RELAY_TOPICS: tuple[str, ...] = (
    "manager.telemetry_update",
    "manager.process_telemetry_update",
    "manager.heartbeat",
    # Process heartbeats give mirrored PROCESSES true liveness (symmetric with
    # device `manager.heartbeat`); without it a mirrored process has no health.
    "manager.process.heartbeat",
    "manager.log",
    "manager.command",
    "manager.command_interceptor.error",
    "manager.command_interceptor.modified",
)

_SUPPORTED_RELAY_TOPICS = set(DEFAULT_FEDERATION_RELAY_TOPICS)

# Per-attempt timeout for the best-effort peer metadata warmup
# (config/schema/capabilities), which runs on a background thread. Kept
# independent of and smaller than the device RPC timeout so a single warmup
# thread isn't hogged for the full device timeout by an unreachable peer (and
# so other, reachable peers warm promptly). Generous enough that a reachable
# peer's cheap manager RPC always completes. Live federated *commands* still
# use the peer's full ``rpc_timeout_ms``.
DEFAULT_FEDERATION_METADATA_RPC_TIMEOUT_MS = 1500


@dataclass(frozen=True)
class FederationRelayConfig:
    topics: tuple[str, ...] = DEFAULT_FEDERATION_RELAY_TOPICS
    only_mirrored_devices: bool = True
    include_origin_meta: bool = True


@dataclass(frozen=True)
class FederationPolicy:
    allow_device_actions: tuple[str, ...] = ("*",)
    deny_device_actions: tuple[str, ...] = ()
    allow_lifecycle_ops: bool = False
    allow_admin_ops: bool = False
    # Process-RPC ACL is a SEPARATE namespace from device actions: mirrored
    # devices default to wide-open ("*") but mirrored processes default to
    # deny-all (empty allowlist), so a federated process is never callable
    # across a peer unless an action is explicitly allowed.
    allow_process_actions: tuple[str, ...] = ()
    deny_process_actions: tuple[str, ...] = ()

    def allows_device_action(self, action: str) -> bool:
        return self._allows(
            action, self.deny_device_actions, self.allow_device_actions
        )

    def allows_process_action(self, action: str) -> bool:
        return self._allows(
            action, self.deny_process_actions, self.allow_process_actions
        )

    @staticmethod
    def _allows(
        action: str, deny: tuple[str, ...], allow: tuple[str, ...]
    ) -> bool:
        text = str(action or "").strip()
        if not text:
            return False
        for pattern in deny:
            if fnmatchcase(text, pattern):
                return False
        for pattern in allow:
            if fnmatchcase(text, pattern):
                return True
        return False


@dataclass(frozen=True)
class MirroredDeviceConfig:
    local_id: str
    remote_device_id: str


@dataclass(frozen=True)
class MirroredProcessConfig:
    local_id: str
    remote_process_id: str


@dataclass(frozen=True)
class FederationPeerConfig:
    peer_id: str
    router_rpc: str
    manager_pub: str
    mirror_devices: tuple[MirroredDeviceConfig, ...]
    rpc_timeout_ms: int
    event_stale_s: float
    reconnect_backoff_s: float
    reconnect_backoff_max_s: float
    mirror_processes: tuple[MirroredProcessConfig, ...] = ()
    metadata_rpc_timeout_ms: int = DEFAULT_FEDERATION_METADATA_RPC_TIMEOUT_MS
    warm_capabilities_on_startup: bool = False
    allow_reexport: bool = False
    policy: FederationPolicy = FederationPolicy()
    relay: FederationRelayConfig = FederationRelayConfig()


@dataclass(frozen=True)
class FederationConfig:
    enabled: bool = False
    peers: tuple[FederationPeerConfig, ...] = ()

    def mirrored_local_ids(self) -> tuple[str, ...]:
        """All mirrored local ids across devices AND processes.

        Both share one local-id namespace (a mirror local_id must be unique
        whether it names a federated device or a federated process), so this
        is the authoritative set for collision checks.
        """
        out: list[str] = []
        for peer in self.peers:
            for device in peer.mirror_devices:
                out.append(device.local_id)
            for process in peer.mirror_processes:
                out.append(process.local_id)
        return tuple(out)

    def mirrored_process_local_ids(self) -> tuple[str, ...]:
        out: list[str] = []
        for peer in self.peers:
            for process in peer.mirror_processes:
                out.append(process.local_id)
        return tuple(out)


def _bool_value(raw: object, *, path: list[str | int], default: bool) -> bool:
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    raise ConfigError(".".join(str(p) for p in path), "must be a boolean")


def _int_value(
    raw: object, *, path: list[str | int], default: int | None = None, minimum: int = 1
) -> int:
    if raw is None:
        if default is None:
            raise ConfigError(".".join(str(p) for p in path), "is required")
        return int(default)
    try:
        if not isinstance(raw, (str, bytes, bytearray, int)):
            raise TypeError
        value = int(raw)
    except Exception:
        raise ConfigError(".".join(str(p) for p in path), "must be an integer") from None
    if value < minimum:
        raise ConfigError(
            ".".join(str(p) for p in path), f"must be >= {int(minimum)}"
        )
    return value


def _float_value(
    raw: object,
    *,
    path: list[str | int],
    default: float | None = None,
    minimum: float = 0.0,
) -> float:
    if raw is None:
        if default is None:
            raise ConfigError(".".join(str(p) for p in path), "is required")
        return float(default)
    try:
        if not isinstance(raw, (str, bytes, bytearray, int, float)):
            raise TypeError
        value = float(raw)
    except Exception:
        raise ConfigError(".".join(str(p) for p in path), "must be a number") from None
    if value < minimum:
        raise ConfigError(
            ".".join(str(p) for p in path), f"must be >= {float(minimum)}"
        )
    return value


def _pattern_list(raw: object, *, path: list[str | int], default: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return tuple(default)
    items = normalize_list(raw, path=path)
    out: list[str] = []
    for idx, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(
                ".".join(str(p) for p in [*path, idx]), "must be a non-empty string"
            )
        out.append(item.strip())
    return tuple(out)


_PLACEHOLDER_HOST_MARKERS = ("TODO", "CHANGEME", "CHANGE_ME", "<", "X.X.X.X", "YOUR-HOST")


def _warn_if_placeholder_endpoint(endpoint: str, *, peer_id: str, field: str) -> None:
    """Warn (never fail) when a peer endpoint host looks like an unfilled
    placeholder (e.g. ``TODO_BRISTOL_WAVEMETER_HOST``). The stack still starts;
    the peer is tolerated and skipped at runtime (its host won't resolve), but
    the operator gets a loud, early signal instead of debugging a silent gap.
    """
    upper = endpoint.upper()
    if any(marker in upper for marker in _PLACEHOLDER_HOST_MARKERS):
        sys.stderr.write(
            f"[federation] warning: peer {peer_id!r} {field} {endpoint!r} looks like "
            "an unfilled placeholder; this peer will be skipped until its host is set.\n"
        )


def _validate_tcp_endpoint(value: str, *, path: list[str | int]) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConfigError(".".join(str(p) for p in path), "must be a non-empty string")
    if not text.startswith("tcp://"):
        raise ConfigError(
            ".".join(str(p) for p in path), "must be a tcp:// endpoint"
        )
    return text


def parse_federation_config(
    raw: object,
    *,
    local_device_ids: set[str] | None,
    manager_raw: Json | None,
) -> FederationConfig:
    if raw is None:
        return FederationConfig(enabled=False, peers=())

    root = require_dict(raw, path=["federation"])
    enabled = _bool_value(root.get("enabled"), path=["federation", "enabled"], default=True)
    if not enabled:
        return FederationConfig(enabled=False, peers=())

    manager_raw = manager_raw or {}
    default_rpc_timeout_ms = _int_value(
        manager_raw.get("device_rpc_timeout_ms"),
        path=["manager", "device_rpc_timeout_ms"],
        default=1500,
    )
    default_event_stale_s = _float_value(
        manager_raw.get("heartbeat_timeout_s"),
        path=["manager", "heartbeat_timeout_s"],
        default=3.0,
        minimum=0.001,
    )
    default_metadata_rpc_timeout_ms = _int_value(
        manager_raw.get("federation_metadata_rpc_timeout_ms"),
        path=["manager", "federation_metadata_rpc_timeout_ms"],
        default=DEFAULT_FEDERATION_METADATA_RPC_TIMEOUT_MS,
    )

    peer_items = normalize_list(root.get("peers"), path=["federation", "peers"])
    if not peer_items:
        raise ConfigError("federation.peers", "must contain at least one peer")

    peers: list[FederationPeerConfig] = []
    peer_ids: set[str] = set()
    local_ids_seen: set[str] = set()
    remote_ids_seen: set[tuple[str, str]] = set()
    local_device_ids = set(local_device_ids or set())

    for idx, item in enumerate(peer_items):
        peer_raw = require_dict(item, path=["federation", "peers", idx])
        peer_id = require_str(peer_raw.get("peer_id"), path=["federation", "peers", idx, "peer_id"]).strip()
        if peer_id in peer_ids:
            raise ConfigError(
                f"federation.peers[{idx}].peer_id", f"duplicate peer_id {peer_id!r}"
            )
        peer_ids.add(peer_id)

        router_rpc = _validate_tcp_endpoint(
            require_str(
                peer_raw.get("router_rpc"),
                path=["federation", "peers", idx, "router_rpc"],
            ),
            path=["federation", "peers", idx, "router_rpc"],
        )
        manager_pub = _validate_tcp_endpoint(
            require_str(
                peer_raw.get("manager_pub"),
                path=["federation", "peers", idx, "manager_pub"],
            ),
            path=["federation", "peers", idx, "manager_pub"],
        )
        _warn_if_placeholder_endpoint(router_rpc, peer_id=peer_id, field="router_rpc")
        _warn_if_placeholder_endpoint(manager_pub, peer_id=peer_id, field="manager_pub")

        allow_reexport = _bool_value(
            peer_raw.get("allow_reexport"),
            path=["federation", "peers", idx, "allow_reexport"],
            default=False,
        )
        if allow_reexport:
            raise ConfigError(
                f"federation.peers[{idx}].allow_reexport",
                "must be false in v1 federation",
            )

        mirror_items = normalize_list(
            peer_raw.get("mirror_devices"),
            path=["federation", "peers", idx, "mirror_devices"],
        )
        mirrors: list[MirroredDeviceConfig] = []
        for m_idx, mirror_item in enumerate(mirror_items):
            mirror_raw = require_dict(
                mirror_item,
                path=["federation", "peers", idx, "mirror_devices", m_idx],
            )
            local_id = require_str(
                mirror_raw.get("local_id"),
                path=["federation", "peers", idx, "mirror_devices", m_idx, "local_id"],
            ).strip()
            remote_device_id = require_str(
                mirror_raw.get("remote_device_id"),
                path=[
                    "federation",
                    "peers",
                    idx,
                    "mirror_devices",
                    m_idx,
                    "remote_device_id",
                ],
            ).strip()
            if local_id in local_ids_seen:
                raise ConfigError(
                    f"federation.peers[{idx}].mirror_devices[{m_idx}].local_id",
                    f"duplicate mirrored local_id {local_id!r}",
                )
            if local_id in local_device_ids:
                raise ConfigError(
                    f"federation.peers[{idx}].mirror_devices[{m_idx}].local_id",
                    f"collides with local device_id {local_id!r}",
                )
            remote_key = (peer_id, "device:" + remote_device_id)
            if remote_key in remote_ids_seen:
                raise ConfigError(
                    (
                        "federation.peers"
                        f"[{idx}].mirror_devices[{m_idx}].remote_device_id"
                    ),
                    (
                        "duplicate mirrored remote device mapping for "
                        f"{peer_id!r}:{remote_device_id!r}"
                    ),
                )
            local_ids_seen.add(local_id)
            remote_ids_seen.add(remote_key)
            mirrors.append(
                MirroredDeviceConfig(local_id=local_id, remote_device_id=remote_device_id)
            )

        process_items = normalize_list(
            peer_raw.get("mirror_processes"),
            path=["federation", "peers", idx, "mirror_processes"],
        )
        process_mirrors: list[MirroredProcessConfig] = []
        for p_idx, process_item in enumerate(process_items):
            process_raw = require_dict(
                process_item,
                path=["federation", "peers", idx, "mirror_processes", p_idx],
            )
            local_id = require_str(
                process_raw.get("local_id"),
                path=["federation", "peers", idx, "mirror_processes", p_idx, "local_id"],
            ).strip()
            remote_process_id = require_str(
                process_raw.get("remote_process_id"),
                path=[
                    "federation",
                    "peers",
                    idx,
                    "mirror_processes",
                    p_idx,
                    "remote_process_id",
                ],
            ).strip()
            # Process and device mirrors share ONE local-id namespace.
            if local_id in local_ids_seen:
                raise ConfigError(
                    f"federation.peers[{idx}].mirror_processes[{p_idx}].local_id",
                    f"duplicate mirrored local_id {local_id!r}",
                )
            if local_id in local_device_ids:
                raise ConfigError(
                    f"federation.peers[{idx}].mirror_processes[{p_idx}].local_id",
                    f"collides with local device_id {local_id!r}",
                )
            remote_key = (peer_id, "process:" + remote_process_id)
            if remote_key in remote_ids_seen:
                raise ConfigError(
                    (
                        "federation.peers"
                        f"[{idx}].mirror_processes[{p_idx}].remote_process_id"
                    ),
                    (
                        "duplicate mirrored remote process mapping for "
                        f"{peer_id!r}:{remote_process_id!r}"
                    ),
                )
            local_ids_seen.add(local_id)
            remote_ids_seen.add(remote_key)
            process_mirrors.append(
                MirroredProcessConfig(
                    local_id=local_id, remote_process_id=remote_process_id
                )
            )

        if not mirrors and not process_mirrors:
            raise ConfigError(
                f"federation.peers[{idx}]",
                "must contain at least one mirror_devices or mirror_processes entry",
            )

        policy_raw = optional_dict(
            peer_raw.get("policy"),
            path=["federation", "peers", idx, "policy"],
        )
        relay_raw = optional_dict(
            peer_raw.get("relay"),
            path=["federation", "peers", idx, "relay"],
        )

        relay_topics = _pattern_list(
            relay_raw.get("topics"),
            path=["federation", "peers", idx, "relay", "topics"],
            default=DEFAULT_FEDERATION_RELAY_TOPICS,
        )
        unsupported = [topic for topic in relay_topics if topic not in _SUPPORTED_RELAY_TOPICS]
        if unsupported:
            raise ConfigError(
                f"federation.peers[{idx}].relay.topics",
                f"unsupported relay topic(s): {unsupported}",
            )

        reconnect_backoff_s = _float_value(
            peer_raw.get("reconnect_backoff_s"),
            path=["federation", "peers", idx, "reconnect_backoff_s"],
            default=0.5,
            minimum=0.001,
        )
        reconnect_backoff_max_s = _float_value(
            peer_raw.get("reconnect_backoff_max_s"),
            path=["federation", "peers", idx, "reconnect_backoff_max_s"],
            default=10.0,
            minimum=0.001,
        )
        if reconnect_backoff_max_s < reconnect_backoff_s:
            raise ConfigError(
                f"federation.peers[{idx}].reconnect_backoff_max_s",
                "must be >= reconnect_backoff_s",
            )

        peers.append(
            FederationPeerConfig(
                peer_id=peer_id,
                router_rpc=router_rpc,
                manager_pub=manager_pub,
                mirror_devices=tuple(mirrors),
                mirror_processes=tuple(process_mirrors),
                rpc_timeout_ms=_int_value(
                    peer_raw.get("rpc_timeout_ms"),
                    path=["federation", "peers", idx, "rpc_timeout_ms"],
                    default=default_rpc_timeout_ms,
                ),
                event_stale_s=_float_value(
                    peer_raw.get("event_stale_s"),
                    path=["federation", "peers", idx, "event_stale_s"],
                    default=default_event_stale_s,
                    minimum=0.001,
                ),
                reconnect_backoff_s=reconnect_backoff_s,
                reconnect_backoff_max_s=reconnect_backoff_max_s,
                metadata_rpc_timeout_ms=_int_value(
                    peer_raw.get("metadata_rpc_timeout_ms"),
                    path=["federation", "peers", idx, "metadata_rpc_timeout_ms"],
                    default=default_metadata_rpc_timeout_ms,
                ),
                warm_capabilities_on_startup=_bool_value(
                    peer_raw.get("warm_capabilities_on_startup"),
                    path=[
                        "federation",
                        "peers",
                        idx,
                        "warm_capabilities_on_startup",
                    ],
                    default=False,
                ),
                allow_reexport=False,
                policy=FederationPolicy(
                    allow_device_actions=_pattern_list(
                        policy_raw.get("allow_device_actions"),
                        path=["federation", "peers", idx, "policy", "allow_device_actions"],
                        default=("*",),
                    ),
                    deny_device_actions=_pattern_list(
                        policy_raw.get("deny_device_actions"),
                        path=["federation", "peers", idx, "policy", "deny_device_actions"],
                        default=(),
                    ),
                    allow_process_actions=_pattern_list(
                        policy_raw.get("allow_process_actions"),
                        path=["federation", "peers", idx, "policy", "allow_process_actions"],
                        default=(),
                    ),
                    deny_process_actions=_pattern_list(
                        policy_raw.get("deny_process_actions"),
                        path=["federation", "peers", idx, "policy", "deny_process_actions"],
                        default=(),
                    ),
                    allow_lifecycle_ops=_bool_value(
                        policy_raw.get("allow_lifecycle_ops"),
                        path=["federation", "peers", idx, "policy", "allow_lifecycle_ops"],
                        default=False,
                    ),
                    allow_admin_ops=_bool_value(
                        policy_raw.get("allow_admin_ops"),
                        path=["federation", "peers", idx, "policy", "allow_admin_ops"],
                        default=False,
                    ),
                ),
                relay=FederationRelayConfig(
                    topics=relay_topics,
                    only_mirrored_devices=_bool_value(
                        relay_raw.get("only_mirrored_devices"),
                        path=["federation", "peers", idx, "relay", "only_mirrored_devices"],
                        default=True,
                    ),
                    include_origin_meta=_bool_value(
                        relay_raw.get("include_origin_meta"),
                        path=["federation", "peers", idx, "relay", "include_origin_meta"],
                        default=True,
                    ),
                ),
            )
        )

    return FederationConfig(enabled=True, peers=tuple(peers))
