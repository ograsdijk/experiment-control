from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING, Any, Callable

from ..utils.zmq_helpers import json_dumps

if TYPE_CHECKING:
    import zmq

    from ..manager_protocol import ManagerProtocol

    # When mypy type-checks ``PubSubMixin`` in isolation, this alias
    # lets us inherit the cross-mixin method signatures from
    # ``ManagerProtocol`` without inheriting at runtime (Protocol is
    # structural). At runtime the inheritance chain is unchanged.
    _MixinBase = ManagerProtocol
else:
    _MixinBase = object

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


class PubSubMixin(_MixinBase):
    """Mixin providing manager pub-sub event publishing.

    Phase 8.2.1: migrated ``publish_manager_event`` from a module-level
    helper to a mixin method. The module-level
    :func:`publish_manager_event` is preserved as a thin forwarder so
    existing tests that import it directly (e.g.
    ``tests.test_group_f_hardening``) keep working.

    At runtime ``_MixinBase`` is ``object``; only mypy sees
    :class:`ManagerProtocol` as the base, which supplies signatures
    for the sibling-mixin methods (``_append_command_journal_entry``,
    ``_maybe_publish_log_event``, ``_maybe_emit_manager_log_sink``)
    this mixin calls. Owned state below stays local because each
    attribute has a concrete type declared on ``Manager`` itself.
    """

    # State owned by Manager / ManagerSockets / LifecycleExecutor —
    # declared here so mypy can type-check reads in method bodies.
    # NOT signatures of sibling methods — those live on ManagerProtocol
    # so each cross-mixin contract has a single source of truth.
    _external_pub: "zmq.Socket"
    _main_thread_id: int
    _lifecycle_event_queue: "queue.Queue[tuple[str, Json]]"
    _lifecycle_event_dropped: int
    _lifecycle_event_dropped_lock: threading.Lock
    _event_hooks: list[Callable[[str, Json], None]]

    def _publish_manager_event(self, topic: str, payload: Json) -> None:
        # ZMQ sockets aren't thread-safe and several side effects below
        # (_external_pub.send_multipart, log forwarding) also write to
        # main-thread-owned sockets. When called from a lifecycle worker
        # thread, queue the event so the main loop's drain step publishes
        # it on the right thread (preserving all side effects).
        #
        # Production ``Manager`` sets ``_main_thread_id`` early in
        # ``__init__`` via ``LifecycleExecutor.bind_to_manager``; the
        # ``hasattr`` guard is a safety net for SimpleNamespace test stubs
        # that publish events without wiring lifecycle bookkeeping.
        if (
            hasattr(self, "_main_thread_id")
            and threading.get_ident() != self._main_thread_id
        ):
            try:
                if topic in _AUDIT_TOPICS:
                    # Block for the audit-publish budget rather than
                    # silently dropping. If the main thread is genuinely
                    # wedged for >budget the put still raises queue.Full
                    # and we fall through to the drop counter.
                    self._lifecycle_event_queue.put(
                        (topic, payload),
                        timeout=_AUDIT_BLOCKING_PUT_TIMEOUT_S,
                    )
                else:
                    self._lifecycle_event_queue.put_nowait((topic, payload))
            except queue.Full:
                # Main thread isn't draining fast enough; drop the
                # event and bump the counter so drain_lifecycle_events
                # can emit a manager.lifecycle.events_dropped warning
                # operators can see in the next tick.
                with self._lifecycle_event_dropped_lock:
                    self._lifecycle_event_dropped += 1
            return

        # External subscribers can filter topics at SUBSCRIBE level.
        self._external_pub.send_multipart(
            [topic.encode("utf-8"), json_dumps(payload)]
        )
        if topic == "manager.command":
            self._append_command_journal_entry(payload)
        for hook in self._event_hooks:
            hook(topic, payload)
        if topic != "manager.log":
            self._maybe_publish_log_event(topic, payload)
        self._maybe_emit_manager_log_sink(topic, payload)


def publish_manager_event(manager: Any, topic: str, payload: Json) -> None:
    """Module-level forwarder kept for backward compatibility.

    Some tests import this name directly (e.g.
    ``from experiment_control._manager.pubsub import publish_manager_event``)
    and call it against a ``SimpleNamespace`` stub. The body lives on
    :class:`PubSubMixin`; this trampoline delegates so both call
    styles keep working. The forwarder will be deleted once the
    direct-import tests migrate to ``mgr._publish_manager_event(...)``.
    """
    PubSubMixin._publish_manager_event(manager, topic, payload)
