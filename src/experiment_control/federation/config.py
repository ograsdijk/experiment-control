from __future__ import annotations

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
    "manager.heartbeat",
    "manager.log",
    "manager.command",
    "manager.command_interceptor.error",
    "manager.command_interceptor.modified",
)

_SUPPORTED_RELAY_TOPICS = set(DEFAULT_FEDERATION_RELAY_TOPICS)


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

    def allows_device_action(self, action: str) -> bool:
        text = str(action or "").strip()
        if not text:
            return False
        for pattern in self.deny_device_actions:
            if fnmatchcase(text, pattern):
                return False
        for pattern in self.allow_device_actions:
            if fnmatchcase(text, pattern):
                return True
        return False


@dataclass(frozen=True)
class MirroredDeviceConfig:
    local_id: str
    remote_device_id: str


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
    warm_capabilities_on_startup: bool = False
    allow_reexport: bool = False
    policy: FederationPolicy = FederationPolicy()
    relay: FederationRelayConfig = FederationRelayConfig()


@dataclass(frozen=True)
class FederationConfig:
    enabled: bool = False
    peers: tuple[FederationPeerConfig, ...] = ()

    def mirrored_local_ids(self) -> tuple[str, ...]:
        out: list[str] = []
        for peer in self.peers:
            for device in peer.mirror_devices:
                out.append(device.local_id)
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
        if not mirror_items:
            raise ConfigError(
                f"federation.peers[{idx}].mirror_devices",
                "must contain at least one mirrored device",
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
            remote_key = (peer_id, remote_device_id)
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
