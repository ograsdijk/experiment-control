"""Compare two UI benchmark result files (medians across repeats).

    uv run python -m bench.ui.compare before.json after.json [--filter metrics.] [--all]

By default only rows that changed by at least --threshold percent are shown.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--filter", default="", help="only keys containing this substring")
    p.add_argument("--threshold", type=float, default=5.0, help="minimum |change| %% to show")
    p.add_argument("--all", action="store_true", help="show unchanged rows too")
    args = p.parse_args(argv)

    a, b = _load(args.before), _load(args.after)
    for key in ("scenario", "preset", "profile", "cpu_throttle", "viewport", "chrome"):
        if a["meta"].get(key) != b["meta"].get(key):
            print(f"warning: meta.{key} differs: {a['meta'].get(key)!r} vs {b['meta'].get(key)!r}")
    print(f"before: {a['meta'].get('git_sha')} ({args.before})")
    print(f"after:  {b['meta'].get('git_sha')} ({args.after})\n")

    rows = []
    for key in sorted(set(a["summary"]) | set(b["summary"])):
        if args.filter and args.filter not in key:
            continue
        va = a["summary"].get(key, {}).get("median")
        vb = b["summary"].get(key, {}).get("median")
        if va is None or vb is None:
            rows.append((key, va, vb, None))
            continue
        change = (vb - va) / abs(va) * 100.0 if va else (0.0 if vb == 0 else float("inf"))
        if args.all or abs(change) >= args.threshold:
            rows.append((key, va, vb, change))

    width = max((len(r[0]) for r in rows), default=10)
    print(f"{'metric':<{width}}  {'before':>12}  {'after':>12}  {'change':>9}")
    for key, va, vb, change in rows:
        fa = "-" if va is None else f"{va:12.3f}"
        fb = "-" if vb is None else f"{vb:12.3f}"
        fc = "" if change is None else f"{change:+8.1f}%"
        print(f"{key:<{width}}  {fa:>12}  {fb:>12}  {fc:>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
