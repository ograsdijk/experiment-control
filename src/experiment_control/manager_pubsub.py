from __future__ import annotations

import threading
from typing import Any

from .utils.zmq_helpers import json_dumps

Json = dict[str, Any]


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
            evt_queue.put_nowait((topic, payload))
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

