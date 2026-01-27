# Other Candidate Improvements (Not Yet Selected)

This document captures cleanup/refactor ideas that were suggested but not chosen in the current round.

## Performance / Robustness
- **Ring buffer zero‑copy path**: add a `read_event_into`/memoryview option for `shm_ring.py` so consumers like the HDF writer can avoid per‑event `bytes(...)` allocations.
- **Optional faster JSON**: allow `orjson` in `utils/zmq_helpers.py` as an opt‑in backend for high‑rate telemetry/event streams.

## Testing / Quality
- **Ruleset parsing tests**: validate config error paths + defaults merge for interlock/watchdog rules.
- **Router flow tests**: cover allow/transform/reject paths through interceptors and ensure event publication is correct.

## Miscellaneous
- **Capabilities caching policy**: consolidate how capabilities are cached/refreshed across TUI + clients to avoid flicker or stale results.
