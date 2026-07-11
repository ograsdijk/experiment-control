from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

import zmq

from .config_parsing import ConfigError, optional_dict
from .network_hosts import (
    first_non_loopback_ipv4 as shared_first_non_loopback_ipv4,
    is_loopback_host as shared_is_loopback_host,
)

Json = dict[str, Any]


@dataclass(frozen=True)
class ManagerNetworkConfig:
    registry_bind: str
    internal_rpc_bind: str
    process_hb_bind_base: str
    process_data_bind_base: str
    external_rpc_bind: str
    external_pub_bind: str
    local_rpc_connect: str
    local_pub_connect: str
    public_rpc_hint: str
    public_pub_hint: str
    bind_host: str
    advertise_host: str | None
    internal_bind_host: str
    external_rpc_port: int
    external_pub_port: int


def _clean_host(raw: object, *, default: str) -> str:
    if raw is None:
        return default
    text = str(raw).strip()
    if not text:
        return default
    return text


def _clean_optional_host(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _is_wildcard_host(host: str) -> bool:
    text = str(host or "").strip().lower().strip("[]")
    return text in {"*", "0.0.0.0", "::"}


def _is_loopback_host(host: str) -> bool:
    return bool(shared_is_loopback_host(host))


def _format_host(host: str) -> str:
    text = str(host or "").strip()
    if not text:
        return "127.0.0.1"
    if text.startswith("[") and text.endswith("]"):
        return text
    if ":" in text and text not in {"*", "0.0.0.0"}:
        return f"[{text}]"
    return text


def _build_tcp_endpoint(host: str, port: int) -> str:
    return f"tcp://{_format_host(host)}:{int(port)}"


def _parse_tcp_host_port(endpoint: str) -> tuple[str, int] | None:
    text = str(endpoint or "").strip()
    if not text.startswith("tcp://"):
        return None
    hostport = text[len("tcp://") :]
    host = ""
    port_text = ""
    if hostport.startswith("["):
        end = hostport.find("]")
        if end < 0:
            return None
        host = hostport[1:end]
        tail = hostport[end + 1 :]
        if not tail.startswith(":"):
            return None
        port_text = tail[1:]
    else:
        if ":" not in hostport:
            return None
        host, port_text = hostport.rsplit(":", 1)
    if not port_text:
        return None
    try:
        port = int(str(port_text).strip())
    except Exception:
        return None
    if port <= 0 or port > 65535:
        return None
    return host, port


def resolve_tcp_endpoint(endpoint: str) -> str | None:
    """Resolve a ``tcp://host:port`` endpoint's hostname to an IP literal.

    Returns ``tcp://<ip>:<port>`` (the endpoint unchanged if the host is already
    an IP literal or a wildcard), or ``None`` if the host is empty or cannot be
    resolved.

    IMPORTANT: call this only from a non-heartbeat-critical thread. libzmq
    resolves a hostname handed to ``connect()`` synchronously on the context's
    I/O thread; an unresolvable host blocks that I/O thread (and any heartbeat
    socket sharing it), cascading processes into ``heartbeat_stale``. Resolving
    here, off that thread, and connecting only to the returned IP keeps DNS off
    every zmq I/O thread, so one bad peer host can never affect the local stack
    or other peers.
    """
    parsed = _parse_tcp_host_port(endpoint)
    if parsed is None:
        return None
    host, port = parsed
    text = str(host).strip().strip("[]")
    if _is_wildcard_host(text):
        return endpoint
    if not text:
        return None  # 'tcp://:port' is not a connectable endpoint
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, text)
            return _build_tcp_endpoint(text, port)  # already an IP literal
        except OSError:
            pass
    # Prefer IPv4: the stack binds IPv4 (0.0.0.0 / IPv4 advertise hosts), so a
    # hostname that resolves to both must not be pinned to an IPv6 address the
    # peer isn't listening on. Fall back to IPv6 only if there is no A record.
    # (Single-address pin, no Happy-Eyeballs, is intentional for this IPv4 stack.)
    # getaddrinfo raises UnicodeError (NOT an OSError) for malformed hosts
    # (over-long labels, non-ASCII, ``a..b``); treat those as unresolvable too,
    # never let them escape onto the warmup thread / poll loop.
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(text, port, family=family, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            continue
        for info in infos:
            ip = str(info[4][0])
            if ip:
                return _build_tcp_endpoint(ip, int(port))
    return None


def connect_dealer(ctx: zmq.Context, resolved_endpoint: str, *, timeout_ms: int) -> zmq.Socket:
    """Create a DEALER and connect it to an ALREADY-RESOLVED endpoint.

    Centralises the peer-socket boilerplate (LINGER=0, RCV/SND timeouts, connect)
    so the "never hand a hostname to connect()" invariant lives in one place:
    callers must pass an IP endpoint from ``resolve_tcp_endpoint`` (handling its
    ``None`` themselves), never a raw hostname.
    """
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, int(timeout_ms))
    sock.setsockopt(zmq.SNDTIMEO, int(timeout_ms))
    sock.connect(resolved_endpoint)
    return sock


