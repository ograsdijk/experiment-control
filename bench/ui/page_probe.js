// Injected into every benchmark page before app scripts run (CDP
// Page.addScriptToEvaluateOnNewDocument). Collects Long Animation Frames,
// Event Timing entries and paint milestones; exposes helpers on
// window.__ECB__. It never schedules per-frame work unless a frame counter
// is explicitly started, so steady-state CPU measurements are not perturbed.
(() => {
  if (window.__ECB__) return;
  const MAX = 20000;
  const s = {
    loaf: [],
    events: [],
    paints: {},
    lcp: 0,
    lastInputAt: 0,
    frames: 0,
    frameTimes: [],
    counting: false,
    firstTelemetryAt: 0,
    firstPlotAt: 0,
  };
  const push = (arr, e) => {
    if (arr.length < MAX) arr.push(e);
  };
  const observe = (type, fn, opts = {}) => {
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) fn(e);
      }).observe({ type, buffered: true, ...opts });
    } catch (_) {
      /* unsupported entry type */
    }
  };
  observe("long-animation-frame", (e) =>
    push(s.loaf, {
      start: e.startTime,
      dur: e.duration,
      block: e.blockingDuration,
      render: e.renderStart ? e.startTime + e.duration - e.renderStart : 0,
      scripts: (e.scripts || [])
        .slice()
        .sort((a, b) => b.duration - a.duration)
        .slice(0, 3)
        .map((x) => ({
          src: (x.sourceURL || "").split("/").pop(),
          fn: x.sourceFunctionName,
          invoker: x.invoker,
          dur: x.duration,
        })),
    })
  );
  observe(
    "event",
    (e) => {
      if (!e.interactionId) return;
      push(s.events, {
        name: e.name,
        start: e.startTime,
        dur: e.duration,
        inputDelay: e.processingStart - e.startTime,
        processing: e.processingEnd - e.processingStart,
        id: e.interactionId,
      });
    },
    { durationThreshold: 16 }
  );
  observe("paint", (e) => {
    s.paints[e.name] = e.startTime;
  });
  observe("largest-contentful-paint", (e) => {
    s.lcp = e.startTime;
  });
  for (const type of ["pointerdown", "keydown"]) {
    window.addEventListener(type, (e) => (s.lastInputAt = e.timeStamp), true);
  }

  // Load milestones from the app's own ?perf=1 counters (polled only until seen).
  const milestonePoll = setInterval(() => {
    const perf = window.__EC_PERF__;
    if (!perf) return;
    const c = perf.snapshot().counters;
    const now = performance.now();
    if (!s.firstTelemetryAt && (c["telemetry.messages"] || 0) > 0) s.firstTelemetryAt = now;
    if (!s.firstPlotAt && Object.keys(c).some((k) => k.includes("uplot_setData") && c[k] > 0)) {
      s.firstPlotAt = now;
    }
    if (s.firstTelemetryAt && s.firstPlotAt) clearInterval(milestonePoll);
  }, 25);
  setTimeout(() => clearInterval(milestonePoll), 60000);

  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const textOf = (el) => (el.innerText || el.textContent || "").trim();

  window.__ECB__ = {
    state: s,
    reset() {
      s.loaf = [];
      s.events = [];
    },
    /** Center of the first visible element matching an aria-label or exact text. */
    locate(label, roles) {
      const sel = roles || "button,[role=button],[role=menuitem],[role=tab],a";
      const els = [...document.querySelectorAll(sel)].filter(visible);
      const el =
        els.find((e) => e.getAttribute("aria-label") === label) ||
        els.find((e) => textOf(e) === label) ||
        els.find((e) => textOf(e).startsWith(label)) ||
        els.find((e) => (e.getAttribute("aria-label") || "").startsWith(label));
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    },
    modalState() {
      const content = [...document.querySelectorAll(".mantine-Modal-content")].filter(visible);
      if (!content.length) return { open: false, mounted: false, settled: false };
      const top = content[content.length - 1];
      const body = top.querySelector(".mantine-Modal-body");
      const mounted = !!body && body.childElementCount > 0;
      const opaque = getComputedStyle(top).opacity === "1";
      const loading = !!top.querySelector(".mantine-Loader-root");
      return { open: true, mounted, settled: mounted && opaque && !loading };
    },
    /** rAF-poll until `predicate()` (a JS expression string) is true; resolves with timings. */
    waitUntil(expr, timeoutMs = 15000) {
      const fn = new Function(`return (${expr});`);
      const started = performance.now();
      return new Promise((resolve) => {
        const step = () => {
          let ok = false;
          try {
            ok = !!fn();
          } catch (_) {
            ok = false;
          }
          const now = performance.now();
          if (ok || now - started > timeoutMs) {
            resolve({ ok, at: now, sinceInput: now - s.lastInputAt, timedOut: !ok });
            return;
          }
          requestAnimationFrame(step);
        };
        step();
      });
    },
    startFrames() {
      s.frames = 0;
      s.frameTimes = [];
      s.counting = true;
      let last = performance.now();
      const tick = (t) => {
        if (!s.counting) return;
        s.frames += 1;
        s.frameTimes.push(t - last);
        last = t;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    },
    stopFrames() {
      s.counting = false;
      const times = s.frameTimes.slice(1).sort((a, b) => a - b);
      const pick = (q) => (times.length ? times[Math.min(times.length - 1, Math.floor(q * times.length))] : 0);
      return { frames: s.frames, p50: pick(0.5), p95: pick(0.95), max: times.length ? times[times.length - 1] : 0, over50: times.filter((t) => t > 50).length };
    },
    loafSummary(since = 0) {
      const xs = s.loaf.filter((e) => e.start >= since);
      const total = xs.reduce((a, e) => a + e.dur, 0);
      const blocking = xs.reduce((a, e) => a + e.block, 0);
      const worst = xs.slice().sort((a, b) => b.dur - a.dur).slice(0, 5);
      return {
        count: xs.length,
        totalMs: total,
        blockingMs: blocking,
        maxMs: xs.reduce((a, e) => Math.max(a, e.dur), 0),
        worst,
      };
    },
    eventsSince(since = 0) {
      return s.events.filter((e) => e.start >= since);
    },
    loadMilestones() {
      const nav = performance.getEntriesByType("navigation")[0] || {};
      return {
        domContentLoadedMs: nav.domContentLoadedEventEnd || 0,
        loadMs: nav.loadEventEnd || 0,
        fcpMs: s.paints["first-contentful-paint"] || 0,
        lcpMs: s.lcp,
        firstTelemetryMs: s.firstTelemetryAt,
        firstPlotMs: s.firstPlotAt,
        transferBytes: performance
          .getEntriesByType("resource")
          .concat([nav])
          .reduce((a, e) => a + (e.transferSize || 0), 0),
        decodedBytes: performance
          .getEntriesByType("resource")
          .concat([nav])
          .reduce((a, e) => a + (e.decodedBodySize || 0), 0),
      };
    },
  };
})();
