"""Automated web UI benchmark harness.

Drives the production React UI against the examples/performance_test stack in
a real Chrome through the DevTools protocol, and writes one JSON result file
per invocation. See bench/ui/README.md.

    uv run --with psutil python -m bench.ui.run --launch --preset baseline \
        --profile baseline --scenario steady --repeats 3
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import scenarios as sc
from .cdp import Browser
from .stack import REPO_ROOT, PerfStack

Json = dict[str, Any]
RESULTS_DIR = REPO_ROOT / "bench" / "ui" / "results"
SCENARIOS = ["steady", "load", "interactions", "soak", "clients"]


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=REPO_ROOT, text=True).strip()
    except Exception:
        return ""


def _meta(args: argparse.Namespace, browser: Browser) -> Json:
    return {
        "timestamp": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain", "--", "web/react_ui/src", "src")),
        "chrome": browser.version.get("Browser"),
        "preset": args.preset,
        "profile": args.profile,
        "scenario": args.scenario,
        "viewport": [args.width, args.height],
        "headless": args.headless,
        "cpu_throttle": args.cpu_throttle,
        "ui_processes": args.ui_processes,
        "warmup_s": args.warmup,
        "duration_s": args.duration,
        "repeats": args.repeats,
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "python": sys.version.split()[0],
    }


def summarize(runs: list[Json]) -> Json:
    """Median/min/max for every numeric leaf shared by all runs."""

    def leaves(obj: Any, prefix: str = "") -> dict[str, float]:
        out: dict[str, float] = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.update(leaves(v, f"{prefix}.{k}" if prefix else str(k)))
        elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
            out[prefix] = float(obj)
        return out

    flat = [leaves(r) for r in runs]
    keys = set(flat[0]).intersection(*flat[1:]) if flat else set()
    summary: Json = {}
    for key in sorted(keys):
        vals = [f[key] for f in flat]
        summary[key] = {"median": statistics.median(vals), "min": min(vals), "max": max(vals)}
    return summary


async def _run(args: argparse.Namespace) -> Json:
    base_url = f"http://127.0.0.1:{args.port}"
    runs: list[Json] = []
    out_dir = Path(args.out) if args.out else RESULTS_DIR
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    label = f"{stamp}-{args.scenario}-{args.preset}-{args.profile or 'noprofile'}"
    if args.cpu_throttle > 1:
        label += f"-x{args.cpu_throttle:g}"
    if args.scenario in ("steady", "interactions", "soak") and args.device_panel == "collapsed":
        label += "-collapsed"
    if args.scenario == "load":
        label += f"-{args.cache}" + (f"-ext{args.external}" if args.external != "allow" else "")
    if args.tag:
        label += f"-{args.tag}"

    async def fresh_browser(fn: Callable[[Browser], Awaitable[Json]]) -> tuple[Json, Json]:
        async with Browser(
            chrome=args.chrome, headless=args.headless, width=args.width, height=args.height
        ) as browser:
            result = await fn(browser)
            return result, _meta(args, browser)

    meta: Json = {}
    for i in range(args.repeats):
        print(f"[ui-bench] {args.scenario} repeat {i + 1}/{args.repeats}", flush=True)

        async def one(browser: Browser) -> Json:
            if args.scenario == "load":
                return await sc.scenario_load(
                    browser, base_url, profile=args.profile, cache=args.cache,
                    settle_s=args.duration, cpu_throttle=args.cpu_throttle,
                    external=args.external,
                )
            if args.scenario == "clients":
                counts = [int(x) for x in args.clients.split(",")]
                return await sc.scenario_clients(
                    browser, base_url, port=args.port, counts=counts,
                    warmup_s=args.warmup, duration_s=args.duration, profile=args.profile,
                )
            page = await sc.prepare_page(
                browser, base_url, cpu_throttle=args.cpu_throttle,
                network=args.network and args.scenario == "steady",
            )
            if args.profile:
                await sc.import_profile(page, args.profile)
            if args.device_panel == "collapsed":
                if await page.evaluate("!!window.__ECB__.locate('Collapse device panel')"):
                    await sc.click_label(page, "Collapse device panel")
            elif await page.evaluate("!!window.__ECB__.locate('Open devices panel')"):
                await sc.click_label(page, "Open devices panel")
            if args.scenario == "steady":
                trace = out_dir / "traces" / f"{label}-r{i + 1}.json.gz" if args.trace else None
                return await sc.scenario_steady(
                    browser, page, warmup_s=args.warmup, duration_s=args.duration,
                    port=args.port, trace_path=trace, network=args.network,
                )
            if args.scenario == "interactions":
                return await sc.scenario_interactions(
                    page, warmup_s=args.warmup, repeats=args.interaction_repeats
                )
            if args.scenario == "soak":
                cycles = args.profile_cycles.split(",") if args.profile_cycles else []
                return await sc.scenario_soak(
                    page, duration_s=args.duration, sample_every_s=args.sample_every,
                    modal_cycles=args.modal_cycles, profile_cycles=cycles,
                )
            raise ValueError(args.scenario)

        result, meta = await fresh_browser(one)
        runs.append(result)

    report = {"meta": meta, "runs": runs, "summary": summarize(runs)}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[ui-bench] wrote {path}")
    return report


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", choices=SCENARIOS, default="steady")
    p.add_argument("--preset", default="baseline", help="light|baseline|heavy|stress")
    p.add_argument("--profile", default="baseline", help="ui_profiles/<name>.json; '' for none")
    p.add_argument("--launch", action="store_true", help="start/stop the stack + gateway")
    p.add_argument(
        "--ui-processes",
        action="store_true",
        help="with --launch: add a sequencer + idle HDF writer (Sequencer/HDF modals)",
    )
    p.add_argument("--ui-dist", default="web/react_ui/dist", help="dist served when --launch")
    p.add_argument("--port", type=int, default=8088)
    p.add_argument("--repeats", type=int, default=3, help="fresh browser per repeat")
    p.add_argument("--warmup", type=float, default=60.0)
    p.add_argument("--duration", type=float, default=60.0, help="measure window / load settle / soak length (s)")
    p.add_argument("--trace", action="store_true", help="steady: save a gzipped Chrome trace")
    p.add_argument("--network", action="store_true", help="steady: count HTTP/WS traffic per endpoint")
    p.add_argument("--device-panel", choices=["open", "collapsed"], default="open")
    p.add_argument("--cache", choices=["cold", "warm"], default="cold", help="load scenario")
    p.add_argument(
        "--external", choices=["allow", "fail", "hang"], default="allow",
        help="load: emulate no internet (non-gateway requests fail or hang)",
    )
    p.add_argument("--interaction-repeats", type=int, default=5)
    p.add_argument("--clients", default="1,2,4,8", help="clients scenario: client counts")
    p.add_argument("--sample-every", type=float, default=60.0, help="soak sampling interval (s)")
    p.add_argument("--modal-cycles", type=int, default=0, help="soak: open/close all modals N times")
    p.add_argument("--profile-cycles", default="", help="soak: comma list of profiles to import in turn")
    p.add_argument("--cpu-throttle", type=float, default=1.0, help="Chrome CPU slowdown factor")
    p.add_argument("--width", type=int, default=1278)
    p.add_argument("--height", type=int, default=1303)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--chrome", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--tag", default="", help="suffix for the result file name")
    args = p.parse_args(argv)
    args.profile = args.profile or None
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.launch:
        with PerfStack(
            args.preset,
            port=args.port,
            ui_dist=REPO_ROOT / args.ui_dist,
            ui_processes=args.ui_processes,
        ):
            asyncio.run(_run(args))
    else:
        asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
