"""Measure inter-host clock skew (and round-trip latency) to federation peers.

Federated telemetry carries the *source* host's ``t_wall`` verbatim, so if two
hosts' wall clocks disagree, cross-host time alignment of federated data is off
by that skew. A one-way wall difference can't separate skew from transport
latency (``t_wall_recv - t_wall = skew + latency`` — one equation, two unknowns).
This probe closes that gap with a round-trip (the NTP algorithm):

    T1 = local wall at send;  m1 = local monotonic at send
    ... call manager.info.ping on the peer -> result.t_wall = T_peer ...
    m2 = local monotonic at recv

    RTT  = m2 - m1                      (timed on the LOCAL monotonic clock)
    skew = T_peer - (T1 + RTT/2)        (peer minus local; + => peer ahead)
    one_way ~= RTT / 2

The peer's monotonic clock is never used: it is a different per-machine epoch and
is not comparable across hosts. The probe reports the skew from the sample with
the smallest RTT (NTP minimum-filter; least queuing noise), plus the spread.

IMPORTANT: run this ON THE CONSUMING/HUB HOST. Its wall clock is the reference
the skew is measured against (the same clock the HDF writer stamps into
``t_wall_recv``). Pair the reported skew with the persisted ``t_wall_recv`` column
to recover per-sample one-way latency offline:
``one_way_latency = (t_wall_recv - t_wall) - skew``.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
import zmq

from ..federation.config import parse_federation_config


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        "experiment_control clock skew probe",
        description=(
            "Round-trip clock-skew/RTT probe to federation peers. "
            "Run on the consuming/hub host."
        ),
    )
    parser.add_argument(
        "--stack",
        default="stack.yaml",
        help="Path to the consuming instance's stack.yaml (for peer discovery). "
        "Default: ./stack.yaml. Ignored when --peer is given.",
    )
    parser.add_argument(
        "--peer",
        action="append",
        default=[],
        metavar="NAME=tcp://HOST:PORT",
        help="Explicit peer router_rpc endpoint, repeatable. Overrides --stack. "
        "The NAME= prefix is optional (defaults to the endpoint).",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=20,
        help="Round-trip samples per peer (default 20).",
    )
    parser.add_argument(
        "--interval-s",
        type=float,
        default=0.2,
        help="Delay between samples in seconds (default 0.2).",
    )
    parser.add_argument(
        "--rpc-timeout-ms",
        type=int,
        default=2000,
        help="Per-call timeout in ms (default 2000).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a text report.",
    )
    return parser.parse_args(argv)


def _parse_peer_arg(spec: str) -> tuple[str, str]:
    """``name=endpoint`` -> (name, endpoint); bare ``endpoint`` -> (endpoint, endpoint)."""
    if "=" in spec:
        name, _, endpoint = spec.partition("=")
        name = name.strip()
        endpoint = endpoint.strip()
        if name and endpoint:
            return name, endpoint
    spec = spec.strip()
    return spec, spec


def _discover_peers(ns: argparse.Namespace) -> list[tuple[str, str]]:
    """Return [(peer_id, router_rpc), ...] from --peer overrides or stack.yaml."""
    if ns.peer:
        return [_parse_peer_arg(p) for p in ns.peer]
    stack_path = Path(ns.stack)
    raw = yaml.safe_load(stack_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []
    cfg = parse_federation_config(
        raw.get("federation"), local_device_ids=set(), manager_raw={}
    )
    if not cfg.enabled:
        return []
    return [(peer.peer_id, peer.router_rpc) for peer in cfg.peers]


def _measure_peer(
    ctx: zmq.Context,
    peer_id: str,
    router_rpc: str,
    *,
    samples: int,
    rpc_timeout_ms: int,
    interval_s: float,
) -> dict:
    """Round-trip ``manager.info.ping`` ``samples`` times; summarize skew + RTT.

    A fresh DEALER per peer (via ManagerClient) keeps reply correlation simple
    and isolates an unreachable peer from the others.
    """
    # Imported lazily (not at module top) so peer discovery and --help stay
    # cheap and don't import manager_client's transitive deps. ManagerClient
    # owns its own socket lifecycle.
    from ..manager_client import ManagerClient

    try:
        client = ManagerClient(
            ctx=ctx,
            manager_rpc=router_rpc,
            manager_pub="",
            rpc_timeout_ms=rpc_timeout_ms,
            subscribe_telemetry=False,
        )
    except Exception as exc:
        # A malformed endpoint makes zmq connect() raise synchronously here.
        # Report this peer unreachable rather than aborting the whole run.
        return {
            "peer_id": peer_id,
            "router_rpc": router_rpc,
            "samples_ok": 0,
            "samples_failed": max(1, samples),
            "reachable": False,
            "error": f"connect failed: {exc}",
        }
    pairs: list[tuple[float, float]] = []  # (rtt_s, skew_s)
    errors = 0
    try:
        for i in range(max(1, samples)):
            if i:
                time.sleep(max(0.0, interval_s))
            m1 = time.monotonic()
            t1 = time.time()
            try:
                resp = client.call({"type": "manager.info.ping"})
            except Exception:
                errors += 1
                continue
            m2 = time.monotonic()
            if not isinstance(resp, dict) or not resp.get("ok"):
                errors += 1
                continue
            result = resp.get("result")
            t_peer = result.get("t_wall") if isinstance(result, dict) else None
            # bool is an int subclass; exclude it, and reject non-finite values
            # so a bad peer can't poison the skew or emit invalid JSON (--json).
            if (
                not isinstance(t_peer, (int, float))
                or isinstance(t_peer, bool)
                or not math.isfinite(t_peer)
            ):
                errors += 1
                continue
            rtt = m2 - m1
            skew = float(t_peer) - (t1 + rtt / 2.0)
            pairs.append((rtt, skew))
    finally:
        client.close()

    summary: dict = {
        "peer_id": peer_id,
        "router_rpc": router_rpc,
        "samples_ok": len(pairs),
        "samples_failed": errors,
    }
    if not pairs:
        summary["reachable"] = False
        return summary

    rtts = [r for r, _ in pairs]
    skews = [s for _, s in pairs]
    # NTP minimum-filter: the sample with the smallest RTT has the least
    # queuing noise, so its skew is the best point estimate.
    best_rtt, best_skew = min(pairs, key=lambda p: p[0])
    summary.update(
        {
            "reachable": True,
            "skew_s": best_skew,
            "rtt_s": best_rtt,
            "one_way_s": best_rtt / 2.0,
            "skew_min_s": min(skews),
            "skew_median_s": statistics.median(skews),
            "skew_max_s": max(skews),
            "rtt_min_s": min(rtts),
            "rtt_median_s": statistics.median(rtts),
            "rtt_max_s": max(rtts),
        }
    )
    return summary


def _format_peer(s: dict) -> str:
    head = f"peer={s['peer_id']!r} router_rpc={s['router_rpc']!r}"
    if not s.get("reachable"):
        err = s.get("error")
        suffix = f" ({err})" if err else ""
        return (
            f"{head} UNREACHABLE "
            f"(ok={s['samples_ok']} failed={s['samples_failed']}){suffix}"
        )
    ms = 1000.0
    return (
        f"{head}\n"
        f"    skew   = {s['skew_s'] * ms:+.3f} ms  "
        f"(best @ min RTT; median {s['skew_median_s'] * ms:+.3f}, "
        f"range [{s['skew_min_s'] * ms:+.3f}, {s['skew_max_s'] * ms:+.3f}])\n"
        f"    RTT    = {s['rtt_s'] * ms:.3f} ms  "
        f"(min; median {s['rtt_median_s'] * ms:.3f}, max {s['rtt_max_s'] * ms:.3f})\n"
        f"    1-way ~= {s['one_way_s'] * ms:.3f} ms  "
        f"(ok={s['samples_ok']} failed={s['samples_failed']})"
    )


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(argv)
    try:
        peers = _discover_peers(ns)
    except FileNotFoundError:
        sys.stderr.write(
            f"clock_skew_probe: stack file not found: {ns.stack!r} "
            f"(pass --stack or --peer)\n"
        )
        return 2
    except Exception as exc:
        # Malformed stack.yaml (yaml error, unexpected structure, ...): fail
        # with a clean message instead of a traceback.
        sys.stderr.write(
            f"clock_skew_probe: could not read peers from {ns.stack!r}: {exc}\n"
        )
        return 2
    if not peers:
        sys.stderr.write(
            "clock_skew_probe: no federation peers found "
            "(federation disabled, or none configured). Use --peer to probe "
            "an explicit endpoint.\n"
        )
        return 1

    ctx = zmq.Context.instance()

    def _probe(peer: tuple[str, str]) -> dict:
        peer_id, router_rpc = peer
        return _measure_peer(
            ctx,
            peer_id,
            router_rpc,
            samples=ns.samples,
            rpc_timeout_ms=ns.rpc_timeout_ms,
            interval_s=ns.interval_s,
        )

    # Probe peers concurrently: each gets its own DEALER socket and zmq.Context
    # is thread-safe. .map preserves peer order in the output.
    with ThreadPoolExecutor(max_workers=min(len(peers), 8)) as executor:
        results = list(executor.map(_probe, peers))

    if ns.json:
        sys.stdout.write(json.dumps(results, sort_keys=True) + "\n")
    else:
        sys.stdout.write(
            "clock skew vs federation peers (+skew => peer clock ahead of this host)\n"
        )
        for s in results:
            sys.stdout.write(_format_peer(s) + "\n")

    # Non-zero exit if any configured peer was unreachable, so the probe is
    # usable as a health gate in scripts.
    return 0 if all(s.get("reachable") for s in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