def _parse_port(raw: object, *, default: int, path: list[str | int]) -> int:
    if raw is None:
        return int(default)
    try:
        if isinstance(raw, str):
            text = raw.strip().replace("_", "")
            if not text:
                return int(default)
            port = int(text)
        elif isinstance(raw, int):
            port = int(raw)
        else:
            raise TypeError
    except Exception:
        raise ConfigError(".".join(str(p) for p in path), "must be an integer") from None
    if port <= 0 or port > 65535:
        raise ConfigError(
            ".".join(str(p) for p in path),
            "must be in range 1..65535",
        )
    return int(port)


def _port_from_endpoint(raw: object, default: int) -> int:
    if not isinstance(raw, str):
        return int(default)
    parsed = _parse_tcp_host_port(raw)
    if parsed is None:
        return int(default)
    _host, port = parsed
    return int(port)


def _endpoint_or_default(raw: object, *, host: str, port: int) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _build_tcp_endpoint(host, port)


def derive_local_connect_endpoint(bind_endpoint: str, default_port: int) -> str:
    text = str(bind_endpoint or "").strip()
    if not text:
        return _build_tcp_endpoint("127.0.0.1", int(default_port))
    parsed = _parse_tcp_host_port(text)
    if parsed is None:
        return text
    _host, port = parsed
    return _build_tcp_endpoint("127.0.0.1", int(port))


def _first_non_loopback_ip() -> str | None:
    return shared_first_non_loopback_ipv4()


def _resolve_public_host(*, bind_host: str, advertise_host: str | None) -> str:
    if advertise_host:
        return advertise_host
    if bind_host and not _is_wildcard_host(bind_host) and not _is_loopback_host(bind_host):
        return bind_host
    discovered = _first_non_loopback_ip()
    if discovered:
        return discovered
    if bind_host and not _is_wildcard_host(bind_host):
        return bind_host
    return "127.0.0.1"


