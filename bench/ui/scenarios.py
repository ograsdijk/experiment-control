"""Benchmark scenarios. Each takes a ready `Page` and returns a JSON-able dict."""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .cdp import Browser, Page
from .stack import CpuSampler, find_gateway_pid, find_stack_pids

Json = dict[str, Any]

PROBE_JS = (Path(__file__).parent / "page_probe.js").read_text(encoding="utf-8")
PROFILE_DIR = Path(__file__).resolve().parents[2] / "examples" / "performance_test" / "ui_profiles"
HANG_TIMEOUT_S = 20.0  # emulated connect timeout for --external hang
PROFILE_INPUT = 'input[type="file"][accept=".json,application/json"]'

# Modals reachable in the performance_test instance. (label, menu parent or None)
MODAL_ACTIONS: list[tuple[str, str | None]] = [
    ("Devices", None),
    ("Processes", None),
    ("State Machines", None),
    ("Safety", None),
    ("DAG", None),
    ("Command history", None),
    ("Logs", None),
    ("Settings", "More"),
    # Present only with --ui-processes (sequencer + HDF writer).
    ("Sequencer", None),
    ("HDF", None),
]


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------


async def prepare_page(
    browser: Browser,
    base_url: str,
    *,
    cpu_throttle: float = 1.0,
    network: bool = False,
    new_window: bool = False,
) -> Page:
    page = await browser.new_page(new_window=new_window)
    await page.send("Page.addScriptToEvaluateOnNewDocument", {"source": PROBE_JS})
    if cpu_throttle > 1:
        await page.send("Emulation.setCPUThrottlingRate", {"rate": cpu_throttle})
    if network:
        # Must start before navigation so WebSocket URLs are known.
        page.net = NetworkTap(page)  # type: ignore[attr-defined]
        await page.net.start()  # type: ignore[attr-defined]
    await page.navigate(f"{base_url}/?perf=1")
    await page.wait_for("!!window.__EC_PERF__ && !!window.__ECB__", timeout=60)
    return page


async def click_label(
    page: Page, label: str, roles: str | None = None, *, timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        pos = await page.evaluate(f"window.__ECB__.locate({label!r}, {json.dumps(roles)})")
        if pos:
            await page.click(pos["x"], pos["y"])
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"no clickable element labelled {label!r}")
        await asyncio.sleep(0.1)


async def click_menu_item(page: Page, parent: str, label: str, *, attempts: int = 4) -> None:
    """Open a Mantine menu and click an item; retries when the menu toggles shut."""
    await dismiss_notifications(page)
    for _ in range(attempts):
        await click_label(page, parent)
        try:
            await page.wait_for(
                f"!!window.__ECB__.locate({label!r}, '[role=menuitem]')", timeout=2
            )
        except TimeoutError:
            await asyncio.sleep(0.3)
            continue
        await click_label(page, label)
        return
    raise TimeoutError(f"menu item {parent!r} -> {label!r} never appeared")


async def press_escape(page: Page) -> None:
    await page.key("Escape", "Escape", 27)


async def dismiss_notifications(page: Page) -> None:
    """Toasts sit over the header's top-right controls; close them before clicking."""
    await page.evaluate(
        "document.querySelectorAll('.mantine-Notification-root button').forEach((b) => b.click()); 0"
    )
    await page.wait_for("!document.querySelector('.mantine-Notification-root')", timeout=5)


async def close_all_modals(page: Page) -> None:
    await dismiss_notifications(page)
    for _ in range(4):
        if not (await page.evaluate("window.__ECB__.modalState().open")):
            return
        await press_escape(page)
        await page.wait_for("!window.__ECB__.modalState().open", timeout=5)


