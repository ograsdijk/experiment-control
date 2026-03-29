from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import zmq
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.driver import Driver
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, RichLog, Static

from .utils.logging_levels import normalize_log_severity, severity_rank
from .utils.zmq_helpers import json_dumps, safe_json_loads

Json = dict[str, Any]


@dataclass
class DeviceStatus:
    device_id: str
    registered: bool
    liveness: str | None
    hb_age_s: float | None
    telemetry_age_s: float | None
    driver_state: str | None
    device_state: str | None
    device_reachable: bool | None
    last_error: str | None
    driver_proc_state: str | None
    driver_pid: int | None
    driver_restart_count: int
    driver_last_exit_code: int | None
    driver_last_error: str | None
    is_remote: bool = False


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Button, Label

        yield Vertical(
            Label(self._message, id="confirm_message"),
            Horizontal(
                Button("Confirm", id="confirm_yes", variant="success"),
                Button("Cancel", id="confirm_no", variant="error"),
                id="confirm_buttons",
            ),
            id="confirm_dialog",
        )

    def on_button_pressed(self, event) -> None:  # type: ignore[override]
        button_id = getattr(event.button, "id", "")
        if button_id == "confirm_yes":
            self.dismiss(True)
        elif button_id == "confirm_no":
            self.dismiss(False)

    def on_key(self, event) -> None:  # type: ignore[override]
        key = event.key.lower()
        try:
            from textual.widgets import Button

            focused = getattr(self.app, "focused", None)
            if key == "enter" and isinstance(focused, Button):
                button_id = getattr(focused, "id", "")
                if button_id == "confirm_yes":
                    self.dismiss(True)
                    event.stop()
                    return
                if button_id == "confirm_no":
                    self.dismiss(False)
                    event.stop()
                    return
        except Exception:
            pass
        if key in ("y", "enter"):
            self.dismiss(True)
            event.stop()
        elif key in ("n", "escape"):
            self.dismiss(False)
            event.stop()

    def on_mount(self) -> None:
        # Put focus on the Confirm button so Enter works naturally and
        # the user can tab/arrow between buttons depending on Textual version.
        try:
            self.query_one("#confirm_yes").focus()
        except Exception:
            pass


class InvokeMemberScreen(ModalScreen[dict[str, Any] | None]):
    def __init__(self, member_name: str, params: list[dict[str, Any]] | None) -> None:
        super().__init__()
        self._member_name = member_name
        self._params = params or []

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Button, Label

        inputs: list[Any] = []
        if self._params:
            for spec in self._params:
                name = str(spec.get("name", ""))
                required = bool(spec.get("required", False))
                annotation = spec.get("annotation")
                default = spec.get("default")
                label_text = name + (" *" if required else "")
                if annotation:
                    label_text += f" ({annotation})"
                default_text = ""
                if default is not None:
                    try:
                        default_text = json.dumps(default)
                    except Exception:
                        default_text = str(default)
                inputs.append(Static(label_text))
                inputs.append(Input(value=default_text, id=f"invoke_param_{name}"))

        yield Vertical(
            Label(f"Invoke {self._member_name}", id="invoke_title"),
            *inputs,
            Horizontal(
                Button("Confirm", id="invoke_yes", variant="success"),
                Button("Cancel", id="invoke_no", variant="error"),
                id="invoke_buttons",
            ),
            id="invoke_dialog",
        )

    def on_mount(self) -> None:
        try:
            if self._params:
                first = str(self._params[0].get("name", ""))
                self.query_one(f"#invoke_param_{first}", Input).focus()
            else:
                self.query_one("#invoke_input", Input).focus()
        except Exception:
            pass

    def on_button_pressed(self, event) -> None:  # type: ignore[override]
        button_id = getattr(event.button, "id", "")
        if button_id == "invoke_yes":
            parsed = self._collect_params()
            if parsed is None:
                self.dismiss(None)
                return
            self.dismiss(parsed)
        elif button_id == "invoke_no":
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:  # type: ignore[override]
        key = event.key.lower()
        if key == "enter":
            try:
                from textual.widgets import Button

                focused = getattr(self.app, "focused", None)
                if isinstance(focused, Button):
                    button_id = getattr(focused, "id", "")
                    if button_id == "invoke_yes":
                        parsed = self._collect_params()
                        if parsed is None:
                            self.dismiss(None)
                            event.stop()
                            return
                        self.dismiss(parsed)
                        event.stop()
                        return
                    if button_id == "invoke_no":
                        self.dismiss(None)
                        event.stop()
                        return
            except Exception:
                pass
            parsed = self._collect_params()
            if parsed is None:
                self.dismiss(None)
                event.stop()
                return
            self.dismiss(parsed)
            event.stop()
        elif key == "escape":
            self.dismiss(None)
            event.stop()

    def _collect_params(self) -> dict[str, Any] | None:
        if not self._params:
            return {}

        out: dict[str, Any] = {}
        for spec in self._params:
            name = str(spec.get("name", ""))
            required = bool(spec.get("required", False))
            raw = self.query_one(f"#invoke_param_{name}", Input).value
            if not raw.strip():
                if required:
                    return None
                continue
            try:
                value = json.loads(raw)
            except Exception:
                value = raw
            out[name] = value
        return out


class SetMemberScreen(ModalScreen[object | None]):
    def __init__(self, member_name: str) -> None:
        super().__init__()
        self._member_name = member_name

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Button, Label

        yield Vertical(
            Label(f"Set {self._member_name}", id="set_title"),
            Input(value="null", id="set_input"),
            Horizontal(
                Button("Confirm", id="set_yes", variant="success"),
                Button("Cancel", id="set_no", variant="error"),
                id="set_buttons",
            ),
            id="set_dialog",
        )

    def on_mount(self) -> None:
        try:
            self.query_one("#set_input", Input).focus()
        except Exception:
            pass

    def on_button_pressed(self, event) -> None:  # type: ignore[override]
        button_id = getattr(event.button, "id", "")
        if button_id == "set_yes":
            raw = self.query_one("#set_input", Input).value
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            self.dismiss(parsed)
        elif button_id == "set_no":
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:  # type: ignore[override]
        key = event.key.lower()
        if key == "enter":
            try:
                from textual.widgets import Button

                focused = getattr(self.app, "focused", None)
                if isinstance(focused, Button):
                    button_id = getattr(focused, "id", "")
                    if button_id == "set_yes":
                        raw = self.query_one("#set_input", Input).value
                        try:
                            parsed = json.loads(raw)
                        except Exception:
                            parsed = raw
                        self.dismiss(parsed)
                        event.stop()
                        return
                    if button_id == "set_no":
                        self.dismiss(None)
                        event.stop()
                        return
            except Exception:
                pass
            raw = self.query_one("#set_input", Input).value
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            self.dismiss(parsed)
            event.stop()
        elif key == "escape":
            self.dismiss(None)
            event.stop()