def resolve_manager_network(manager_raw: Json) -> ManagerNetworkConfig:
    external_raw = optional_dict(manager_raw.get("external"), path=["manager", "external"])
    local_connect_raw = optional_dict(
        manager_raw.get("local_connect"), path=["manager", "local_connect"]
    )
    internal_ports_raw = optional_dict(
        manager_raw.get("internal_ports"), path=["manager", "internal_ports"]
    )

    bind_host = _clean_host(
        external_raw.get("bind_host", manager_raw.get("bind_host")),
        default="127.0.0.1",
    )
    advertise_host = _clean_optional_host(
        external_raw.get("advertise_host", manager_raw.get("advertise_host"))
    )
    internal_bind_host = _clean_host(
        manager_raw.get("internal_bind_host"),
        default="127.0.0.1",
    )

    old_external_rpc_bind = manager_raw.get("external_rpc_bind")
    old_external_pub_bind = manager_raw.get("external_pub_bind")
    old_registry_bind = manager_raw.get("registry_bind")
    old_internal_rpc_bind = manager_raw.get("internal_rpc_bind")
    old_process_hb_bind_base = manager_raw.get("process_hb_bind_base")
    old_process_data_bind_base = manager_raw.get("process_data_bind_base")

    external_rpc_port = _parse_port(
        external_raw.get("rpc_port"),
        default=_port_from_endpoint(old_external_rpc_bind, 6000),
        path=["manager", "external", "rpc_port"],
    )
    external_pub_port = _parse_port(
        external_raw.get("pub_port"),
        default=_port_from_endpoint(old_external_pub_bind, 6001),
        path=["manager", "external", "pub_port"],
    )
    registry_port = _parse_port(
        internal_ports_raw.get("registry"),
        default=_port_from_endpoint(old_registry_bind, 5555),
        path=["manager", "internal_ports", "registry"],
    )
    internal_rpc_port = _parse_port(
        internal_ports_raw.get("rpc"),
        default=_port_from_endpoint(old_internal_rpc_bind, 6002),
        path=["manager", "internal_ports", "rpc"],
    )
    process_hb_port = _parse_port(
        internal_ports_raw.get("heartbeat_base"),
        default=_port_from_endpoint(old_process_hb_bind_base, 6100),
        path=["manager", "internal_ports", "heartbeat_base"],
    )
    process_data_port = _parse_port(
        internal_ports_raw.get(
            "event_base", internal_ports_raw.get("process_data_base")
        ),
        default=_port_from_endpoint(old_process_data_bind_base, 6200),
        path=["manager", "internal_ports", "event_base"],
    )

    external_rpc_bind = _endpoint_or_default(
        old_external_rpc_bind, host=bind_host, port=external_rpc_port
    )
    external_pub_bind = _endpoint_or_default(
        old_external_pub_bind, host=bind_host, port=external_pub_port
    )
    registry_bind = _endpoint_or_default(
        old_registry_bind, host=internal_bind_host, port=registry_port
    )
    internal_rpc_bind = _endpoint_or_default(
        old_internal_rpc_bind, host=internal_bind_host, port=internal_rpc_port
    )
    process_hb_bind_base = _endpoint_or_default(
        old_process_hb_bind_base, host=internal_bind_host, port=process_hb_port
    )
    process_data_bind_base = _endpoint_or_default(
        old_process_data_bind_base, host=internal_bind_host, port=process_data_port
    )

    local_rpc_override = local_connect_raw.get(
        "rpc", manager_raw.get("external_rpc_connect_local")
    )
    local_pub_override = local_connect_raw.get(
        "pub", manager_raw.get("external_pub_connect_local")
    )
    local_rpc_connect = (
        str(local_rpc_override).strip()
        if isinstance(local_rpc_override, str) and str(local_rpc_override).strip()
        else derive_local_connect_endpoint(external_rpc_bind, external_rpc_port)
    )
    local_pub_connect = (
        str(local_pub_override).strip()
        if isinstance(local_pub_override, str) and str(local_pub_override).strip()
        else derive_local_connect_endpoint(external_pub_bind, external_pub_port)
    )

    effective_bind_host = bind_host
    parsed_external_rpc = _parse_tcp_host_port(external_rpc_bind)
    if parsed_external_rpc is not None:
        parsed_host, _parsed_port = parsed_external_rpc
        if parsed_host:
            effective_bind_host = parsed_host
    public_host = _resolve_public_host(
        bind_host=effective_bind_host, advertise_host=advertise_host
    )
    public_rpc_hint = (
        _build_tcp_endpoint(public_host, external_rpc_port)
        if _parse_tcp_host_port(external_rpc_bind) is not None
        else external_rpc_bind
    )
    public_pub_hint = (
        _build_tcp_endpoint(public_host, external_pub_port)
        if _parse_tcp_host_port(external_pub_bind) is not None
        else external_pub_bind
    )

    return ManagerNetworkConfig(
        registry_bind=registry_bind,
        internal_rpc_bind=internal_rpc_bind,
        process_hb_bind_base=process_hb_bind_base,
        process_data_bind_base=process_data_bind_base,
        external_rpc_bind=external_rpc_bind,
        external_pub_bind=external_pub_bind,
        local_rpc_connect=local_rpc_connect,
        local_pub_connect=local_pub_connect,
        public_rpc_hint=public_rpc_hint,
        public_pub_hint=public_pub_hint,
        bind_host=bind_host,
        advertise_host=advertise_host,
        internal_bind_host=internal_bind_host,
        external_rpc_port=external_rpc_port,
        external_pub_port=external_pub_port,
    )