async def import_profile(page: Page, profile: str) -> None:
    """Import a UI profile through the real Settings → Import UI profile path."""
    path = PROFILE_DIR / (profile if profile.endswith(".json") else f"{profile}.json")
    await close_all_modals(page)
    await click_menu_item(page, "More", "Settings")
    await page.wait_for(f"!!document.querySelector({PROFILE_INPUT!r})", timeout=10)
    await page.set_file_input(PROFILE_INPUT, path)
    await asyncio.sleep(1.5)
    await close_all_modals(page)
    await asyncio.sleep(1.0)


def _delta(m0: dict[str, float], m1: dict[str, float], key: str) -> float:
    return m1.get(key, 0.0) - m0.get(key, 0.0)


def _metrics_summary(m0: dict[str, float], m1: dict[str, float], wall: float) -> Json:
    pct = lambda key: 100.0 * _delta(m0, m1, key) / wall  # noqa: E731
    return {
        "wall_s": wall,
        "renderer_cpu_pct": pct("ProcessTime"),
        "main_thread_busy_pct": pct("TaskDuration"),
        "script_pct": pct("ScriptDuration"),
        "style_layout_pct": pct("RecalcStyleDuration") + pct("LayoutDuration"),
        "v8_compile_pct": pct("V8CompileDuration"),
        "layouts_per_s": _delta(m0, m1, "LayoutCount") / wall,
        "style_recalcs_per_s": _delta(m0, m1, "RecalcStyleCount") / wall,
        "js_heap_used_mb": m1.get("JSHeapUsedSize", 0.0) / 2**20,
        "js_heap_total_mb": m1.get("JSHeapTotalSize", 0.0) / 2**20,
        "dom_nodes": m1.get("Nodes", 0.0),
        "js_event_listeners": m1.get("JSEventListeners", 0.0),
        "documents": m1.get("Documents", 0.0),
    }


def _perf_digest(snapshot: Json) -> Json:
    """Keep rates, timings and aggregated counters (scoped counters summed by prefix)."""
    counters: dict[str, float] = snapshot.get("counters", {})
    elapsed = max(1e-6, float(snapshot.get("elapsedSeconds", 0.0)))
    grouped: dict[str, float] = defaultdict(float)
    for name, value in counters.items():
        parts = name.split(".")
        # react.PanelCard.panel-3 -> react.PanelCard ; api.requests./api/x -> api.requests
        base = ".".join(parts[:2]) if parts[0] in {"react", "plot", "canvas", "poll", "api"} else name
        grouped[base] += value
    endpoints = {
        name.split(".", 2)[2]: value / elapsed
        for name, value in counters.items()
        if name.startswith("api.requests.") or name.startswith("poll.requests.")
    }
    return {
        "elapsed_s": elapsed,
        "rates": snapshot.get("rates", {}),
        "per_second": {k: v / elapsed for k, v in sorted(grouped.items())},
        "endpoint_requests_per_s": dict(sorted(endpoints.items(), key=lambda kv: -kv[1])),
        "timings": snapshot.get("timings", {}),
    }


def _backend_sampler(port: int) -> tuple[CpuSampler, CpuSampler]:
    gw = find_gateway_pid(port)
    return CpuSampler([gw] if gw else []), CpuSampler(find_stack_pids())


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def scenario_steady(
    browser: Browser,
    page: Page,
    *,
    warmup_s: float,
    duration_s: float,
    port: int,
    trace_path: Path | None = None,
    network: bool = False,
) -> Json:
    """Steady-state CPU/main-thread cost under live telemetry."""
    await asyncio.sleep(warmup_s)
    net: NetworkTap | None = getattr(page, "net", None) if network else None
    if net:
        net.clear()
    gw_cpu, stack_cpu = _backend_sampler(port)
    await page.evaluate("window.__EC_PERF__.reset(); window.__ECB__.reset(); 0")
    t_page0 = await page.evaluate("performance.now()")
    m0 = await page.metrics()
    gw_cpu.start()
    stack_cpu.start()
    t0 = time.monotonic()

    async def _wait() -> None:
        await asyncio.sleep(duration_s)

    if trace_path is not None:
        await browser.collect_trace(page, _wait, trace_path)
    else:
        await _wait()
    wall = time.monotonic() - t0
    m1 = await page.metrics()
    result: Json = {"metrics": _metrics_summary(m0, m1, wall)}
    result["gateway"] = gw_cpu.stop()
    result["stack"] = stack_cpu.stop()
    result["app"] = _perf_digest(await page.evaluate("window.__EC_PERF__.snapshot()"))
    loaf = await page.evaluate(f"window.__ECB__.loafSummary({t_page0})")
    loaf["per_minute"] = 60.0 * loaf["count"] / wall
    result["long_frames"] = loaf
    if net:
        result["network"] = await net.stop(wall)
    if trace_path is not None:
        result["trace"] = str(trace_path)
    return result