class TopicFilterScreen(ModalScreen[dict[str, bool]]):
    """Modal that shows seen topics and lets the user toggle visibility per topic."""

    CSS = """
    TopicFilterScreen {
        align: center middle;
        background: $surface 80%;
    }

    #topic_dialog {
        width: 100;
        height: 30;
        padding: 1 2;
        border: round $primary;
        background: $panel;
    }

    #topics_table {
        height: 1fr;
    }

    #topic_help {
        height: auto;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("enter", "toggle_topic", "Toggle"),
        ("space", "toggle_topic", "Toggle"),
        ("a", "all_on", "All on"),
        ("n", "all_off", "All off"),
    ]

    def __init__(
        self,
        *,
        topic_counts: dict[str, int],
        topic_visible: dict[str, bool],
    ) -> None:
        super().__init__()
        self._topic_counts = dict(topic_counts)
        self._topic_visible = dict(topic_visible)

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Topics (toggle show/hide)", id="topic_title"),
            DataTable(id="topics_table"),
            Static(
                "Enter/Space: toggle | a: all on | n: all off | Esc: close",
                id="topic_help",
            ),
            id="topic_dialog",
        )

    def on_mount(self) -> None:
        table = self.query_one("#topics_table", DataTable)
        table.add_columns("show", "topic", "count")
        table.cursor_type = "row"

        self._refresh_table()

        try:
            table.focus()
        except Exception:
            pass

    def _refresh_table(self) -> None:
        table = self.query_one("#topics_table", DataTable)
        table.clear()

        for topic in sorted(self._topic_counts.keys()):
            shown = self._topic_visible.get(topic, True)
            mark = "x" if shown else ""
            table.add_row(mark, topic, str(self._topic_counts.get(topic, 0)), key=topic)

    def action_toggle_topic(self) -> None:
        table = self.query_one("#topics_table", DataTable)
        row_index = table.cursor_row
        if row_index is None or row_index < 0:
            return
        row = table.get_row_at(row_index)
        if not row:
            return
        topic = str(row[1])
        cur = self._topic_visible.get(topic, True)
        self._topic_visible[topic] = not cur
        self._refresh_table()

    def action_all_on(self) -> None:
        for t in self._topic_counts.keys():
            self._topic_visible[t] = True
        self._refresh_table()

    def action_all_off(self) -> None:
        for t in self._topic_counts.keys():
            self._topic_visible[t] = False
        self._refresh_table()

    def action_close(self) -> None:
        self.dismiss(self._topic_visible)


class ManagerTUI(App):
    CSS = """
    #main {height: 1fr;}
    #devices {width: 1fr;}
    #inspector {width: 1fr;}
    #status_row {height: 2;}
    #errors_table {height: 10;}
    #members_table {height: 14;}
    #cap_help {height: auto;}
    #event_log {height: 12;}

    InvokeMemberScreen, SetMemberScreen {
        align: center middle;
        background: $surface 80%;
    }

    #invoke_dialog, #set_dialog {
        width: 80;
        height: auto;
        min-height: 0;
        max-height: 70%;
        padding: 1 2;
        border: round $primary;
        background: $panel;
    }

    #invoke_dialog > *, #set_dialog > * {
        height: auto;
    }

    ConfirmScreen {
        align: center middle;
        background: $surface 80%;
    }

    #confirm_dialog {
        width: 60;
        height: auto;
        min-height: 0;
        max-height: 70%;
        padding: 1 2;
        border: round $primary;
        background: $panel;
    }

    #confirm_dialog > * {
        height: auto;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "driver_start", "Start selected"),
        ("x", "driver_stop", "Stop selected"),
        ("r", "driver_restart", "Restart selected"),
        ("v", "device_recover", "Recover device"),
        ("c", "device_connect", "Connect"),
        ("d", "device_disconnect", "Disconnect"),
        ("S", "drivers_start_all", "Start all"),
        ("X", "drivers_stop_all", "Stop all"),
        ("t", "toggle_streaming", "Toggle streaming"),
        ("enter", "member_primary", "Invoke/Get"),
        ("e", "member_set", "Set value"),
        ("R", "capabilities_refresh", "Refresh capabilities"),
        ("f5", "reconnect_backend", "Reconnect"),
        ("p", "topics", "Topics"),
        ("l", "clear_log", "Clear log"),
    ]

    _DEFAULT_EVENT_LOG_HIDDEN_TOPICS = frozenset(
        {
            "manager.telemetry_update",
            "manager.heartbeat",
            "manager.chunk_ready",
        }
    )
    _VALID_PUB_QUEUE_OVERFLOW_POLICIES = frozenset({"drop_newest", "drop_oldest"})

    streaming_enabled = reactive(True)

    def __init__(
        self,
        *,
        manager_rpc: str = "tcp://127.0.0.1:6000",
        manager_pub: str = "tcp://127.0.0.1:6001",
        rpc_timeout_ms: int = 1500,
        snapshot_period_s: float = 2.0,
        event_log_max_lines: int = 10_000,
        event_log_default_hidden_topics: list[str] | tuple[str, ...] | set[str] | None = None,
        event_log_manager_min_severity: str = "warning",
        pub_queue_maxsize: int = 10_000,
        pub_queue_overflow_policy: str = "drop_newest",
        driver_class: type[Driver] | None = None,
    ) -> None:
        super().__init__(driver_class=driver_class)
        self._manager_rpc = manager_rpc
        self._manager_pub = manager_pub
        self._rpc_timeout_ms = rpc_timeout_ms
        self._snapshot_period_s = snapshot_period_s
        self._event_log_max_lines = max(100, int(event_log_max_lines))
        self._event_log_hidden_topics = self._normalize_topic_set(
            event_log_default_hidden_topics,
            default=self._DEFAULT_EVENT_LOG_HIDDEN_TOPICS,
        )
        self._event_log_manager_min_severity = self._normalize_log_severity(
            event_log_manager_min_severity
        )
        self._event_log_manager_min_rank = self._severity_rank(
            self._event_log_manager_min_severity
        )
        self._pub_queue_maxsize = max(1, int(pub_queue_maxsize))
        overflow_policy = str(pub_queue_overflow_policy or "drop_newest").strip().lower()
        if overflow_policy not in self._VALID_PUB_QUEUE_OVERFLOW_POLICIES:
            overflow_policy = "drop_newest"
        self._pub_queue_overflow_policy = overflow_policy

        self._ctx = zmq.Context.instance()
        self._rpc = self._new_rpc_socket()
        self._rpc_seq = 0

        self._sub: zmq.Socket | None = None

        self._pub_queue: queue.Queue[tuple[str, Json]] = queue.Queue(
            maxsize=self._pub_queue_maxsize
        )
        self._chunk_cache: dict[tuple[str, str], tuple[str, Json]] = {}
        self._chunk_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._sub_reconnect_event = threading.Event()
        self._pub_thread_handle: threading.Thread | None = None

        self._device_status: dict[str, DeviceStatus] = {}
        self._telemetry_cache: dict[str, Json] = {}
        self._heartbeat_cache: dict[str, Json] = {}

        self._selected_device_id: str | None = None
        self._has_user_selection = False
        self._processes: list[Json] = []
        self._process_status_map: dict[str, Json] = {}
        self._selected_process_id: str | None = None
        self._has_user_process_selection = False
        self._suppress_selection_events = False
        self._selected_member_key: str | None = None
        self._has_user_member_selection = False
        self._suppress_member_selection = False
        self._topic_counts: dict[str, int] = {}
        self._topic_visible: dict[str, bool] = {}
        self._dropped_pub_messages = 0
        self._errors = deque(maxlen=200)
        self._seen_error_fingerprints: set[str] = set()
        self._seen_error_fingerprint_order: deque[str] = deque(maxlen=2000)
        self._last_manager_log_t_mono: float | None = None
        self._log_tail_bootstrap_limit = 250
        self._last_toast_by_key: dict[str, tuple[str, float]] = {}
        self._toast_cooldown_s = 2.0
        self._toast_repeat_s = 30.0
        self._pub_drain_max = 500
        self._telemetry_columns_device = ("signal", "value", "units", "quality", "age_s")
        self._telemetry_columns_process = ("field", "value")
        self._heartbeat_columns_device = (
            "pid",
            "seq",
            "driver_state",
            "device_state",
            "reachable",
            "loop_lag_s",
            "last_error",
        )
        self._heartbeat_columns_process = (
            "hb_age_s",
            "last_hb_t_wall",
            "last_hb_t_mono",
            "endpoint",
        )
        self._driver_columns = (
            "state",
            "pid",
            "restart_count",
            "last_exit_code",
            "last_error",
        )
        self._cap_cache: dict[str, dict[str, Any]] = {}
        self._cap_cache_mono: dict[str, float] = {}
        self._cap_ttl_s: float = 5.0
        self._proc_cap_cache: dict[str, dict[str, Any]] = {}
        self._proc_cap_retry_next_mono: dict[str, float] = {}
        self._proc_cap_retry_delay_s: dict[str, float] = {}
        self._proc_cap_retry_initial_s: float = 0.5
        self._proc_cap_retry_max_s: float = 2.0
        self._members_last: dict[str, list[dict[str, Any]]] = {}
        self._proc_members_last: dict[str, list[dict[str, Any]]] = {}
        self._members_source: str = "device"
        self._inspector_mode: str = "device"
        self._members_context_key: str | None = None
        self._members_rendered_fingerprint: dict[str, str] = {}
        self._inspector_dirty = True
        self._last_inspector_render = 0.0
        self._inspector_min_period_s = 0.2
        self._error_counts: dict[str, int] = {}
        self._backend_status_text = "Backend: connecting"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="devices"):
                yield DataTable(id="devices_table")
                yield Label("Processes", id="processes_title")
                yield DataTable(id="processes_table")
            with Vertical(id="inspector"):
                yield Label("Telemetry", id="telemetry_title")
                yield DataTable(id="telemetry_table")
                yield Label("Heartbeat", id="heartbeat_title")
                yield DataTable(id="heartbeat_table")
                yield Label("Capabilities", id="cap_title")
                yield DataTable(id="members_table")
                yield Static("Enter: invoke/get | e: set | R: refresh", id="cap_help")
                yield Label("Driver process", id="driver_title")
                yield DataTable(id="driver_table")
                yield Label("Process", id="process_title")
                yield DataTable(id="process_table")
        yield Label("Last errors", id="errors_title")
        yield DataTable(id="errors_table")
        with Horizontal(id="status_row"):
            yield Static("Streaming: ON", id="streaming_status")
            yield Static("Dropped: 0", id="dropped_status")
            yield Static(self._backend_status_text, id="backend_status")
        yield RichLog(id="event_log", max_lines=self._event_log_max_lines)
        yield Footer()

    def on_mount(self) -> None:
        self._setup_tables()
        self._load_manager_log_tail_bootstrap()
        self.set_interval(self._snapshot_period_s, self._refresh_snapshot)
        self.set_interval(0.2, self._drain_pub_queue)
        self._pub_thread_handle = threading.Thread(target=self._pub_thread, daemon=True)
        self._pub_thread_handle.start()

    def on_unmount(self) -> None:
        self._stop_event.set()
        self._sub_reconnect_event.set()
        thread = self._pub_thread_handle
        if thread is not None:
            thread.join(timeout=1.5)
        try:
            self._rpc.close(0)
        except Exception:
            pass

    def _setup_tables(self) -> None:
        devices = self.query_one("#devices_table", DataTable)
        devices.add_columns(
            "device_id",
            "liveness",
            "driver_proc",
            "pid",
            "hb_age_s",
            "telemetry_age_s",
            "driver_state",
            "device_state",
            "last_error",
        )
        devices.cursor_type = "row"

        processes = self.query_one("#processes_table", DataTable)
        processes.add_columns(
            "process_id",
            "state",
            "pid",
            "hb_age_s",
            "restart_count",
            "last_exit_code",
            "last_error",
        )
        processes.cursor_type = "row"

        telemetry = self.query_one("#telemetry_table", DataTable)
        telemetry.add_columns(*self._telemetry_columns_device)

        heartbeat = self.query_one("#heartbeat_table", DataTable)
        heartbeat.add_columns(*self._heartbeat_columns_device)

        driver = self.query_one("#driver_table", DataTable)
        driver.add_columns(*self._driver_columns)

        process = self.query_one("#process_table", DataTable)
        process.add_columns("field", "value")

        errors = self.query_one("#errors_table", DataTable)
        errors.add_columns("time", "sev", "source", "id", "message")
        errors.cursor_type = "row"

        members = self.query_one("#members_table", DataTable)
        members.add_columns("name", "kind", "rw", "type", "source", "doc")
        members.cursor_type = "row"

    def _is_table_focused(self, table: DataTable) -> bool:
        return self.focused is table

    def _action_target(self) -> str:
        if self._inspector_mode in {"device", "process"}:
            return self._inspector_mode
        return self._members_source

    def _set_inspector_mode(self, mode: str) -> None:
        if mode not in {"device", "process"}:
            return
        if mode == self._inspector_mode:
            return
        self._inspector_mode = mode
        self._members_source = mode
        self._configure_inspector_tables()
        self._mark_inspector_dirty()

    def _configure_inspector_tables(self) -> None:
        telemetry = self.query_one("#telemetry_table", DataTable)
        heartbeat = self.query_one("#heartbeat_table", DataTable)
        driver = self.query_one("#driver_table", DataTable)
        if self._members_source == "process":
            telemetry.clear(columns=True)
            telemetry.add_columns(*self._telemetry_columns_process)
            heartbeat.clear(columns=True)
            heartbeat.add_columns(*self._heartbeat_columns_process)
        else:
            telemetry.clear(columns=True)
            telemetry.add_columns(*self._telemetry_columns_device)
            heartbeat.clear(columns=True)
            heartbeat.add_columns(*self._heartbeat_columns_device)
        driver.clear(columns=True)
        driver.add_columns(*self._driver_columns)

    def _restore_members_scroll(self, scroll_x: float, scroll_y: float) -> None:
        table = self.query_one("#members_table", DataTable)
        table.scroll_x = min(scroll_x, table.max_scroll_x)
        table.scroll_y = min(scroll_y, table.max_scroll_y)
        table.scroll_target_x = table.scroll_x
        table.scroll_target_y = table.scroll_y

    def _new_rpc_socket(self) -> zmq.Socket:
        rpc = self._ctx.socket(zmq.DEALER)
        rpc.setsockopt(zmq.LINGER, 0)
        rpc.connect(self._manager_rpc)
        return rpc

    def _reset_rpc_socket(self) -> None:
        try:
            self._rpc.close(0)
        except Exception:
            pass
        self._rpc = self._new_rpc_socket()

    def _new_sub_socket(self) -> zmq.Socket:
        sub = self._ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.SUBSCRIBE, b"manager.")
        sub.setsockopt(zmq.RCVTIMEO, 200)
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(self._manager_pub)
        return sub

    def _reset_sub_socket(self) -> None:
        try:
            if self._sub is not None:
                self._sub.close(0)
        except Exception:
            pass
        self._sub = self._new_sub_socket()

    def _request_sub_reconnect(self) -> None:
        self._sub_reconnect_event.set()

    def _set_backend_status(self, text: str) -> None:
        self._backend_status_text = text
        try:
            self.query_one("#backend_status", Static).update(text)
        except Exception:
            pass

    def _next_request_id(self) -> str:
        self._rpc_seq += 1
        return f"tui-{self._rpc_seq}"

    def _rpc_call(self, payload: Json) -> Json | None:
        try:
            request = dict(payload)
            expected_request_id = request.get("request_id")
            if expected_request_id is None:
                expected_request_id = self._next_request_id()
                request["request_id"] = expected_request_id

            # Drop late replies from previous timed-out requests.
            while True:
                if not self._rpc.poll(0, zmq.POLLIN):
                    break
                _ = self._rpc.recv(zmq.NOBLOCK)

            self._rpc.send(json_dumps(request))
            deadline = time.monotonic() + (self._rpc_timeout_ms / 1000.0)
            while True:
                remaining_s = deadline - time.monotonic()
                if remaining_s <= 0:
                    raise TimeoutError(
                        f"manager rpc timed out after {self._rpc_timeout_ms} ms"
                    )
                remaining_ms = int(max(1.0, remaining_s * 1000.0))
                if not self._rpc.poll(remaining_ms, zmq.POLLIN):
                    raise TimeoutError(
                        f"manager rpc timed out after {self._rpc_timeout_ms} ms"
                    )
                raw = self._rpc.recv()
                resp = safe_json_loads(raw)
                if not isinstance(resp, dict):
                    continue
                if (
                    expected_request_id is not None
                    and resp.get("request_id") is not None
                    and resp.get("request_id") != expected_request_id
                ):
                    # Late/stale reply from an older request; keep waiting.
                    continue
                self._set_backend_status("Backend: connected")
                return resp
        except Exception:
            self._set_backend_status("Backend: unavailable")
            self._reset_rpc_socket()
            return None

    @staticmethod
    def _normalize_log_severity(raw: Any) -> str:
        return normalize_log_severity(raw, default="info")

    @staticmethod
    def _severity_rank(raw: Any) -> int:
        return severity_rank(raw, default="info")

    @staticmethod
    def _normalize_topic_set(
        raw: list[str] | tuple[str, ...] | set[str] | None,
        *,
        default: set[str] | frozenset[str],
    ) -> set[str]:
        if raw is None:
            return set(default)
        out: set[str] = set()
        for item in raw:
            topic = str(item or "").strip()
            if topic:
                out.add(topic)
        return out

    def _default_topic_visibility(self, topic: str) -> bool:
        return topic not in self._event_log_hidden_topics

    def _topic_enabled_for_event_log(self, topic: str, payload: Json) -> bool:
        visible = self._topic_visible.get(topic, self._default_topic_visibility(topic))
        if not visible:
            return False
        if topic == "manager.log":
            severity = self._normalize_log_severity(payload.get("severity"))
            if self._severity_rank(severity) < self._event_log_manager_min_rank:
                return False
        return True

    def _remember_error_fingerprint(self, fingerprint: str) -> bool:
        if fingerprint in self._seen_error_fingerprints:
            return False
        if self._seen_error_fingerprint_order.maxlen is not None:
            while (
                len(self._seen_error_fingerprint_order)
                >= self._seen_error_fingerprint_order.maxlen
            ):
                old = self._seen_error_fingerprint_order.popleft()
                self._seen_error_fingerprints.discard(old)
        self._seen_error_fingerprint_order.append(fingerprint)
        self._seen_error_fingerprints.add(fingerprint)
        return True

    def _load_manager_log_tail_bootstrap(self) -> None:
        resp = self._rpc_call(
            {
                "type": "manager.logs.tail",
                "params": {"limit": self._log_tail_bootstrap_limit},
            }
        )
        if not resp or not resp.get("ok"):
            return
        result = resp.get("result", {})
        if not isinstance(result, dict):
            return
        entries = result.get("entries", [])
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    self._ingest_manager_log_entry(entry, from_tail=True)
        latest = result.get("latest_t_mono")
        if isinstance(latest, (int, float)):
            self._last_manager_log_t_mono = float(latest)
        self._render_errors_table()

    def _ingest_manager_log_entry(self, entry: Json, *, from_tail: bool = False) -> None:
        severity = self._normalize_log_severity(entry.get("severity"))
        if self._severity_rank(severity) < self._severity_rank("warning"):
            ts = entry.get("ts")
            if isinstance(ts, dict):
                t_mono = ts.get("t_mono")
                if isinstance(t_mono, (int, float)):
                    t_mono_f = float(t_mono)
                    if (
                        self._last_manager_log_t_mono is None
                        or t_mono_f > self._last_manager_log_t_mono
                    ):
                        self._last_manager_log_t_mono = t_mono_f
            return

        topic = str(entry.get("topic", "manager.log") or "manager.log")
        source_kind = str(entry.get("source_kind", "manager") or "manager")
        source_id = str(entry.get("source_id", "") or "")
        device_id = str(entry.get("device_id", "") or "")
        process_id = str(entry.get("process_id", "") or "")
        message = str(entry.get("message", "") or "")
        payload_json = str(entry.get("payload_json", "") or "")

        source = source_kind or "manager"
        id_ = device_id or process_id or source_id or "manager"
        text = message or payload_json
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if len(text) > 2000:
            text = text[:1997] + "..."

        ts = entry.get("ts")
        t_wall: float | None = None
        t_mono: float | None = None
        if isinstance(ts, dict):
            raw_wall = ts.get("t_wall")
            raw_mono = ts.get("t_mono")
            if isinstance(raw_wall, (int, float)):
                t_wall = float(raw_wall)
            if isinstance(raw_mono, (int, float)):
                t_mono = float(raw_mono)
        if t_mono is not None and (
            self._last_manager_log_t_mono is None or t_mono > self._last_manager_log_t_mono
        ):
            self._last_manager_log_t_mono = t_mono

        base_fp = {
            "sev": severity,
            "topic": topic,
            "source": source,
            "id": id_,
            "message": text,
            "t_mono": t_mono,
        }
        try:
            fingerprint = json.dumps(base_fp, sort_keys=True, default=str)[:800]
        except Exception:
            fingerprint = f"{severity}:{topic}:{source}:{id_}:{text[:256]}:{t_mono}"

        if not self._remember_error_fingerprint(fingerprint):
            return

        self._record_error(
            source=source,
            id_=id_,
            topic=topic,
            message=text,
            severity=severity,
            fingerprint=fingerprint,
            t_wall=t_wall,
            t_mono=t_mono,
            render=False,
        )

        if not from_tail and self._severity_rank(severity) >= self._severity_rank("error"):
            toast_message = text.replace("\n", " ").strip()
            if len(toast_message) > 180:
                toast_message = toast_message[:177] + "..."
            if not toast_message:
                toast_message = f"{topic} ({source}:{id_})"
            self._toast_once(
                key=f"log:{source}:{id_}:{topic}",
                fingerprint=fingerprint,
                message=toast_message,
                severity="error",
            )

    def _format_manager_log_event_line(self, entry: Json) -> str:
        severity = self._normalize_log_severity(entry.get("severity"))
        source_kind = str(entry.get("source_kind", "manager") or "manager")
        source_id = str(entry.get("source_id", "") or "")
        topic = str(entry.get("topic", "manager.log") or "manager.log")
        message = str(entry.get("message", "") or "")
        payload_json = str(entry.get("payload_json", "") or "")
        text = message or payload_json
        text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
        if len(text) > 180:
            text = text[:177] + "..."
        source_text = f"{source_kind}:{source_id}" if source_id else source_kind
        if text:
            return f"manager.log [{severity}] {source_text} {topic} {text}"
        return f"manager.log [{severity}] {source_text} {topic}"

    def _device_command(
        self, device_id: str, action: str, params: Json | None = None
    ) -> Json | None:
        if params is None:
            params = {}
        resp = self._rpc_call(
            {
                "type": "command",
                "device_id": device_id,
                "action": action,
                "params": params,
                "source_kind": "tui",
                "source_id": "manager_tui",
            }
        )
        if resp is None:
            return None
        if "ok" in resp:
            return resp
        status = resp.get("status")
        if status == "OK":
            return {"ok": True, "result": resp.get("result")}
        if status == "ERROR":
            return {"ok": False, "error": resp.get("error", "unknown error")}
        return resp

    def _process_rpc(self, process_id: str, request: Json) -> Json | None:
        resp = self._rpc_call(
            {
                "type": "manager.processes.rpc",
                "process_id": process_id,
                "request": request,
                "source_kind": "tui",
                "source_id": "manager_tui",
            }
        )
        if resp is None:
            return None
        if "ok" in resp:
            return resp
        return resp

    @staticmethod
    def _process_is_registered(proc: Json | None) -> bool:
        if not isinstance(proc, dict):
            return False
        if "registered" in proc:
            return bool(proc.get("registered"))
        rpc_endpoint = proc.get("rpc_endpoint")
        return isinstance(rpc_endpoint, str) and bool(rpc_endpoint.strip())

    def _process_capabilities_probe_ready(self, process_id: str) -> bool:
        proc = self._process_status_map.get(process_id)
        if not isinstance(proc, dict):
            return False
        state = str(proc.get("state", "") or "").strip().upper()
        if state != "RUNNING":
            return False
        return self._process_is_registered(proc)

    def _process_capabilities_retry_allowed(self, process_id: str) -> bool:
        next_retry = self._proc_cap_retry_next_mono.get(process_id)
        if next_retry is None:
            return True
        return time.monotonic() >= float(next_retry)

    def _schedule_process_capabilities_retry(self, process_id: str) -> None:
        current = float(
            self._proc_cap_retry_delay_s.get(
                process_id, self._proc_cap_retry_initial_s
            )
        )
        delay = max(self._proc_cap_retry_initial_s, current)
        self._proc_cap_retry_next_mono[process_id] = time.monotonic() + delay
        self._proc_cap_retry_delay_s[process_id] = min(
            self._proc_cap_retry_max_s, delay * 2.0
        )

    def _reset_process_capabilities_retry(self, process_id: str) -> None:
        self._proc_cap_retry_next_mono.pop(process_id, None)
        self._proc_cap_retry_delay_s.pop(process_id, None)

    def _get_device_capabilities(
        self, device_id: str, *, force: bool = False
    ) -> dict[str, Any] | None:
        now = time.monotonic()
        if not force:
            t0 = self._cap_cache_mono.get(device_id)
            if t0 is not None and (now - t0) < self._cap_ttl_s:
                return self._cap_cache.get(device_id)

        resp = self._device_command(device_id, "capabilities", {})
        if not resp or not resp.get("ok"):
            return None
        result = resp.get("result")
        if not isinstance(result, dict):
            return None

        self._cap_cache[device_id] = result
        self._cap_cache_mono[device_id] = now
        members = result.get("members", [])
        if isinstance(members, list):
            self._members_last[device_id] = [m for m in members if isinstance(m, dict)]
        return result

    def _get_member_spec(self, device_id: str, name: str) -> dict[str, Any] | None:
        members = self._members_last.get(device_id, [])
        for member in members:
            if str(member.get("name", "")) == name:
                return member
        return None

    def _get_process_capabilities(
        self, process_id: str, *, force: bool = False
    ) -> dict[str, Any] | None:
        if not self._process_capabilities_probe_ready(process_id):
            return None
        if not force and not self._process_capabilities_retry_allowed(process_id):
            return None
        if not force and process_id in self._proc_cap_cache:
            return self._proc_cap_cache.get(process_id)

        resp = self._process_rpc(
            process_id,
            {"type": "process.capabilities", "params": {}},
        )
        if not resp or not resp.get("ok"):
            self._schedule_process_capabilities_retry(process_id)
            return None
        result = resp.get("result")
        if not isinstance(result, dict):
            self._schedule_process_capabilities_retry(process_id)
            return None

        self._proc_cap_cache[process_id] = result
        self._reset_process_capabilities_retry(process_id)
        members = result.get("members", [])
        if isinstance(members, list):
            self._proc_members_last[process_id] = [
                m for m in members if isinstance(m, dict)
            ]
        return result

    def _get_process_member_spec(
        self, process_id: str, name: str
    ) -> dict[str, Any] | None:
        members = self._proc_members_last.get(process_id, [])
        for member in members:
            if str(member.get("name", "")) == name:
                return member
        return None

    @staticmethod
    def _row_key_str(row_key: Any) -> str:
        key_value = row_key.value if hasattr(row_key, "value") else row_key
        return str(key_value)

    @staticmethod
    def _device_label(status: DeviceStatus) -> str:
        if status.is_remote:
            return f"⇄ {status.device_id}"
        return status.device_id

    def _refresh_snapshot(self) -> None:
        snapshot_changed = False

        old_status = self._device_status
        resp = self._rpc_call({"type": "device.list_status"})
        if resp and resp.get("ok"):
            result = resp.get("result", [])
            if isinstance(result, list):
                next_status: dict[str, DeviceStatus] = {}
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    device_id_raw = item.get("device_id")
                    if not device_id_raw:
                        continue
                    device_id = str(device_id_raw)
                    proc = item.get("driver_process", {}) or {}
                    status = DeviceStatus(
                        device_id=device_id,
                        registered=bool(item.get("registered")),
                        liveness=str(item.get("liveness")) if item.get("liveness") else None,
                        hb_age_s=item.get("hb_age_s"),
                        telemetry_age_s=item.get("telemetry_age_s"),
                        driver_state=str(item.get("driver_state"))
                        if item.get("driver_state")
                        else None,
                        device_state=str(item.get("device_state"))
                        if item.get("device_state")
                        else None,
                        device_reachable=item.get("device_reachable"),
                        last_error=item.get("last_error"),
                        driver_proc_state=str(proc.get("state")) if proc.get("state") else None,
                        driver_pid=proc.get("pid"),
                        driver_restart_count=int(proc.get("restart_count", 0)),
                        driver_last_exit_code=proc.get("last_exit_code"),
                        driver_last_error=proc.get("last_error"),
                        is_remote=bool(item.get("is_remote"))
                        or str(item.get("source_kind", "")).strip().lower()
                        == "federated",
                    )
                    next_status[status.device_id] = status
                    prev = old_status.get(status.device_id)
                    if prev and prev.driver_pid != status.driver_pid:
                        self._heartbeat_cache.pop(status.device_id, None)
                        self._telemetry_cache.pop(status.device_id, None)
                self._device_status = next_status
                self._prune_device_caches(active_device_ids=set(next_status.keys()))
                snapshot_changed = True

        proc_resp = self._rpc_call({"type": "manager.processes.list"})
        if proc_resp and proc_resp.get("ok"):
            raw = proc_resp.get("result", [])
            if isinstance(raw, list):
                old_proc_map = self._process_status_map
                next_proc_map: dict[str, Json] = {}
                for proc in raw:
                    if not isinstance(proc, dict):
                        continue
                    pid = str(proc.get("process_id", ""))
                    if not pid:
                        continue
                    next_proc_map[pid] = proc
                    prev = old_proc_map.get(pid)
                    if isinstance(prev, dict):
                        prev_pid = prev.get("pid")
                        next_pid = proc.get("pid")
                        if prev_pid != next_pid:
                            self._proc_cap_cache.pop(pid, None)
                            self._proc_members_last.pop(pid, None)
                            self._reset_process_capabilities_retry(pid)
                    if not self._process_is_registered(proc):
                        self._proc_cap_cache.pop(pid, None)
                        self._proc_members_last.pop(pid, None)
                self._process_status_map = next_proc_map
                self._processes = [
                    self._process_status_map[pid]
                    for pid in sorted(self._process_status_map.keys())
                ]
                stale_retry_keys = set(self._proc_cap_retry_next_mono) - set(
                    self._process_status_map
                )
                for pid in stale_retry_keys:
                    self._reset_process_capabilities_retry(pid)
                self._prune_process_caches(active_process_ids=set(next_proc_map.keys()))
                snapshot_changed = True

        if snapshot_changed:
            self._render_devices_table()
            self._render_processes_table()
            self._mark_inspector_dirty()
            self._render_inspector_if_needed(force=True)

    def _prune_device_caches(self, *, active_device_ids: set[str]) -> None:
        stale_telemetry = set(self._telemetry_cache) - active_device_ids
        for device_id in stale_telemetry:
            self._telemetry_cache.pop(device_id, None)

        stale_heartbeat = set(self._heartbeat_cache) - active_device_ids
        for device_id in stale_heartbeat:
            self._heartbeat_cache.pop(device_id, None)

        stale_caps = set(self._cap_cache) - active_device_ids
        for device_id in stale_caps:
            self._cap_cache.pop(device_id, None)
            self._cap_cache_mono.pop(device_id, None)
            self._members_last.pop(device_id, None)

        stale_member_fingerprints = [
            key
            for key in self._members_rendered_fingerprint
            if key.startswith("device:")
            and key.split(":", 1)[1] not in active_device_ids
        ]
        for key in stale_member_fingerprints:
            self._members_rendered_fingerprint.pop(key, None)

    def _prune_process_caches(self, *, active_process_ids: set[str]) -> None:
        stale_proc_caps = set(self._proc_cap_cache) - active_process_ids
        for process_id in stale_proc_caps:
            self._proc_cap_cache.pop(process_id, None)
            self._proc_members_last.pop(process_id, None)
            self._reset_process_capabilities_retry(process_id)

        stale_member_fingerprints = [
            key
            for key in self._members_rendered_fingerprint
            if key.startswith("process:")
            and key.split(":", 1)[1] not in active_process_ids
        ]
        for key in stale_member_fingerprints:
            self._members_rendered_fingerprint.pop(key, None)

    def _render_devices_table(self) -> None:
        devices = self.query_one("#devices_table", DataTable)
        cursor_device_id: str | None = None
        try:
            row_index = devices.cursor_row
            if row_index is not None and row_index >= 0:
                ordered_rows = devices.ordered_rows
                if row_index < len(ordered_rows):
                    cursor_device_id = self._row_key_str(ordered_rows[row_index].key)
        except Exception:
            cursor_device_id = None

        self._suppress_selection_events = True
        try:
            visible_ids: list[str] = []
            rows_to_render: list[tuple[str, list[str]]] = []
            needs_full_refresh = False

            key_map: dict[str, Any] = {}
            for row in devices.ordered_rows:
                row_key = row.key
                key_value = row_key.value if hasattr(row_key, "value") else row_key
                key_map[str(key_value)] = row_key
            remaining_keys: set[str] = set(key_map.keys())
            device_columns = [
                "device_id",
                "liveness",
                "driver_proc",
                "pid",
                "hb_age_s",
                "telemetry_age_s",
                "driver_state",
                "device_state",
                "last_error",
            ]

            for device_id in sorted(self._device_status.keys()):
                status = self._device_status[device_id]

                row_values = [
                    self._device_label(status),
                    status.liveness or "",
                    status.driver_proc_state or "",
                    str(status.driver_pid) if status.driver_pid is not None else "",
                    f"{status.hb_age_s:.1f}" if status.hb_age_s is not None else "",
                    f"{status.telemetry_age_s:.1f}"
                    if status.telemetry_age_s is not None
                    else "",
                    status.driver_state or "",
                    status.device_state or "",
                    (status.last_error or "")[:30],
                ]

                if status.device_id in remaining_keys and not needs_full_refresh:
                    row_key = key_map.get(status.device_id, status.device_id)
                    for col_name, value in zip(device_columns, row_values, strict=True):
                        try:
                            devices.update_cell(row_key, col_name, value)
                        except Exception:
                            needs_full_refresh = True
                            break
                    remaining_keys.discard(status.device_id)
                elif not needs_full_refresh:
                    devices.add_row(*row_values, key=status.device_id)
                rows_to_render.append((status.device_id, row_values))
                visible_ids.append(status.device_id)

            if needs_full_refresh:
                devices.clear()
                for device_id, row_values in rows_to_render:
                    devices.add_row(*row_values, key=device_id)
            else:
                for key in remaining_keys:
                    try:
                        devices.remove_row(key_map.get(key, key))
                    except Exception:
                        pass

            if self._selected_device_id not in visible_ids:
                if self._has_user_selection:
                    self._selected_device_id = None
                else:
                    self._selected_device_id = visible_ids[0] if visible_ids else None

            target_id = None
            if cursor_device_id in visible_ids:
                target_id = cursor_device_id
            elif self._selected_device_id in visible_ids:
                target_id = self._selected_device_id
            elif visible_ids:
                target_id = visible_ids[0]

            if target_id is not None:
                try:
                    target_index = visible_ids.index(target_id)
                    devices.move_cursor(row=target_index, column=0)
                except Exception:
                    pass
        finally:
            self.call_later(self._end_selection_suppression)

    def _render_processes_table(self) -> None:
        processes = self.query_one("#processes_table", DataTable)
        self._suppress_selection_events = True
        try:
            visible_ids: list[str] = []
            rows_to_render: list[tuple[str, list[str]]] = []
            needs_full_refresh = False
            key_map: dict[str, Any] = {}
            for row in processes.ordered_rows:
                row_key = row.key
                key_value = row_key.value if hasattr(row_key, "value") else row_key
                key_map[str(key_value)] = row_key
            remaining_keys: set[str] = set(key_map.keys())
            process_columns = [
                "process_id",
                "state",
                "pid",
                "hb_age_s",
                "restart_count",
                "last_exit_code",
                "last_error",
            ]
            for proc in self._processes:
                pid = str(proc.get("process_id", ""))
                hb_age = ""
                hb_age_val = proc.get("hb_age_s")
                if isinstance(hb_age_val, (int, float)):
                    hb_age = f"{max(0.0, float(hb_age_val)):.1f}"
                else:
                    last_hb_t_mono = proc.get("last_hb_t_mono")
                    if isinstance(last_hb_t_mono, (int, float)):
                        hb_age = f"{max(0.0, time.monotonic() - float(last_hb_t_mono)):.1f}"
                row_values = [
                    pid,
                    str(proc.get("state", "")),
                    str(proc.get("pid", "") or ""),
                    hb_age,
                    str(proc.get("restart_count", "")),
                    str(proc.get("last_exit_code", "") or ""),
                    str(proc.get("last_error", "") or "")[:40],
                ]

                if pid in remaining_keys and not needs_full_refresh:
                    row_key = key_map.get(pid, pid)
                    for col_name, value in zip(
                        process_columns, row_values, strict=True
                    ):
                        try:
                            processes.update_cell(row_key, col_name, value)
                        except Exception:
                            needs_full_refresh = True
                            break
                    remaining_keys.discard(pid)
                elif not needs_full_refresh:
                    processes.add_row(*row_values, key=pid)
                rows_to_render.append((pid, row_values))
                visible_ids.append(pid)

            if needs_full_refresh:
                processes.clear()
                for pid, row_values in rows_to_render:
                    processes.add_row(*row_values, key=pid)
            else:
                for key in remaining_keys:
                    try:
                        processes.remove_row(key_map.get(key, key))
                    except Exception:
                        pass

            if self._selected_process_id not in visible_ids:
                if self._has_user_process_selection:
                    self._selected_process_id = None
                else:
                    self._selected_process_id = visible_ids[0] if visible_ids else None

            if (
                self._selected_process_id is not None
                and self._selected_process_id in visible_ids
            ):
                try:
                    target_index = visible_ids.index(self._selected_process_id)
                    processes.move_cursor(row=target_index, column=0)
                except Exception:
                    pass
        finally:
            self.call_later(self._end_selection_suppression)

    def _end_selection_suppression(self) -> None:
        self._suppress_selection_events = False

    def _mark_inspector_dirty(self) -> None:
        self._inspector_dirty = True

    def _bump_error(self, key: str) -> None:
        self._error_counts[key] = self._error_counts.get(key, 0) + 1

    def _render_inspector_if_needed(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force:
            if not self._inspector_dirty:
                return
            if (now - self._last_inspector_render) < self._inspector_min_period_s:
                return
        self._inspector_dirty = False
        self._last_inspector_render = now
        self._render_inspector()

    def _render_inspector(self) -> None:
        if self._members_source == "process":
            self._render_process_inspector()
        else:
            self._render_device_inspector()
        self._render_members_table()

        process_table = self.query_one("#process_table", DataTable)
        process_table.clear()

    def _render_device_inspector(self) -> None:
        device_id = self._selected_device_id
        telemetry = self.query_one("#telemetry_table", DataTable)
        telemetry.clear()
        if device_id:
            telemetry_cache = self._telemetry_cache.get(device_id, {})
            for name, entry in telemetry_cache.items():
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("ts", {})
                age = None
                if isinstance(ts, dict) and "t_mono" in ts:
                    age = max(0.0, time.monotonic() - float(ts["t_mono"]))
                telemetry.add_row(
                    name,
                    str(entry.get("value")),
                    str(entry.get("units")),
                    str(entry.get("quality")),
                    f"{age:.1f}" if age is not None else "",
                )

        heartbeat = self.query_one("#heartbeat_table", DataTable)
        heartbeat.clear()
        if device_id:
            hb = self._heartbeat_cache.get(device_id, {})
            if isinstance(hb, dict) and hb:
                heartbeat.add_row(
                    str(hb.get("pid", "")),
                    str(hb.get("seq", "")),
                    str(hb.get("driver_state", "")),
                    str(hb.get("device_state", "")),
                    str(hb.get("device_reachable", "")),
                    str(hb.get("loop_lag_s", "")),
                    str(hb.get("last_error", ""))[:40],
                )

        driver = self.query_one("#driver_table", DataTable)
        driver.clear()
        if device_id:
            status = self._device_status.get(device_id)
            if status:
                driver.add_row(
                    status.driver_proc_state or "",
                    str(status.driver_pid) if status.driver_pid is not None else "",
                    str(status.driver_restart_count),
                    str(status.driver_last_exit_code or ""),
                    (status.driver_last_error or "")[:40],
                )

    def _render_process_inspector(self) -> None:
        process_id = self._selected_process_id
        telemetry = self.query_one("#telemetry_table", DataTable)
        telemetry.clear()
        heartbeat = self.query_one("#heartbeat_table", DataTable)
        heartbeat.clear()
        driver = self.query_one("#driver_table", DataTable)
        driver.clear()

        if not process_id:
            return

        selected = None
        for proc in self._processes:
            if str(proc.get("process_id", "")) == process_id:
                selected = proc
                break
        if selected is None:
            return

        driver.add_row(
            str(selected.get("state", "")),
            str(selected.get("pid", "") or ""),
            str(selected.get("restart_count", "") or ""),
            str(selected.get("last_exit_code", "") or ""),
            str(selected.get("last_error", "") or "")[:40],
        )

        hb_age = ""
        last_hb_t_mono = selected.get("last_hb_t_mono")
        hb_age_val = selected.get("hb_age_s")
        if isinstance(hb_age_val, (int, float)):
            hb_age = f"{max(0.0, float(hb_age_val)):.1f}"
        elif isinstance(last_hb_t_mono, (int, float)):
            hb_age = f"{max(0.0, time.monotonic() - float(last_hb_t_mono)):.1f}"
        last_hb_t_wall = selected.get("last_hb_t_wall")
        heartbeat_endpoint = selected.get("heartbeat_endpoint")
        if any(
            v not in (None, "")
            for v in (hb_age, last_hb_t_wall, last_hb_t_mono, heartbeat_endpoint)
        ):
            heartbeat.add_row(
                hb_age,
                str(last_hb_t_wall or ""),
                str(last_hb_t_mono or ""),
                str(heartbeat_endpoint or ""),
            )

        primary_keys = {
            "state",
            "pid",
            "restart_count",
            "last_exit_code",
            "last_error",
        }
        telemetry_keys = ["process_id"] + sorted(
            [k for k in selected.keys() if k not in primary_keys and k != "process_id"]
        )
        for key in telemetry_keys:
            value = selected.get(key, "")
            if isinstance(value, (dict, list)):
                text = json.dumps(value)
            else:
                text = str(value)
            if len(text) > 120:
                text = text[:117] + "..."
            telemetry.add_row(key, text)

    def _render_members_table(self) -> None:
        table = self.query_one("#members_table", DataTable)
        if self._members_source == "process":
            process_id = self._selected_process_id
            if not process_id:
                table.clear()
                return
            context_key = f"process:{process_id}"
            if process_id not in self._proc_members_last:
                proc = None
                for item in self._processes:
                    if str(item.get("process_id", "")) == process_id:
                        proc = item
                        break
                state = str(proc.get("state", "")) if proc else ""
                if state == "RUNNING":
                    self._get_process_capabilities(process_id)
            members = self._proc_members_last.get(process_id, [])
        else:
            device_id = self._selected_device_id
            if not device_id:
                table.clear()
                return
            context_key = f"device:{device_id}"

            if device_id not in self._members_last:
                status = self._device_status.get(device_id)
                if status and not self._status_driver_stopped(status):
                    if status.device_state != "DISCONNECTED":
                        self._get_device_capabilities(device_id)

            members = self._members_last.get(device_id, [])

        preserve_scroll = context_key == self._members_context_key
        self._members_context_key = context_key

        members_render = sorted(
            [m for m in members if isinstance(m, dict)],
            key=lambda d: (str(d.get("kind", "")), str(d.get("name", ""))),
        )
        try:
            fingerprint = json.dumps(members_render, sort_keys=True, default=str)
        except Exception:
            fingerprint = str(members_render)
        if preserve_scroll and fingerprint == self._members_rendered_fingerprint.get(
            context_key
        ):
            return
        self._members_rendered_fingerprint[context_key] = fingerprint

        cursor_key: str | None = None
        try:
            row_index = table.cursor_row
            if row_index is not None and row_index >= 0:
                row = table.get_row_at(row_index)
                if row:
                    source = str(row[4]) if len(row) > 4 else ""
                    kind = str(row[1]) if len(row) > 1 else ""
                    name = str(row[0]) if row else ""
                    source_key = source or self._members_source
                    cursor_key = f"{source_key}:{kind}:{name}"
        except Exception:
            cursor_key = None

        scroll_x = table.scroll_x
        scroll_y = table.scroll_y
        self._suppress_member_selection = True
        try:
            table.clear()
            visible_keys: list[str] = []
            for m in members_render:
                name = str(m.get("name", ""))
                kind = str(m.get("kind", ""))
                readable = bool(m.get("readable", False))
                settable = bool(m.get("settable", False))
                rw = ("R" if readable else "") + ("W" if settable else "")
                if kind == "method":
                    typ = str(m.get("return_annotation") or "")
                else:
                    typ = str(m.get("value_annotation") or "")
                source = str(m.get("source", ""))
                doc = str(m.get("doc", "") or "")[:40]
                source_key = source or self._members_source
                key = f"{source_key}:{kind}:{name}"
                visible_keys.append(key)
                table.add_row(name, kind, rw, typ, source, doc, key=key)

            if self._selected_member_key not in visible_keys:
                if self._has_user_member_selection:
                    self._selected_member_key = None
                else:
                    self._selected_member_key = (
                        visible_keys[0] if visible_keys else None
                    )

            target_key = None
            if cursor_key in visible_keys:
                target_key = cursor_key
            elif self._selected_member_key in visible_keys:
                target_key = self._selected_member_key
            elif visible_keys:
                target_key = visible_keys[0]

            if target_key is not None:
                try:
                    target_index = visible_keys.index(target_key)
                    table.move_cursor(
                        row=target_index,
                        column=0,
                        scroll=not preserve_scroll,
                    )
                except Exception:
                    pass
            if preserve_scroll:
                table.call_after_refresh(
                    self._restore_members_scroll, scroll_x, scroll_y
                )
        finally:
            self.call_later(self._end_member_selection_suppression)

    def _end_member_selection_suppression(self) -> None:
        self._suppress_member_selection = False

    def _render_errors_table(self) -> None:
        table = self.query_one("#errors_table", DataTable)
        table.clear()
        for entry in reversed(self._errors):
            t_wall = entry.get("t_wall")
            time_str = ""
            if isinstance(t_wall, (int, float)):
                try:
                    time_str = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(float(t_wall))
                    )
                except Exception:
                    time_str = ""
            message = str(entry.get("message", ""))
            message = message.replace("\r\n", " | ").replace("\n", " | ").replace("\r", " | ")
            if len(message) > 220:
                message = message[:217] + "..."
            severity = str(entry.get("severity", ""))
            sev_cell: str | Text
            if severity == "critical":
                sev_cell = Text(severity, style="bold red")
            elif severity == "error":
                sev_cell = Text(severity, style="red")
            elif severity == "warning":
                sev_cell = Text(severity, style="yellow")
            else:
                sev_cell = severity
            table.add_row(
                time_str,
                sev_cell,
                str(entry.get("source", "")),
                str(entry.get("id", "")),
                message,
            )

    def _toast_once(
        self, *, key: str, fingerprint: str, message: str, severity: str
    ) -> bool:
        now = time.monotonic()
        last = self._last_toast_by_key.get(key)
        if last is not None:
            last_fp, last_t = last
            if fingerprint == last_fp and (now - last_t) < self._toast_repeat_s:
                return False
            if (now - last_t) < self._toast_cooldown_s:
                return False

        self._last_toast_by_key[key] = (fingerprint, now)
        try:
            self.notify(message, severity=severity)
        except Exception:
            self.notify(message)
        return True

    def _record_error(
        self,
        *,
        source: str,
        id_: str,
        topic: str,
        message: str,
        severity: str,
        fingerprint: str,
        t_mono: float | None = None,
        t_wall: float | None = None,
        render: bool = True,
    ) -> None:
        if t_mono is None:
            t_mono = time.monotonic()
        if t_wall is None:
            t_wall = time.time()
        self._errors.append(
            {
                "t_mono": t_mono,
                "t_wall": t_wall,
                "severity": severity,
                "source": source,
                "id": id_,
                "topic": topic,
                "message": message,
                "fingerprint": fingerprint,
            }
        )
        if render:
            self._render_errors_table()

    def _record_action_error(self, *, source: str, id_: str, message: str) -> None:
        self._record_error(
            source=source,
            id_=id_,
            topic="ui.action",
            message=message,
            severity="error",
            fingerprint=f"{source}:{id_}:{message}",
        )

    def _maybe_emit_error_ui(
        self, topic: str, payload: Json, *, prev_hb: Json | None = None
    ) -> None:
        # Most failures are now surfaced through manager.log; keep this only for
        # heartbeat fault transitions that are not auto-promoted there.
        if topic != "manager.heartbeat":
            return
        device_id = str(payload.get("device_id", "unknown"))
        driver_state = str(payload.get("driver_state", ""))
        device_state = str(payload.get("device_state", ""))
        reachable = payload.get("device_reachable") is True
        last_error = str(payload.get("last_error", "") or "").strip()
        driver_state_norm = driver_state.upper()
        device_state_norm = device_state.upper()
        is_fault = "FAULT" in driver_state_norm or "FAULT" in device_state_norm
        # Do not surface intentional disconnected startup as an error/warning.
        # Example: stack startup with connect disabled -> INIT/DISCONNECTED.
        if (
            (not reachable)
            and device_state_norm == "DISCONNECTED"
            and not last_error
            and not is_fault
        ):
            return
        if not (last_error or not reachable or is_fault):
            return

        fingerprint = json.dumps(
            {
                "driver_state": driver_state,
                "device_state": device_state,
                "device_reachable": payload.get("device_reachable"),
                "last_error": last_error,
            },
            sort_keys=True,
        )
        if prev_hb is not None:
            prev_fp = json.dumps(
                {
                    "driver_state": str(prev_hb.get("driver_state", "")),
                    "device_state": str(prev_hb.get("device_state", "")),
                    "device_reachable": prev_hb.get("device_reachable"),
                    "last_error": str(prev_hb.get("last_error", "") or ""),
                },
                sort_keys=True,
            )
            if prev_fp == fingerprint:
                return

        # Treat only explicit failures as errors (connect failures, faults).
        # Bare unreachable without an error message is a warning signal.
        severity = "error" if (last_error or is_fault) else "warning"
        message = f"device {device_id}: {driver_state}/{device_state}"
        if not reachable:
            message += " unreachable"
        if last_error:
            message += f" {last_error}"
        key = f"dev:{device_id}:heartbeat"
        if self._toast_once(
            key=key,
            fingerprint=fingerprint,
            message=message,
            severity=severity,
        ):
            self._record_error(
                source="device",
                id_=device_id,
                topic=topic,
                message=message,
                severity=severity,
                fingerprint=fingerprint,
                render=False,
            )

    def _pub_thread(self) -> None:
        try:
            while not self._stop_event.is_set():
                if self._sub is None:
                    try:
                        self._sub = self._new_sub_socket()
                    except Exception:
                        if self._stop_event.is_set():
                            break
                        self._bump_error("pub.open")
                        time.sleep(0.2)
                        continue
                if self._sub_reconnect_event.is_set():
                    self._sub_reconnect_event.clear()
                    try:
                        self._reset_sub_socket()
                    except Exception:
                        if self._stop_event.is_set():
                            break
                        self._bump_error("pub.reconnect")
                        continue
                try:
                    sub = self._sub
                    if sub is None:
                        continue
                    topic_b, payload_b = sub.recv_multipart()
                except zmq.Again:
                    continue
                except Exception:
                    if self._stop_event.is_set():
                        break
                    self._bump_error("pub.recv")
                    continue

                topic = topic_b.decode("utf-8", errors="replace")
                try:
                    payload = safe_json_loads(payload_b)
                except Exception:
                    payload = None
                if not isinstance(payload, dict):
                    self._bump_error("pub.decode")
                    continue

                if topic == "manager.chunk_ready":
                    device_raw = payload.get("device_id")
                    stream_raw = payload.get("stream")
                    if device_raw is None or stream_raw is None:
                        continue
                    device_id = str(device_raw)
                    stream = str(stream_raw)
                    if device_id and device_id != "None" and stream and stream != "None":
                        with self._chunk_lock:
                            self._chunk_cache[(device_id, stream)] = (topic, payload)
                    continue

                self._enqueue_pub_message(topic, payload)
        finally:
            try:
                if self._sub is not None:
                    self._sub.close(0)
            except Exception:
                pass
            self._sub = None

    def _enqueue_pub_message(self, topic: str, payload: Json) -> None:
        try:
            self._pub_queue.put_nowait((topic, payload))
            return
        except queue.Full:
            if self._pub_queue_overflow_policy == "drop_newest":
                self._dropped_pub_messages += 1
                return

        dropped = 0
        if self._pub_queue_overflow_policy == "drop_oldest":
            try:
                self._pub_queue.get_nowait()
                dropped += 1
            except queue.Empty:
                pass
            try:
                self._pub_queue.put_nowait((topic, payload))
                self._dropped_pub_messages += dropped
                return
            except queue.Full:
                dropped += 1
                self._dropped_pub_messages += dropped
                return

        self._dropped_pub_messages += 1

    def _drain_pub_queue(self) -> None:
        if not self.streaming_enabled:
            return

        log = self.query_one("#event_log", RichLog)
        errors_dirty = False
        with self._chunk_lock:
            cached = list(self._chunk_cache.values())
            self._chunk_cache.clear()
        for topic, payload in cached:
            self._enqueue_pub_message(topic, payload)

        for _ in range(self._pub_drain_max):
            try:
                topic, payload = self._pub_queue.get_nowait()
            except queue.Empty:
                break

            prev_hb: Json | None = None

            if topic == "manager.telemetry_update":
                device_raw = payload.get("device_id")
                if device_raw is None:
                    continue
                device_id = str(device_raw)
                signals = payload.get("signals", {})
                if isinstance(signals, dict):
                    self._telemetry_cache[device_id] = signals
                    if device_id == self._selected_device_id:
                        self._mark_inspector_dirty()
            elif topic == "manager.heartbeat":
                device_raw = payload.get("device_id")
                if device_raw is None:
                    continue
                device_id = str(device_raw)
                status = self._device_status.get(device_id)
                hb_pid = payload.get("pid")
                if (
                    status is not None
                    and status.driver_pid is not None
                    and hb_pid != status.driver_pid
                ):
                    continue
                prev_hb = self._heartbeat_cache.get(device_id)
                self._heartbeat_cache[device_id] = payload
                if device_id == self._selected_device_id:
                    self._mark_inspector_dirty()

            if topic == "manager.log":
                self._ingest_manager_log_entry(payload)
                errors_dirty = True
            else:
                before_count = len(self._errors)
                self._maybe_emit_error_ui(topic, payload, prev_hb=prev_hb)
                if len(self._errors) != before_count:
                    errors_dirty = True

            self._topic_counts[topic] = self._topic_counts.get(topic, 0) + 1
            if topic not in self._topic_visible:
                self._topic_visible[topic] = self._default_topic_visibility(topic)

            if self._topic_enabled_for_event_log(topic, payload):
                try:
                    if topic == "manager.log":
                        log.write(self._format_manager_log_event_line(payload))
                    else:
                        try:
                            payload_text = json.dumps(payload)
                        except Exception:
                            payload_text = str(payload)
                        log.write(f"{topic} {payload_text[:200]}")
                except Exception:
                    self._bump_error("log.write")
        if errors_dirty:
            self._render_errors_table()
        dropped = self.query_one("#dropped_status", Static)
        dropped.update(f"Dropped: {self._dropped_pub_messages}")

        self._render_inspector_if_needed()

    def _notify_rpc_result(
        self, action: str, device_id: str, resp: Json | None
    ) -> None:
        if resp is None:
            self.notify(
                f"{action} failed: {device_id} (timeout)",
                severity="error",
            )
            self._record_action_error(
                source="device",
                id_=device_id,
                message=f"{action} failed: {device_id} (timeout)",
            )
            return
        if resp.get("ok"):
            self.notify(f"{action} sent: {device_id}")
            return
        err = resp.get("error", "unknown error")
        self.notify(
            f"{action} failed: {device_id} ({err})",
            severity="error",
        )
        self._record_action_error(
            source="device",
            id_=device_id,
            message=f"{action} failed: {device_id} ({err})",
        )

    def _log_action_result(self, message: str) -> None:
        log = self.query_one("#event_log", RichLog)
        try:
            log.write(message[:200])
        except Exception:
            pass

    def _reconnect_backend(self) -> bool:
        self._set_backend_status("Backend: reconnecting")
        self._log_action_result("Reconnecting backend...")
        self._reset_rpc_socket()
        self._request_sub_reconnect()
        ready = bool(self._rpc_call({"type": "manager.info.identity"}))
        if not ready:
            self._set_backend_status("Backend: unavailable")
            self._log_action_result("Backend reconnect failed")
            return False
        self._load_manager_log_tail_bootstrap()
        self._refresh_snapshot()
        self._set_backend_status("Backend: connected")
        self._log_action_result("Backend reconnected")
        return True

    def _format_result(self, result: Any) -> str:
        if isinstance(result, dict) and "__enum__" in result and "name" in result:
            enum_type = str(result.get("__enum__"))
            enum_name = str(result.get("name"))
            enum_value = result.get("value")
            if enum_value is None:
                return f"{enum_type}.{enum_name}"
            return f"{enum_type}.{enum_name} ({enum_value})"
        try:
            return json.dumps(result)
        except Exception:
            return str(result)

    @on(DataTable.RowSelected, "#devices_table")
    def _on_device_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#devices_table", DataTable)
        if self._suppress_selection_events and not self._is_table_focused(table):
            return
        if not self._is_table_focused(table):
            return
        self._selected_device_id = self._row_key_str(event.row_key)
        self._has_user_selection = True
        self._set_inspector_mode("device")
        self._mark_inspector_dirty()
        self._render_inspector_if_needed(force=True)

    @on(DataTable.RowHighlighted, "#devices_table")
    def _on_device_cursor_moved(self, event: DataTable.RowHighlighted) -> None:
        table = self.query_one("#devices_table", DataTable)
        if self._suppress_selection_events and not self._is_table_focused(table):
            return
        if not self._is_table_focused(table):
            return
        self._selected_device_id = self._row_key_str(event.row_key)
        self._set_inspector_mode("device")
        self._mark_inspector_dirty()
        self._render_inspector_if_needed(force=True)

    @on(DataTable.RowSelected, "#processes_table")
    def _on_process_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#processes_table", DataTable)
        if self._suppress_selection_events and not self._is_table_focused(table):
            return
        if not self._is_table_focused(table):
            return
        row = table.get_row(event.row_key)
        if row:
            self._selected_process_id = str(row[0])
            self._has_user_process_selection = True
            self._set_inspector_mode("process")
            self._mark_inspector_dirty()
            self._render_inspector_if_needed(force=True)

    @on(DataTable.RowHighlighted, "#processes_table")
    def _on_process_cursor_moved(self, event: DataTable.RowHighlighted) -> None:
        table = self.query_one("#processes_table", DataTable)
        if self._suppress_selection_events and not self._is_table_focused(table):
            return
        if not self._is_table_focused(table):
            return
        try:
            row = table.get_row(event.row_key)
        except Exception:
            return
        if row:
            self._selected_process_id = str(row[0])
            self._set_inspector_mode("process")
            self._mark_inspector_dirty()
            self._render_inspector_if_needed(force=True)

    @on(DataTable.RowSelected, "#members_table")
    def _on_member_selected(self, event: DataTable.RowSelected) -> None:
        if self._suppress_member_selection:
            return
        table = self.query_one("#members_table", DataTable)
        row = table.get_row(event.row_key)
        if row:
            source = str(row[4]) if len(row) > 4 else ""
            kind = str(row[1]) if len(row) > 1 else ""
            name = str(row[0]) if row else ""
            source_key = source or self._members_source
            key = f"{source_key}:{kind}:{name}"
            self._selected_member_key = key
            self._has_user_member_selection = True

    @on(DataTable.RowHighlighted, "#members_table")
    def _on_member_cursor_moved(self, event: DataTable.RowHighlighted) -> None:
        if self._suppress_member_selection:
            return
        table = self.query_one("#members_table", DataTable)
        try:
            row = table.get_row(event.row_key)
        except Exception:
            return
        if row:
            source = str(row[4]) if len(row) > 4 else ""
            kind = str(row[1]) if len(row) > 1 else ""
            name = str(row[0]) if row else ""
            source_key = source or self._members_source
            key = f"{source_key}:{kind}:{name}"
            self._selected_member_key = key

    def _selected_device(self) -> str | None:
        return self._selected_device_id

    def _selected_process(self) -> str | None:
        return self._selected_process_id

    def _get_process_record(self, process_id: str | None) -> Json | None:
        if not process_id:
            return None
        for proc in self._processes:
            if str(proc.get("process_id", "")) == process_id:
                return proc
        return None

    def _status_process_started(self, proc: Json | None) -> bool:
        if not proc:
            return False
        state = str(proc.get("state", ""))
        if state in {"STARTING", "RUNNING", "STOPPING"}:
            return True
        return bool(proc.get("pid"))

    def _status_process_stopped(self, proc: Json | None) -> bool:
        if not proc:
            return False
        state = str(proc.get("state", ""))
        if state in {"STOPPED", "EXITED", "FAILED", "CRASHLOOP"}:
            return True
        return not proc.get("pid")

    def _status_driver_started(self, status: DeviceStatus | None) -> bool:
        if status is None:
            return False
        if status.driver_proc_state in {"STARTING", "RUNNING", "STOPPING"}:
            return True
        return status.driver_pid is not None

    def _status_driver_stopped(self, status: DeviceStatus | None) -> bool:
        if status is None:
            return False
        if status.driver_proc_state in {"STOPPED", "EXITED", "FAILED"}:
            return True
        return status.driver_pid is None

    def action_toggle_streaming(self) -> None:
        self.streaming_enabled = not self.streaming_enabled
        status = self.query_one("#streaming_status", Static)
        status.update("Streaming: ON" if self.streaming_enabled else "Streaming: OFF")

    def action_quit(self) -> None:
        try:
            self._rpc_call({"type": "manager.control.shutdown"})
        except Exception:
            pass
        self.exit()

    def action_reconnect_backend(self) -> None:
        try:
            if self._reconnect_backend():
                self.notify("Backend reconnected")
                return
            self.notify("Backend reconnect failed", severity="warning")
        except Exception as exc:
            self._set_backend_status("Backend: unavailable")
            self._log_action_result(f"Backend reconnect error: {exc}")
            self.notify(f"Backend reconnect error: {exc}", severity="error")

    def action_capabilities_refresh(self) -> None:
        if self._members_source == "process":
            process_id = self._selected_process_id
            if not process_id:
                return
            resp = self._get_process_capabilities(process_id, force=True)
            if resp is None:
                self.notify(f"Capabilities refresh failed: {process_id}")
                return
            self._render_members_table()
            self.notify(f"Capabilities refreshed: {process_id}")
            return

        device_id = self._selected_device()
        if not device_id:
            return
        resp = self._get_device_capabilities(device_id, force=True)
        if resp is None:
            self.notify(f"Capabilities refresh failed: {device_id}")
            return
        self._render_members_table()
        self.notify(f"Capabilities refreshed: {device_id}")

    def action_member_primary(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        table = self.query_one("#members_table", DataTable)
        row_index = table.cursor_row
        if row_index is None or row_index < 0:
            return
        row = table.get_row_at(row_index)
        if not row:
            return
        name = str(row[0])
        kind = str(row[1])

        if self._members_source == "process":
            process_id = self._selected_process_id
            if not process_id:
                return
            if kind != "method":
                self.notify(f"Process member not invokable: {name}")
                return
            spec = self._get_process_member_spec(process_id, name) or {}
            params_spec = spec.get("params")
            if not isinstance(params_spec, list):
                params_spec = None

            if not params_spec:
                resp = self._process_rpc(process_id, {"type": name, "params": {}})
                if resp and resp.get("ok"):
                    result = resp.get("result")
                    text = self._format_result(result) if result is not None else "ok"
                    self._log_action_result(
                        f"PROC CALL {process_id}.{name} kwargs={{}} -> {text}"
                    )
                    self.notify(f"Call ok: {process_id}.{name}")
                else:
                    err = resp.get("error", "unknown error") if resp else "timeout"
                    err_text = json.dumps(err) if isinstance(err, dict) else str(err)
                    self._log_action_result(
                        f"PROC CALL {process_id}.{name} kwargs={{}} -> error {err_text}"
                    )
                    self.notify(
                        f"Call failed: {process_id}.{name} ({err_text})",
                        severity="error",
                    )
                    self._record_action_error(
                        source="process",
                        id_=process_id,
                        message=f"Call failed: {process_id}.{name} ({err_text})",
                    )
                return

            def _on_dismiss(params: dict[str, Any] | None) -> None:
                if params is None:
                    return
                resp = self._process_rpc(
                    process_id, {"type": name, "params": params}
                )
                if resp and resp.get("ok"):
                    result = resp.get("result")
                    text = self._format_result(result) if result is not None else "ok"
                    self._log_action_result(
                        f"PROC CALL {process_id}.{name} kwargs={json.dumps(params)} -> {text}"
                    )
                    self.notify(f"Call ok: {process_id}.{name}")
                else:
                    err = resp.get("error", "unknown error") if resp else "timeout"
                    err_text = json.dumps(err) if isinstance(err, dict) else str(err)
                    self._log_action_result(
                        f"PROC CALL {process_id}.{name} kwargs={json.dumps(params)} -> error {err_text}"
                    )
                    self.notify(
                        f"Call failed: {process_id}.{name} ({err_text})",
                        severity="error",
                    )
                    self._record_action_error(
                        source="process",
                        id_=process_id,
                        message=f"Call failed: {process_id}.{name} ({err_text})",
                    )

            self.push_screen(InvokeMemberScreen(name, params_spec), _on_dismiss)
            return

        device_id = self._selected_device()
        if not device_id:
            return
        if kind == "method":
            spec = self._get_member_spec(device_id, name) or {}
            params_spec = spec.get("params")
            if not isinstance(params_spec, list):
                params_spec = None

            if not params_spec:
                resp = self._device_command(device_id, name, {})
                if resp and resp.get("ok"):
                    result = resp.get("result")
                    text = self._format_result(result) if result is not None else "ok"
                    self._log_action_result(
                        f"CALL {device_id}.{name} kwargs={{}} -> {text}"
                    )
                    self.notify(f"Call ok: {device_id}.{name}")
                else:
                    err = resp.get("error", "unknown error") if resp else "timeout"
                    self._log_action_result(
                        f"CALL {device_id}.{name} kwargs={{}} -> error {err}"
                    )
                    self.notify(
                        f"Call failed: {device_id}.{name} ({err})",
                        severity="error",
                    )
                    self._record_action_error(
                        source="device",
                        id_=device_id,
                        message=f"Call failed: {device_id}.{name} ({err})",
                    )
                return

            def _on_dismiss(params: dict[str, Any] | None) -> None:
                if params is None:
                    return
                resp = self._device_command(device_id, name, params)
                if resp and resp.get("ok"):
                    result = resp.get("result")
                    text = self._format_result(result) if result is not None else "ok"
                    self._log_action_result(
                        f"CALL {device_id}.{name} kwargs={json.dumps(params)} -> {text}"
                    )
                    self.notify(f"Call ok: {device_id}.{name}")
                else:
                    err = resp.get("error", "unknown error") if resp else "timeout"
                    self._log_action_result(
                        f"CALL {device_id}.{name} kwargs={json.dumps(params)} -> error {err}"
                    )
                    self.notify(
                        f"Call failed: {device_id}.{name} ({err})",
                        severity="error",
                    )
                    self._record_action_error(
                        source="device",
                        id_=device_id,
                        message=f"Call failed: {device_id}.{name} ({err})",
                    )

            self.push_screen(InvokeMemberScreen(name, params_spec), _on_dismiss)
            return

        resp = self._device_command(device_id, "get", {"name": name})
        if resp and resp.get("ok"):
            result = resp.get("result")
            text = self._format_result(result)
            self._log_action_result(f"GET {device_id}.{name} -> {text}")
            self.notify(f"Get ok: {device_id}.{name}")
        else:
            err = resp.get("error", "unknown error") if resp else "timeout"
            self._log_action_result(f"GET {device_id}.{name} -> error {err}")
            self.notify(
                f"Get failed: {device_id}.{name} ({err})",
                severity="error",
            )
            self._record_action_error(
                source="device",
                id_=device_id,
                message=f"Get failed: {device_id}.{name} ({err})",
            )

    def action_member_set(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        table = self.query_one("#members_table", DataTable)
        row_index = table.cursor_row
        if row_index is None or row_index < 0:
            return
        row = table.get_row_at(row_index)
        if not row:
            return
        name = str(row[0])
        rw = str(row[2])
        if self._members_source == "process":
            self.notify(f"Process members are not settable: {name}")
            return

        device_id = self._selected_device()
        if not device_id:
            return
        if "W" not in rw:
            self.notify(f"Member not settable: {name}")
            return

        def _on_dismiss(value: object | None) -> None:
            if value is None:
                return
            resp = self._device_command(
                device_id, "set", {"name": name, "value": value}
            )
            if resp and resp.get("ok"):
                self._log_action_result(
                    f"SET {device_id}.{name} = {json.dumps(value)} -> ok"
                )
                self.notify(f"Set ok: {device_id}.{name}")
            else:
                err = resp.get("error", "unknown error") if resp else "timeout"
                self._log_action_result(
                    f"SET {device_id}.{name} = {json.dumps(value)} -> error {err}"
                )
                self.notify(
                    f"Set failed: {device_id}.{name} ({err})",
                    severity="error",
                )
                self._record_action_error(
                    source="device",
                    id_=device_id,
                    message=f"Set failed: {device_id}.{name} ({err})",
                )

        self.push_screen(SetMemberScreen(name), _on_dismiss)

    def action_topics(self) -> None:
        for t in self._topic_counts.keys():
            self._topic_visible.setdefault(t, self._default_topic_visibility(t))

        def _on_dismiss(result: dict[str, bool] | None) -> None:
            if not result:
                return
            self._topic_visible.update(result)
            shown = sum(1 for v in self._topic_visible.values() if v)
            total = len(self._topic_visible)
            self.query_one("#event_log", RichLog).write(
                f"Topic visibility updated: showing {shown}/{total}"
            )

        self.push_screen(
            TopicFilterScreen(
                topic_counts=self._topic_counts,
                topic_visible=self._topic_visible,
            ),
            _on_dismiss,
        )

    def action_clear_log(self) -> None:
        log = self.query_one("#event_log", RichLog)
        try:
            log.clear()
            self.notify("Event log cleared")
        except Exception:
            self._bump_error("log.clear")

    def action_device_connect(self) -> None:
        device_id = self._selected_device()
        if not device_id:
            return
        resp = self._rpc_call({"type": "device.connect", "device_id": device_id})
        self._notify_rpc_result("Device connect", device_id, resp)
        if resp and resp.get("ok"):
            self._get_device_capabilities(device_id, force=True)
            self._render_members_table()

    def action_device_disconnect(self) -> None:
        device_id = self._selected_device()
        if not device_id:
            return
        resp = self._rpc_call({"type": "device.disconnect", "device_id": device_id})
        self._notify_rpc_result("Device disconnect", device_id, resp)

    def action_driver_start(self) -> None:
        if self._action_target() == "process":
            process_id = self._selected_process()
            if not process_id:
                return
            proc = self._get_process_record(process_id)
            if self._status_process_started(proc):
                self.notify(f"Process already started: {process_id}")
                return
            resp = self._rpc_call({"type": "manager.processes.start", "process_id": process_id})
            self._notify_rpc_result("Process start", process_id, resp)
            return

        device_id = self._selected_device()
        if not device_id:
            return
        status = self._device_status.get(device_id)
        if self._status_driver_started(status):
            self.notify(f"Driver already started: {device_id}")
            return
        resp = self._rpc_call({"type": "device.driver.start", "device_id": device_id})
        self._notify_rpc_result("Driver start", device_id, resp)

    def action_drivers_start_all(self) -> None:
        if self._action_target() == "process":
            for proc in self._processes:
                process_id = str(proc.get("process_id", ""))
                if not process_id:
                    continue
                resp = self._rpc_call(
                    {"type": "manager.processes.start", "process_id": process_id}
                )
                self._notify_rpc_result("Process start", process_id, resp)
            return

        for device_id in list(self._device_status):
            resp = self._rpc_call(
                {"type": "device.driver.start", "device_id": device_id}
            )
            self._notify_rpc_result("Driver start", device_id, resp)

    def action_driver_stop(self) -> None:
        if self._action_target() == "process":
            process_id = self._selected_process()
            if not process_id:
                return
            proc = self._get_process_record(process_id)
            if self._status_process_stopped(proc):
                self.notify(f"Process already stopped: {process_id}")
                return

            def _on_dismiss(confirmed: bool | None) -> None:
                if not confirmed:
                    return
                resp = self._rpc_call(
                    {"type": "manager.processes.stop", "process_id": process_id}
                )
                self._notify_rpc_result("Process stop", process_id, resp)

            self.push_screen(
                ConfirmScreen(f"Stop process {process_id}?"), _on_dismiss
            )
            return

        device_id = self._selected_device()
        if not device_id:
            return
        status = self._device_status.get(device_id)
        if self._status_driver_stopped(status):
            self.notify(f"Driver already stopped: {device_id}")
            return

        def _on_dismiss(confirmed: bool | None) -> None:
            if not confirmed:
                return
            resp = self._rpc_call(
                {"type": "device.driver.stop", "device_id": device_id}
            )
            self._notify_rpc_result("Driver stop", device_id, resp)

        self.push_screen(ConfirmScreen(f"Stop driver for {device_id}?"), _on_dismiss)

    def action_driver_restart(self) -> None:
        if self._action_target() == "process":
            process_id = self._selected_process()
            if not process_id:
                return

            def _on_dismiss(confirmed: bool | None) -> None:
                if not confirmed:
                    return
                resp = self._rpc_call(
                    {"type": "manager.processes.restart", "process_id": process_id}
                )
                self._notify_rpc_result("Process restart", process_id, resp)

            self.push_screen(
                ConfirmScreen(f"Restart process {process_id}?"), _on_dismiss
            )
            return

        device_id = self._selected_device()
        if not device_id:
            return

        def _on_dismiss(confirmed: bool | None) -> None:
            if not confirmed:
                return
            resp = self._rpc_call(
                {"type": "device.driver.restart", "device_id": device_id}
            )
            self._notify_rpc_result("Driver restart", device_id, resp)

        self.push_screen(ConfirmScreen(f"Restart driver for {device_id}?"), _on_dismiss)

    async def on_key(self, event: events.Key) -> None:  # type: ignore[override]
        # Work around cases where focused widgets swallow app bindings.
        if isinstance(self.screen, ModalScreen):
            event.stop()
            return
        if isinstance(self.focused, Input):
            return
        if event.character and event.character.isupper():
            return
        key = event.key
        if key == "x":
            self.action_driver_stop()
            event.stop()
        elif key == "r":
            self.action_driver_restart()
            event.stop()
        elif key == "enter":
            self.action_member_primary()
            event.stop()

    async def action_device_recover(self) -> None:
        device_id = self._selected_device()
        if not device_id:
            return
        confirmed = await self.push_screen(
            ConfirmScreen(f"Recover device {device_id}?")
        )
        if not confirmed:
            return
        self._rpc_call({"type": "device.recover", "device_id": device_id})

    def action_drivers_stop_all(self) -> None:
        if self._action_target() == "process":
            def _on_dismiss(confirmed: bool | None) -> None:
                if not confirmed:
                    return
                ok_count = 0
                fail_count = 0
                for proc in self._processes:
                    process_id = str(proc.get("process_id", ""))
                    if not process_id:
                        continue
                    resp = self._rpc_call(
                        {"type": "manager.processes.stop", "process_id": process_id}
                    )
                    if resp and resp.get("ok"):
                        ok_count += 1
                    else:
                        fail_count += 1
                        err = resp.get("error", "timeout") if resp else "timeout"
                        err_text = json.dumps(err) if isinstance(err, dict) else str(err)
                        self._log_action_result(
                            f"PROC STOP {process_id} -> error {err_text}"
                        )
                self.notify(
                    f"Stop all processes: {ok_count} ok, {fail_count} failed"
                )

            self.push_screen(ConfirmScreen("Stop all processes?"), _on_dismiss)
            return

        def _on_dismiss(confirmed: bool | None) -> None:
            if not confirmed:
                return
            ok_count = 0
            fail_count = 0
            for device_id in list(self._device_status):
                resp = self._rpc_call(
                    {"type": "device.driver.stop", "device_id": device_id}
                )
                if resp and resp.get("ok"):
                    ok_count += 1
                else:
                    fail_count += 1
                    err = resp.get("error", "timeout") if resp else "timeout"
                    err_text = json.dumps(err) if isinstance(err, dict) else str(err)
                    self._log_action_result(
                        f"DRIVER STOP {device_id} -> error {err_text}"
                    )
            self.notify(f"Stop all drivers: {ok_count} ok, {fail_count} failed")

        self.push_screen(ConfirmScreen("Stop all drivers?"), _on_dismiss)


def main() -> None:
    app = ManagerTUI()
    app.run()


if __name__ == "__main__":
    main()

