from __future__ import annotations

from typing import Any

from .utils.zmq_helpers import json_dumps

Json = dict[str, Any]


def publish_manager_event(manager: Any, topic: str, payload: Json) -> None:
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