class NetworkTap:
    """Counts HTTP requests and WebSocket frames/bytes per endpoint via CDP Network events."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.ws_urls: dict[str, str] = {}
        self.ws_frames: Counter[str] = Counter()
        self.ws_bytes: Counter[str] = Counter()
        self.http: Counter[str] = Counter()
        self.http_bytes: Counter[str] = Counter()
        self.req_urls: dict[str, str] = {}
        self._handlers = {
            "Network.webSocketCreated": self._ws_created,
            "Network.webSocketFrameReceived": self._ws_frame,
            "Network.requestWillBeSent": self._request,
            "Network.loadingFinished": self._finished,
        }

    @staticmethod
    def _path(url: str) -> str:
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        return "/" + path.split("?", 1)[0]

    def _ws_created(self, p: Json) -> None:
        self.ws_urls[p["requestId"]] = self._path(p["url"])

    def _ws_frame(self, p: Json) -> None:
        key = self.ws_urls.get(p["requestId"], "?")
        resp = p.get("response", {})
        size = len(resp.get("payloadData", ""))
        if resp.get("opcode") == 2:  # binary frames arrive base64-encoded
            size = size * 3 // 4
        self.ws_frames[key] += 1
        self.ws_bytes[key] += size

    def clear(self) -> None:
        for counter in (self.ws_frames, self.ws_bytes, self.http, self.http_bytes):
            counter.clear()

    def _request(self, p: Json) -> None:
        key = self._path(p["request"]["url"])
        self.req_urls[p["requestId"]] = key
        self.http[key] += 1

    def _finished(self, p: Json) -> None:
        key = self.req_urls.get(p["requestId"])
        if key:
            self.http_bytes[key] += int(p.get("encodedDataLength", 0))

    async def start(self) -> None:
        for method, handler in self._handlers.items():
            self.page.on(method, handler)
        await self.page.send("Network.enable", {"maxTotalBufferSize": 0, "maxResourceBufferSize": 0})

    async def stop(self, wall: float) -> Json:
        await self.page.send("Network.disable")
        for method, handler in self._handlers.items():
            self.page.off(method, handler)
        rate = lambda c: {k: v / wall for k, v in c.most_common()}  # noqa: E731
        return {
            "ws_frames_per_s": rate(self.ws_frames),
            "ws_kb_per_s": {k: v / 1024 for k, v in rate(self.ws_bytes).items()},
            "http_requests_per_s": rate(self.http),
            "http_kb_per_s": {k: v / 1024 for k, v in rate(self.http_bytes).items()},
            "http_requests_total_per_s": sum(self.http.values()) / wall,
            "ws_kb_total_per_s": sum(self.ws_bytes.values()) / 1024 / wall,
        }


async def _emulate_offline_internet(page: Page, base_url: str, mode: str) -> None:
    """Lab PCs often have no internet: make non-gateway requests fail or hang."""
    if mode == "allow":
        return
    origin = base_url.rstrip("/")

    def _paused(params: Json) -> None:
        url = params["request"]["url"]
        if url.startswith(origin) or url.startswith("data:"):
            asyncio.ensure_future(page.send("Fetch.continueRequest", {"requestId": params["requestId"]}))
        elif mode == "fail":
            asyncio.ensure_future(
                page.send(
                    "Fetch.failRequest",
                    {"requestId": params["requestId"], "errorReason": "NameNotResolved"},
                )
            )
        else:  # "hang": a firewall silently dropping packets, until a connect timeout
            async def _timeout(request_id: str = params["requestId"]) -> None:
                await asyncio.sleep(HANG_TIMEOUT_S)
                await page.send(
                    "Fetch.failRequest", {"requestId": request_id, "errorReason": "TimedOut"}
                )

            asyncio.ensure_future(_timeout())

    page.on("Fetch.requestPaused", _paused)
    await page.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})


async def scenario_load(
    browser: Browser,
    base_url: str,
    *,
    profile: str | None,
    cache: str,
    settle_s: float,
    cpu_throttle: float,
    external: str = "allow",
) -> Json:
    """Page load: navigation milestones, first telemetry/plot, requests, early long frames."""
    page = await prepare_page(browser, base_url, cpu_throttle=cpu_throttle)
    if profile:
        await import_profile(page, profile)
    await asyncio.sleep(2.0)
    await _emulate_offline_internet(page, base_url, external)
    if cache == "cold":
        await page.send("Network.enable")
        await page.send("Network.clearBrowserCache")
        await page.send("Network.setCacheDisabled", {"cacheDisabled": True})
    net = NetworkTap(page)
    await net.start()
    t0 = time.monotonic()
    try:
        await page.navigate(f"{base_url}/?perf=1", timeout=90)
    except TimeoutError:
        pass  # a hung external stylesheet can delay the load event indefinitely
    await page.wait_for(
        "window.__ECB__ && window.__ECB__.state.firstTelemetryAt > 0", timeout=120
    )
    await asyncio.sleep(settle_s)
    wall = time.monotonic() - t0
    milestones = await page.evaluate("window.__ECB__.loadMilestones()")
    loaf = await page.evaluate("window.__ECB__.loafSummary(0)")
    metrics = await page.metrics()
    network = await net.stop(wall)
    http_counts = {k: round(v * wall) for k, v in network["http_requests_per_s"].items()}
    await browser.close_page(page)
    return {
        "cache": cache,
        "external": external,
        "milestones_ms": milestones,
        "requests_in_window": dict(sorted(http_counts.items(), key=lambda kv: -kv[1])),
        "requests_total": sum(http_counts.values()),
        "window_s": wall,
        "long_frames": {k: loaf[k] for k in ("count", "totalMs", "blockingMs", "maxMs")},
        "worst_frames": loaf["worst"],
        "js_heap_used_mb": metrics.get("JSHeapUsedSize", 0.0) / 2**20,
        "dom_nodes": metrics.get("Nodes", 0.0),
    }


async def _one_modal(page: Page, label: str, parent: str | None) -> Json:
    since = await page.evaluate("performance.now()")
    if parent:
        await click_menu_item(page, parent, label)
    else:
        await click_label(page, label)
    mounted = await page.evaluate("window.__ECB__.waitUntil('window.__ECB__.modalState().mounted')")
    settled = await page.evaluate("window.__ECB__.waitUntil('window.__ECB__.modalState().settled')")
    await asyncio.sleep(0.5)
    close_since = await page.evaluate("performance.now()")
    await press_escape(page)
    closed = await page.evaluate("window.__ECB__.waitUntil('!window.__ECB__.modalState().open')")
    await asyncio.sleep(0.3)
    events = await page.evaluate(f"window.__ECB__.eventsSince({since})")
    open_events = [e for e in events if e["start"] < close_since]
    close_events = [e for e in events if e["start"] >= close_since]
    open_loaf = await page.evaluate(f"window.__ECB__.loafSummary({since})")
    return {
        "open_to_mounted_ms": mounted["sinceInput"] if mounted["ok"] else None,
        "open_to_settled_ms": settled["sinceInput"] if settled["ok"] else None,
        "close_ms": closed["sinceInput"] if closed["ok"] else None,
        "open_event_max_ms": max((e["dur"] for e in open_events), default=0.0),
        "close_event_max_ms": max((e["dur"] for e in close_events), default=0.0),
        "long_frame_max_ms": open_loaf["maxMs"],
        "long_frame_blocking_ms": open_loaf["blockingMs"],
        "worst_frame_scripts": open_loaf["worst"][0]["scripts"] if open_loaf["worst"] else [],
    }


async def scenario_interactions(page: Page, *, warmup_s: float, repeats: int) -> Json:
    """Open/close each header modal under live load; first open includes lazy chunks."""
    await asyncio.sleep(warmup_s)
    await close_all_modals(page)
    out: Json = {}
    for label, parent in MODAL_ACTIONS:
        present = await page.evaluate(f"!!window.__ECB__.locate({(parent or label)!r})")
        if not present:
            out[label] = {"skipped": "control not present"}
            continue
        runs = []
        for _ in range(repeats):
            try:
                runs.append(await _one_modal(page, label, parent))
            except Exception as exc:
                runs.append({"error": repr(exc)})
                await close_all_modals(page)
            await asyncio.sleep(0.5)
        ok = [r for r in runs if "error" not in r and r["open_to_mounted_ms"] is not None]
        warm = ok[1:] or ok

        def med(key: str, xs: list[Json] = warm) -> float | None:
            vals = [r[key] for r in xs if r.get(key) is not None]
            return statistics.median(vals) if vals else None

        out[label] = {
            "first": runs[0],
            "warm_median": {
                key: med(key)
                for key in (
                    "open_to_mounted_ms",
                    "open_to_settled_ms",
                    "close_ms",
                    "open_event_max_ms",
                    "close_event_max_ms",
                    "long_frame_max_ms",
                )
            },
            "errors": [r["error"] for r in runs if "error" in r],
        }
    out["sequencer_typing"] = await _sequencer_typing(page)
    out["device_panel_toggle"] = await _device_panel_toggle(page, repeats)
    out["panel_scroll"] = await _panel_scroll(page)
    return out


CM_CONTENT = "document.querySelector('.cm-content')"
LIBRARY_INPUT = "document.querySelector('input[placeholder^=\"Library (\"]')"


def _keystroke_summary(events: list[Json], keystrokes: int) -> Json:
    """One duration per keystroke (its slowest event).

    Event Timing only reports events of 16 ms or more; the rest count as fast.
    """
    by_key: dict[int, float] = {}
    for e in events:
        if e["name"] in ("keydown", "keypress", "keyup", "beforeinput", "input"):
            by_key[e["id"]] = max(by_key.get(e["id"], 0.0), e["dur"])
    slow = sorted(by_key.values())
    padded = [0.0] * max(0, keystrokes - len(slow)) + slow

    def pick(q: float) -> float:
        return padded[min(len(padded) - 1, int(q * len(padded)))] if padded else 0.0

    return {
        "keystrokes": keystrokes,
        "slow_events": len(slow),
        "event_p50_ms": pick(0.5),
        "event_p95_ms": pick(0.95),
        "event_max_ms": slow[-1] if slow else 0.0,
    }


async def _open_sequencer_editor(page: Page) -> str | None:
    """Open the Sequencer on its YAML tab in edit mode, with the loaded sequence shown."""
    if not await page.evaluate("!!window.__ECB__.locate('Sequencer')"):
        return "no sequencer"
    await close_all_modals(page)
    await click_label(page, "Sequencer")
    await page.evaluate("window.__ECB__.waitUntil('window.__ECB__.modalState().settled')")
    await click_label(page, "YAML", "[role=tab]")
    try:
        await page.wait_for(f"!!{CM_CONTENT} && {CM_CONTENT}.textContent.length > 500", timeout=15)
    except TimeoutError:
        await close_all_modals(page)
        return "editor not shown"
    await click_label(page, "Edit", "label,button,[role=radio]")
    await page.wait_for(f"{CM_CONTENT}.getAttribute('contenteditable') === 'true'", timeout=5)
    return None


async def _type_measured(page: Page, text: str) -> Json:
    since = await page.evaluate("performance.now()")
    await page.type_text(text)
    await asyncio.sleep(1.0)
    events = await page.evaluate(f"window.__ECB__.eventsSince({since})")
    loaf = await page.evaluate(f"window.__ECB__.loafSummary({since})")
    return {
        **_keystroke_summary(events, len(text)),
        "long_frame_max_ms": loaf["maxMs"],
        "long_frame_blocking_ms": loaf["blockingMs"],
    }


async def _focus_editor_mid_line(page: Page) -> None:
    """Put the caret at the end of a line in the middle of the visible YAML."""
    pos = await page.evaluate(
        f"""(() => {{ const lines = [...{CM_CONTENT}.querySelectorAll('.cm-line')];
        const r = lines[Math.floor(lines.length / 2)].getBoundingClientRect();
        return {{ x: r.left + 4, y: r.top + r.height / 2 }}; }})()"""
    )
    await page.click(pos["x"], pos["y"])
    await page.key("End", "End", 35)


async def _sequencer_typing(page: Page, text: str = " # typed by the UI benchmark" * 2) -> Json:
    """Type into the Sequencer YAML editor; report per-keystroke event latency."""
    skipped = await _open_sequencer_editor(page)
    if skipped:
        return {"skipped": skipped}
    await _focus_editor_mid_line(page)
    out = await _type_measured(page, text)
    await close_all_modals(page)
    return out


async def scenario_sequencer(page: Page, *, warmup_s: float, idle_s: float = 10.0) -> Json:
    """Sequencer modal with a ~900-line sequence: idle, typing, library search and switch."""
    await asyncio.sleep(warmup_s)
    skipped = await _open_sequencer_editor(page)
    if skipped:
        return {"skipped": skipped}
    out: Json = {}

    # Idle with the modal open: only status polls and telemetry run.
    since = await page.evaluate("performance.now()")
    await page.evaluate("window.__ECB__.startFrames()")
    await asyncio.sleep(idle_s)
    frames = await page.evaluate("window.__ECB__.stopFrames()")
    loaf = await page.evaluate(f"window.__ECB__.loafSummary({since})")
    out["idle"] = {
        "seconds": idle_s,
        "frames": frames,
        "long_frames": loaf["count"],
        "long_frame_max_ms": loaf["maxMs"],
        "long_frame_blocking_ms": loaf["blockingMs"],
    }

    await _focus_editor_mid_line(page)
    out["editor_typing"] = await _type_measured(page, " # typed by the UI benchmark" * 2)

    if not await page.evaluate(f"!!{LIBRARY_INPUT}"):
        out["library"] = {"skipped": "no sequence library"}
        await close_all_modals(page)
        return out
    pos = await page.evaluate(
        f"(() => {{ const r = {LIBRARY_INPUT}.getBoundingClientRect();"
        " return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()"
    )
    await page.click(pos["x"], pos["y"])
    out["library_search_typing"] = await _type_measured(page, "bench_scan_1")
    await page.key("ArrowDown", "ArrowDown", 40)
    await page.key("Enter", "Enter", 13)
    await page.wait_for(f"/bench_scan_1/.test({LIBRARY_INPUT}.value)", timeout=5)

    # Switch file: Load selected → the new sequence shows in the editor.
    await page.evaluate(f"window.__benchBefore = {CM_CONTENT}.textContent; 0")
    since = await page.evaluate("performance.now()")
    await click_label(page, "Load selected")
    changed = json.dumps(f"{CM_CONTENT}.textContent !== window.__benchBefore")
    res = await page.evaluate(f"window.__ECB__.waitUntil({changed}, 20000)")
    await asyncio.sleep(1.0)
    events = await page.evaluate(f"window.__ECB__.eventsSince({since})")
    loaf = await page.evaluate(f"window.__ECB__.loafSummary({since})")
    out["library_switch"] = {
        "click_to_editor_ms": res["sinceInput"] if not res["timedOut"] else None,
        "click_event_max_ms": max((e["dur"] for e in events), default=0.0),
        "long_frame_max_ms": loaf["maxMs"],
        "long_frame_blocking_ms": loaf["blockingMs"],
    }
    await close_all_modals(page)
    return out


async def _device_panel_toggle(page: Page, repeats: int) -> Json:
    results = []
    for _ in range(repeats):
        for label, done in (
            ("Collapse device panel", "!!window.__ECB__.locate('Open devices panel')"),
            ("Open devices panel", "!!window.__ECB__.locate('Collapse device panel')"),
        ):
            present = await page.evaluate(f"!!window.__ECB__.locate({label!r})")
            if not present:
                return {"skipped": f"{label} not present"}
            since = await page.evaluate("performance.now()")
            await click_label(page, label)
            res = await page.evaluate(f"window.__ECB__.waitUntil({done!r})")
            await asyncio.sleep(0.6)
            events = await page.evaluate(f"window.__ECB__.eventsSince({since})")
            loaf = await page.evaluate(f"window.__ECB__.loafSummary({since})")
            results.append(
                {
                    "action": label,
                    "done_ms": res["sinceInput"],
                    "event_max_ms": max((e["dur"] for e in events), default=0.0),
                    "long_frame_max_ms": loaf["maxMs"],
                }
            )
    by_action: Json = {}
    for action in {r["action"] for r in results}:
        rs = [r for r in results if r["action"] == action]
        by_action[action] = {
            k: statistics.median(r[k] for r in rs) for k in ("done_ms", "event_max_ms", "long_frame_max_ms")
        }
    return by_action


async def _panel_scroll(page: Page, seconds: float = 4.0) -> Json:
    """Wheel-scroll the plot workspace down and back; report frame pacing."""
    pos = await page.evaluate(
        """(() => { const el = document.querySelector('.uplot');
        if (!el) return null; const r = el.getBoundingClientRect();
        return {x: r.left + r.width / 2, y: Math.min(window.innerHeight - 50, r.top + r.height / 2)}; })()"""
    )
    if not pos:
        return {"skipped": "no plot panels"}
    since = await page.evaluate("performance.now()")
    await page.evaluate("window.__ECB__.startFrames()")
    steps = int(seconds / 0.05)
    for i in range(steps):
        await page.wheel(pos["x"], pos["y"], 120 if i < steps // 2 else -120)
        await asyncio.sleep(0.05)
    frames = await page.evaluate("window.__ECB__.stopFrames()")
    loaf = await page.evaluate(f"window.__ECB__.loafSummary({since})")
    perf = await page.evaluate("window.__EC_PERF__.snapshot().counters")
    return {
        "frames": frames,
        "long_frames": {k: loaf[k] for k in ("count", "blockingMs", "maxMs")},
        "worst_frames": loaf["worst"][:3],
        "uplot_setData_total": sum(v for k, v in perf.items() if "uplot_setData" in k),
    }


async def scenario_soak(
    page: Page, *, duration_s: float, sample_every_s: float, modal_cycles: int, profile_cycles: list[str]
) -> Json:
    """Heap/DOM/listener growth after forced GC, plus optional churn cycles."""

    async def sample(tag: str) -> Json:
        await page.send("HeapProfiler.collectGarbage")
        await asyncio.sleep(0.2)
        m = await page.metrics()
        return {
            "tag": tag,
            "t_s": time.monotonic() - t0,
            "js_heap_used_mb": m.get("JSHeapUsedSize", 0.0) / 2**20,
            "dom_nodes": m.get("Nodes", 0.0),
            "js_event_listeners": m.get("JSEventListeners", 0.0),
            "documents": m.get("Documents", 0.0),
            "uplot_instances": await page.evaluate("document.querySelectorAll('.uplot').length"),
        }

    await page.send("HeapProfiler.enable")
    t0 = time.monotonic()
    samples = [await sample("start")]
    churn: Json = {}

    if modal_cycles:
        await asyncio.sleep(5)
        before = await sample("before-modal-churn")
        for _ in range(modal_cycles):
            for label, parent in MODAL_ACTIONS:
                if not await page.evaluate(f"!!window.__ECB__.locate({(parent or label)!r})"):
                    continue
                if parent:
                    await click_menu_item(page, parent, label)
                else:
                    await click_label(page, label)
                await page.evaluate("window.__ECB__.waitUntil('window.__ECB__.modalState().settled')")
                await close_all_modals(page)
        after = await sample("after-modal-churn")
        samples += [before, after]
        churn["modal"] = _growth(before, after, modal_cycles)

    if profile_cycles:
        before = await sample("before-profile-churn")
        for profile in profile_cycles:
            await import_profile(page, profile)
        after = await sample("after-profile-churn")
        samples += [before, after]
        churn["profile"] = _growth(before, after, len(profile_cycles))

    soak_start = time.monotonic()
    steady = [await sample("steady-0")]
    while time.monotonic() - soak_start < duration_s:
        await asyncio.sleep(sample_every_s)
        steady.append(await sample(f"steady-{len(steady)}"))
    samples += steady
    hours = max(1e-6, (steady[-1]["t_s"] - steady[0]["t_s"]) / 3600)
    trend = {
        key: (steady[-1][key] - steady[0][key]) / hours
        for key in ("js_heap_used_mb", "dom_nodes", "js_event_listeners")
    } if len(steady) > 1 else {}
    return {"samples": samples, "churn": churn, "steady_growth_per_hour": trend}


def _growth(before: Json, after: Json, cycles: int) -> Json:
    return {
        "cycles": cycles,
        **{
            f"{key}_delta": after[key] - before[key]
            for key in ("js_heap_used_mb", "dom_nodes", "js_event_listeners", "uplot_instances")
        },
    }


async def scenario_clients(
    browser: Browser, base_url: str, *, port: int, counts: list[int], warmup_s: float, duration_s: float,
    profile: str | None,
) -> Json:
    """Gateway + stack CPU as concurrent browser clients increase."""
    pages: list[Page] = []
    out: Json = {}
    for n in counts:
        while len(pages) < n:
            page = await prepare_page(browser, base_url, new_window=bool(pages))
            if profile and not pages:
                await import_profile(page, profile)
            pages.append(page)
        await asyncio.sleep(warmup_s)
        gw_cpu, stack_cpu = _backend_sampler(port)
        gw_cpu.start()
        stack_cpu.start()
        for p in pages:
            await p.evaluate("window.__EC_PERF__.reset(); 0")
        m0 = [await p.metrics() for p in pages]
        t0 = time.monotonic()
        await asyncio.sleep(duration_s)
        wall = time.monotonic() - t0
        m1 = [await p.metrics() for p in pages]
        rates = [await p.evaluate("window.__EC_PERF__.snapshot().rates") for p in pages]
        out[str(n)] = {
            "gateway": gw_cpu.stop(),
            "stack": stack_cpu.stop(),
            "renderer_cpu_pct_per_client": [
                100.0 * _delta(a, b, "ProcessTime") / wall for a, b in zip(m0, m1)
            ],
            "telemetry_msgs_per_s_per_client": [
                r.get("telemetry.messages_per_second", 0.0) for r in rates
            ],
        }
    for p in pages:
        await browser.close_page(p)
    return out
