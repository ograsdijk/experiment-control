from __future__ import annotations

import ipaddress
import socket


def is_loopback_host(raw_host: str | None) -> bool:
    if not raw_host:
        return False
    host = str(raw_host).strip().lower().strip("[]")
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return bool(ipaddress.ip_address(host).is_loopback)
    except Exception:
        return False


def server_ipv4_candidates() -> list[str]:
    candidates: set[str] = set()
    try:
        _host, _aliases, ips = socket.gethostbyname_ex(socket.gethostname())
        for ip in ips:
            try:
                parsed = ipaddress.ip_address(ip)
            except Exception:
                continue
            if parsed.version == 4 and not parsed.is_loopback:
                candidates.add(str(parsed))
    except Exception:
        pass
    try:
        infos = socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
        for info in infos:
            ip = str(info[4][0])
            try:
                parsed = ipaddress.ip_address(ip)
            except Exception:
                continue
            if parsed.version == 4 and not parsed.is_loopback:
                candidates.add(str(parsed))
    except Exception:
        pass
    return sorted(candidates)


def first_non_loopback_ipv4() -> str | None:
    candidates = server_ipv4_candidates()
    if not candidates:
        return None
    return candidates[0]
