from __future__ import annotations

import queue
import threading
from typing import Any

from .utils.zmq_helpers import json_dumps

Json = dict[str, Any]

# Topics whose loss compromises an audit / contract guarantee, not just
# observability. When the lifecycle event queue is full, these topics
# block the worker briefly (up to _AUDIT_BLOCKING_PUT_TIMEOUT_S) for the
# main thread to drain, instead of being silently dropped. Other topics
# fall back to the original drop+counter behaviour.
#
#   manager.command                       — tamper-evident command journal
#   manager.command_interceptor.modified  — same journal stream
#   manager.command_interceptor.error     — same journal stream
_AUDIT_TOPICS: frozenset[str] = frozenset(
    {
        "manager.command",
        "manager.command_interceptor.modified",
        "manager.command_interceptor.error",
    }
)
# 5s is well above the main loop's typical drain cadence (~10-100Hz) but
# well below realistic supervisor restart / shutdown timeouts. A worker
# blocked for 5s on an audit publish indicates a manager stall worth
# noticing; the eventual fallback bumps the drop counter so the loss is
# visible in the next drain.
_AUDIT_BLOCKING_PUT_TIMEOUT_S: float = 5.0


def publish_manager_event(manager: Any, topic: str, payload: Json) -> None:
    # ZMQ sockets aren't thread-safe and several side effects below
    # (_external_pub.send_multipart, log forwarding) also write to
    # main-thread-owned sockets. When called from a lifecycle worker
    # thread, queue the event so the main loop's drain step publishes
    # it on the right thread (preserving all side effects).
    main_id = getattr(manager, "_main_thread_id", None)
    if main_id is not None and threading.get_ident() != main_id:
        evt_queue = getattr(manager, "_lifecycle_event_queue", None)
        if evt_queue is not None:
            try:
                if topic in _AUDIT_TOPICS:
                    # Block for the audit-publish budget rather than
                    # silently dropping. If the main thread is genuinely
                    # wedged for >budget the put still raises queue.Full
                    # and we fall through to the drop counter.
                    evt_queue.put(
                        (topic, payload),
                        timeout=_AUDIT_BLOCKING_PUT_TIMEOUT_S,
                    )
                else:
                    evt_queue.put_nowait((topic, payload))
            except queue.Full:
                # Main thread isn't draining fast enough; drop the
                # event and bump the counter so drain_lifecycle_events
                # can emit a manager.lifecycle.events_dropped warning
                # operators can see in the next tick.
                with manager._lifecycle_event_dropped_lock:
                    manager._lifecycle_event_dropped += 1
            return
        # No queue available — fall through. In normal operation the
        # queue exists once Manager.__init__ has run.

    # External subscribers can filter topics at SUBSCRIBE level.
    manager._external_pub.send_multipart(
        [topic.encode("utf-8"), json_dumps(payload)]
    )
    if topic == "manager.command":
        manager._append_command_journal_entry(payload)
    for hook in manager._event_hooks:
        hook(topic, payload)
    if topic != "manager.log":
        manager._maybe_publish_log_event(topic, payload)
    manager._maybe_emit_manager_log_sink(topic, payload)

