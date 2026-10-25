"""Minimal Chrome DevTools Protocol client for the UI benchmark harness.

Uses only `websockets` (already a runtime dependency) so the harness needs no
Playwright/Puppeteer install. One `Browser` owns a Chrome process launched with
a throwaway profile; `Page` wraps a flattened session attached to one tab.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect

Json = dict[str, Any]
Handler = Callable[[Json], None]

_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def find_chrome(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("EC_BENCH_CHROME")
    if env:
        return env
    for candidate in _CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if found:
        return found
    raise FileNotFoundError("Chrome not found; pass --chrome or set EC_BENCH_CHROME")


class CdpError(RuntimeError):
    pass


class Connection:
    """One browser-level websocket; sessions are multiplexed via sessionId."""

    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Json]] = {}
        self._handlers: dict[tuple[str | None, str], list[Handler]] = defaultdict(list)
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            fut.set_exception(CdpError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result", {}))
                    continue
                key = (msg.get("sessionId"), msg.get("method", ""))
                for handler in list(self._handlers.get(key, ())):
                    handler(msg.get("params", {}))
        except Exception as exc:  # connection closed
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CdpError(f"connection lost: {exc!r}"))

    async def send(
        self,
        method: str,
        params: Json | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 60.0,
    ) -> Json:
        self._next_id += 1
        msg: Json = {"id": self._next_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        fut: asyncio.Future[Json] = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = fut
        await self._ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout)

    def on(self, method: str, handler: Handler, *, session_id: str | None = None) -> None:
        self._handlers[(session_id, method)].append(handler)

    def off(self, method: str, handler: Handler, *, session_id: str | None = None) -> None:
        handlers = self._handlers.get((session_id, method), [])
        if handler in handlers:
            handlers.remove(handler)

    async def close(self) -> None:
        self._reader.cancel()
        await self._ws.close()


class Page:
    def __init__(self, conn: Connection, target_id: str, session_id: str) -> None:
        self.conn = conn
        self.target_id = target_id
        self.session_id = session_id

    async def send(self, method: str, params: Json | None = None, *, timeout: float = 60.0) -> Json:
        return await self.conn.send(method, params, session_id=self.session_id, timeout=timeout)

    def on(self, method: str, handler: Handler) -> None:
        self.conn.on(method, handler, session_id=self.session_id)

    def off(self, method: str, handler: Handler) -> None:
        self.conn.off(method, handler, session_id=self.session_id)

    async def evaluate(self, expression: str, *, timeout: float = 60.0) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text")
            raise CdpError(f"evaluate failed: {text}")
        return result.get("result", {}).get("value")

    async def wait_for(
        self, expression: str, *, timeout: float = 30.0, interval: float = 0.1
    ) -> Any:
        deadline = time.monotonic() + timeout
        while True:
            value = await self.evaluate(expression)
            if value:
                return value
            if time.monotonic() > deadline:
                raise TimeoutError(f"timed out waiting for: {expression}")
            await asyncio.sleep(interval)

    async def navigate(self, url: str, *, timeout: float = 60.0) -> None:
        loaded = asyncio.get_running_loop().create_future()

        def _on_load(_: Json) -> None:
            if not loaded.done():
                loaded.set_result(None)

        self.on("Page.loadEventFired", _on_load)
        try:
            await self.send("Page.navigate", {"url": url})
            await asyncio.wait_for(loaded, timeout)
        finally:
            self.off("Page.loadEventFired", _on_load)

    async def metrics(self) -> dict[str, float]:
        result = await self.send("Performance.getMetrics")
        return {m["name"]: float(m["value"]) for m in result.get("metrics", [])}

    async def click(self, x: float, y: float) -> None:
        for kind in ("mousePressed", "mouseReleased"):
            await self.send(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
            )

    async def key(self, key: str, code: str, key_code: int) -> None:
        for kind in ("keyDown", "keyUp"):
            await self.send(
                "Input.dispatchKeyEvent",
                {"type": kind, "key": key, "code": code, "windowsVirtualKeyCode": key_code},
            )

    async def type_text(self, text: str) -> None:
        for ch in text:
            await self.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "key": ch})
            await self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})

    async def wheel(self, x: float, y: float, delta_y: float) -> None:
        await self.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": delta_y},
        )

    async def set_file_input(self, selector: str, path: Path) -> None:
        doc = await self.send("DOM.getDocument", {"depth": 0})
        node = await self.send(
            "DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": selector}
        )
        if not node.get("nodeId"):
            raise CdpError(f"no element for {selector}")
        await self.send(
            "DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(path.resolve())]}
        )


class Browser:
    """A Chrome process with an isolated profile and one browser websocket."""

    def __init__(
        self,
        *,
        chrome: str | None = None,
        headless: bool = False,
        width: int = 1278,
        height: int = 1303,
        extra_args: list[str] | None = None,
    ) -> None:
        self.chrome = find_chrome(chrome)
        self.headless = headless
        self.width = width
        self.height = height
        self.extra_args = extra_args or []
        self.proc: subprocess.Popen[bytes] | None = None
        self.conn: Connection | None = None
        self._profile_dir: str | None = None
        self.version: Json = {}

    async def __aenter__(self) -> "Browser":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        self._profile_dir = tempfile.mkdtemp(prefix="ec-ui-bench-")
        args = [
            self.chrome,
            "--remote-debugging-port=0",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-default-apps",
            "--disable-component-update",
            "--disable-sync",
            # Keep an unfocused/occluded bench window running at full rate.
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--force-device-scale-factor=1",
            f"--window-size={self.width},{self.height}",
            *self.extra_args,
        ]
        if self.headless:
            args.append("--headless=new")
        args.append("about:blank")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = Path(self._profile_dir) / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        port = 0
        while not port:
            try:
                # Windows: Chrome may still hold the file open while writing it.
                lines = port_file.read_text().splitlines() if port_file.is_file() else []
                port = int(lines[0]) if lines and lines[0].strip() else 0
            except (PermissionError, ValueError):
                port = 0
            if not port:
                if time.monotonic() > deadline:
                    raise TimeoutError("Chrome did not publish DevToolsActivePort")
                await asyncio.sleep(0.1)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=10) as r:
            self.version = json.loads(r.read())
        ws = await connect(self.version["webSocketDebuggerUrl"], max_size=None, ping_interval=None)
        self.conn = Connection(ws)

    async def new_page(self, url: str = "about:blank", *, new_window: bool = False) -> Page:
        """Attach to the initial blank tab, or open a new tab/window.

        Use `new_window=True` for concurrent clients: a background tab is hidden,
        and the app deliberately stops rendering hidden plots.
        """
        assert self.conn is not None
        targets = await self.conn.send("Target.getTargets")
        blank = [
            t
            for t in targets["targetInfos"]
            if t["type"] == "page" and t["url"] == "about:blank" and not t.get("attached")
        ]
        if blank and not new_window:
            target_id = blank[0]["targetId"]
        else:
            target_id = (
                await self.conn.send(
                    "Target.createTarget", {"url": "about:blank", "newWindow": new_window}
                )
            )["targetId"]
        attached = await self.conn.send(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        page = Page(self.conn, target_id, attached["sessionId"])
        for domain in ("Page", "Runtime", "DOM"):
            await page.send(f"{domain}.enable")
        await page.send("Performance.enable", {"timeDomain": "timeTicks"})
        await page.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": self.width, "height": self.height, "deviceScaleFactor": 1, "mobile": False},
        )
        if url != "about:blank":
            await page.navigate(url)
        return page

    async def close_page(self, page: Page) -> None:
        assert self.conn is not None
        await self.conn.send("Target.closeTarget", {"targetId": page.target_id})

    async def collect_trace(self, page: Page, run: Callable[[], Awaitable[None]], out: Path) -> Path:
        """Record a Chrome trace around `run()` and stream it to `out` (JSON array)."""
        assert self.conn is not None
        chunks: list[list[Json]] = []
        done = asyncio.get_running_loop().create_future()

        def _on_data(params: Json) -> None:
            chunks.append(params.get("value", []))

        def _on_complete(params: Json) -> None:
            if not done.done():
                done.set_result(params)

        page.on("Tracing.dataCollected", _on_data)
        page.on("Tracing.tracingComplete", _on_complete)
        categories = [
            "devtools.timeline",
            "disabled-by-default-devtools.timeline",
            "disabled-by-default-devtools.timeline.frame",
            "v8.execute",
            "blink.user_timing",
            "loading",
            "latencyInfo",
            "disabled-by-default-v8.cpu_profiler",
        ]
        await page.send(
            "Tracing.start",
            {
                "transferMode": "ReportEvents",
                "traceConfig": {"includedCategories": categories, "recordMode": "recordAsMuchAsPossible"},
            },
        )
        try:
            await run()
        finally:
            await page.send("Tracing.end")
            complete = await asyncio.wait_for(done, 300)
            page.off("Tracing.dataCollected", _on_data)
            page.off("Tracing.tracingComplete", _on_complete)
        events = [e for chunk in chunks for e in chunk]
        out.parent.mkdir(parents=True, exist_ok=True)
        import gzip

        with gzip.open(out, "wt", encoding="utf-8") as fh:
            json.dump({"traceEvents": events, "dataLossOccurred": complete.get("dataLossOccurred")}, fh)
        return out

    async def close(self) -> None:
        if self.conn is not None:
            try:
                await self.conn.send("Browser.close", timeout=5)
            except Exception:
                pass
            await self.conn.close()
            self.conn = None
        if self.proc is not None:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        if self._profile_dir:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None
