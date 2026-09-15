from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Literal

import zmq
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.driver import Driver
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from .helpers import (
    normalize_log_severity_for_tui,
    normalize_topic_set,
    severity_rank_for_tui,
)
from .models import DeviceStatus, ResourceView
from .status_presentation import (
    format_age,
    render_health_state,
    render_link_state,
    render_run_state,
    state_name,
    tui_symbols,
)
from .screens import (
    ConfirmScreen,
    InvokeMemberScreen,
    ResultScreen,
    SetMemberScreen,
    TopicFilterScreen,
)
from .theme import (
    DIM_TEXT,
    FAULT_RED,
    HEALTHY_GREEN,
    INDUSTRIAL_THEME,
    MUTED_TEXT,
    SECONDARY_TEXT,
    WARNING_ORANGE,
)
from ..utils.zmq_helpers import json_dumps, safe_json_loads
from ..utils.config_redaction import redact_config

Json = dict[str, Any]
ResourceSortColumn = Literal[
    "kind", "resource", "connection", "health", "runtime", "age_s", "error"
]

_RESOURCE_COLUMNS: tuple[tuple[ResourceSortColumn, str], ...] = (
    ("kind", "KIND"),
    ("resource", "RESOURCE"),
    ("connection", "LINK"),
    ("health", "HEALTH"),
    ("runtime", "RUN"),
    ("age_s", "AGE"),
    ("error", "ERROR"),
)
_INSPECTOR_TAB_IDS = ("overview", "telemetry", "commands", "config")
_ACTIVITY_TAB_IDS = ("errors", "events")


def _add_uppercase_columns(table: DataTable, *keys: str) -> None:
    """Keep visible table labels industrial and keys stable for cell updates."""
    for key in keys:
        table.add_column(key.upper(), key=key)


class ManagerTUI(App):
    TITLE = "EXPERIMENT CONTROL"

    CSS = """
    Screen {background: $background; color: $foreground;}
    Header {background: $panel; color: $text-secondary;}
    Footer {background: $panel; color: $text-muted;}

    #health_summary {
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    #navigation_help {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $background;
    }
    #main {height: 1fr; layout: vertical;}
    #navigator {
        width: 1fr;
        height: auto;
        min-height: 7;
        max-height: 16;
        background: $background;
        border-bottom: solid $border-subtle;
    }
    #navigator:focus-within {border-bottom: heavy $accent;}
    #resource_filter {
        height: 3;
        padding: 0 1;
        color: $foreground;
        background: $surface;
        border: none;
        border-bottom: solid $border-subtle;
    }
    #resource_filter:focus {
        background: $surface;
        background-tint: transparent;
        border-bottom: heavy $accent;
    }
    Input > .input--placeholder {color: $text-disabled;}
    Input > .input--cursor {background: $accent; color: $background;}
    #resources_table {height: auto; min-height: 3; max-height: 13;}
    #devices_table, #processes_table, #processes_title {display: none;}
    #inspector {width: 1fr; height: 1fr; background: $background;}
    #inspector:focus-within {border-top: heavy $accent;}
    #selected_resource_title {
        height: 1;
        padding: 0 1;
        color: $text-secondary;
        background: $surface;
        text-style: bold;
    }
    #action_strip {
        height: 1;
        padding: 0 1;
        overflow-x: auto;
        background: $surface;
    }
    #action_strip Button {
        width: auto;
        min-width: 0;
        height: 1;
        padding: 0 1;
        margin-right: 0;
        border: none;
        background: transparent;
        color: $text-muted;
        text-style: none;
    }
    #action_strip Button:hover {background: $selection-background-blurred; color: $foreground;}
    #action_strip Button:focus {background: $selection-background; color: $accent; text-style: bold;}
    #action_strip Button:disabled {background: transparent; color: $text-disabled; text-opacity: 1;}
    #action_strip #action_start {color: $success;}
    #action_strip #action_stop {color: $error;}
    #action_strip #action_start:disabled, #action_strip #action_stop:disabled {color: $text-disabled;}
    #inspector_tabs {height: 1fr;}
    TabbedContent {background: $background;}
    Tabs {height: 2; background: $surface;}
    Tab {background: $surface; color: $text-muted; text-style: none;}
    Tab:hover {background: $surface; color: $text-secondary;}
    Tab.-active {background: $surface; color: $foreground; text-style: bold;}
    Tabs:focus Tab.-active {background: $surface; color: $accent; text-style: bold;}
    Underline > .underline--bar {color: $accent; background: $border-subtle;}
    #overview_tables {height: 1fr;}
    #heartbeat_title, #driver_title, #process_title {
        height: 1;
        padding: 0 1;
        color: $text-secondary;
        background: $surface;
        text-style: bold;
    }
    DataTable {background: $background; color: $foreground;}
    DataTable > .datatable--header {background: $panel; color: $text-secondary; text-style: bold;}
    DataTable > .datatable--even-row, DataTable > .datatable--odd-row {background: $background;}
    DataTable > .datatable--cursor {background: $selection-background-blurred; color: $text-secondary; text-style: none;}
    DataTable:focus {background: $background; background-tint: transparent;}
    DataTable:focus > .datatable--header {background: $panel; background-tint: transparent;}
    DataTable:focus > .datatable--cursor {background: $selection-background; color: $foreground; text-style: bold;}
    DataTable > .datatable--hover {background: $selection-background-blurred;}
    #status_row {
        height: 2;
        padding: 0 1;
        background: $surface;
        border-top: solid $border-subtle;
    }
    #status_row Static {width: 1fr;}
    #dropped_status {text-align: center;}
    #backend_status {text-align: right;}
    #errors_table {height: 1fr;}
    #members_table {height: 1fr;}
    #cap_help {height: auto; padding: 0 1; color: $text-muted;}
    #config_table {height: 1fr;}
    #telemetry_empty {height: 1; padding: 0 1; color: $text-muted; display: none;}
    #event_log {height: 1fr; background: $background; color: $text-secondary;}
    #activity_summary {
        height: 2;
        padding: 0 1;
        border-top: solid $border-subtle;
        background: $surface;
        color: $text-muted;
    }
    #activity_summary.has-warning {color: $warning;}
    #activity_summary.has-error {color: $error; text-style: bold;}
    #activity_drawer {
        height: 14;
        display: none;
        border-top: solid $border-visible;
        background: $background;
    }
    #activity_drawer.open {display: block;}
    #activity_drawer:focus-within {border-top: heavy $accent;}
    #activity_tabs {height: 1fr; background: $background;}
    #activity_tabs Tabs {background: $panel;}
    #activity_tabs Tab {background: $panel; color: $text-muted;}
    #activity_tabs Tab.-active {background: $panel; color: $foreground;}
    #activity_tabs Tabs:focus Tab.-active {background: $panel; color: $accent;}
    #activity_tabs Underline > .underline--bar {
        color: $accent;
        background: $border-subtle;
    }

    InvokeMemberScreen, SetMemberScreen, ResultScreen, ConfirmScreen {
        align: center middle;
        background: $background 88%;
    }

    #invoke_dialog, #set_dialog, #confirm_dialog {
        width: 80;
        height: auto;
        min-height: 0;
        max-height: 70%;
        padding: 1 2;
        border: solid $border-visible;
        border-top: heavy $accent;
        background: $panel;
    }

    #confirm_dialog {width: 60;}

    #result_dialog {
        width: 80;
        max-width: 90%;
        height: auto;
        min-height: 0;
        max-height: 80%;
        padding: 1 2;
        border: solid $border-visible;
        border-top: heavy $accent;
        background: $panel;
    }

    #invoke_title, #set_title, #result_title {
        height: 1;
        color: $accent;
        text-style: bold;
    }
    #result_body {
        height: auto;
        min-height: 3;
        max-height: 50vh;
        border: solid $border-subtle;
        background: $background;
        color: $foreground;
    }
    #result_close {height: 3; margin-top: 1;}

    #invoke_dialog > *, #set_dialog > * {
        height: auto;
    }

    #confirm_dialog > * {
        height: auto;
    }

    #invoke_dialog Input, #set_dialog Input {
        background: $background;
        color: $foreground;
        border: solid $border-subtle;
    }
    #invoke_dialog Input:focus, #set_dialog Input:focus {
        background: $background;
        background-tint: transparent;
        border: solid $accent;
    }

    #invoke_buttons Button, #set_buttons Button, #confirm_buttons Button, #result_close {
        border: none;
        background: $surface;
        color: $text-secondary;
    }
    #invoke_buttons Button:focus, #set_buttons Button:focus,
    #confirm_buttons Button:focus, #result_close:focus {
        background: $selection-background;
        color: $accent;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        Binding("s", "driver_start", "Start selected", show=False),
        Binding("x", "driver_stop", "Stop selected", show=False),
        Binding("r", "driver_restart", "Restart selected", show=False),
        Binding("v", "device_recover", "Recover device", show=False),
        Binding("c", "device_connect", "Connect", show=False),
        Binding("d", "device_disconnect", "Disconnect", show=False),
        ("S", "drivers_start_all", "Start all"),
        ("X", "drivers_stop_all", "Stop all"),
        ("C", "devices_connect_all", "Connect all"),
        Binding("t", "toggle_streaming", "Toggle streaming", show=False),
        Binding("enter", "member_primary", "Invoke/Get", show=False),
        Binding("e", "member_set", "Set value", show=False),
        Binding("R", "capabilities_refresh", "Refresh capabilities", show=False),
        ("f5", "reconnect_backend", "Reconnect"),
        ("p", "topics", "Topics"),
        ("l", "clear_log", "Clear log"),
        Binding("slash", "focus_search", "Search", show=False),
        Binding("u", "toggle_unhealthy", "Problems only", priority=True),
        ("o", "resource_sort_next", "Next sort"),
        ("O", "resource_sort_reverse", "Reverse sort"),
        Binding(
            "left_square_bracket",
            "inspector_previous",
            "Previous tab",
            show=False,
            priority=True,
        ),
        Binding(
            "right_square_bracket",
            "inspector_next",
            "Next tab",
            show=False,
            priority=True,
        ),
        Binding("a", "toggle_activity", "Activity", show=False),
        Binding("1", "inspector_overview", "Overview", show=False),
        Binding("2", "inspector_telemetry", "Telemetry", show=False),
        Binding("3", "inspector_commands", "Commands", show=False),
        Binding("4", "inspector_config", "Config", show=False),
    ]

    _DEFAULT_EVENT_LOG_HIDDEN_TOPICS = frozenset(
        {
            "manager.telemetry_update",
            "manager.heartbeat",
            "manager.chunk_ready",
            "manager.process_telemetry_update",
            "manager.process.heartbeat",
            "manager.device_config",
        }
    )
    _VALID_PUB_QUEUE_OVERFLOW_POLICIES = frozenset({"drop_newest", "drop_oldest"})

    streaming_enabled = reactive(True)

    _INPUT_BLOCKED_ACTIONS = frozenset(
        {"inspector_previous", "inspector_next", "toggle_unhealthy"}
    )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in self._INPUT_BLOCKED_ACTIONS and (
            isinstance(self.screen, ModalScreen) or isinstance(self.focused, Input)
        ):
            return False
        return super().check_action(action, parameters)

    def __init__(
        self,
        *,
        manager_rpc: str = "tcp://127.0.0.1:6000",
        manager_pub: str = "tcp://127.0.0.1:6001",
        rpc_timeout_ms: int = 1500,
        snapshot_period_s: float = 2.0,
        event_log_max_lines: int = 10_000,
        event_log_default_hidden_topics: list[str]
        | tuple[str, ...]
        | set[str]
        | None = None,
        event_log_manager_min_severity: str = "warning",
        pub_queue_maxsize: int = 10_000,
        pub_queue_overflow_policy: str = "drop_newest",
        ascii_only: bool = False,
        driver_class: type[Driver] | None = None,
    ) -> None:
        super().__init__(driver_class=driver_class)
        self.register_theme(INDUSTRIAL_THEME)
        self.theme = INDUSTRIAL_THEME.name
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
        overflow_policy = (
            str(pub_queue_overflow_policy or "drop_newest").strip().lower()
        )
        if overflow_policy not in self._VALID_PUB_QUEUE_OVERFLOW_POLICIES:
            overflow_policy = "drop_newest"
        self._pub_queue_overflow_policy = overflow_policy
        self._ascii_only = bool(ascii_only)

        self._ctx = zmq.Context.instance()
        self._rpc = self._new_rpc_socket()
        self._rpc_seq = 0
        self._bg_rpc = self._new_rpc_socket()
        self._bg_rpc_seq = 0

        # Single-owner RPC socket: `self._rpc` (a ZMQ DEALER, NOT thread-safe)
        # is touched by exactly ONE thread — the RPC worker started in
        # on_mount, which drains this queue of zero-arg callables. UI-thread
        # code and Textual @work threads never touch the socket directly;
        # they submit work here (fire-and-forget via _rpc_submit, or blocking
        # via _rpc_call which waits on a per-call result box). This removes the
        # cross-thread DEALER race and keeps the UI thread off the blocking
        # round-trip. Set to the worker's ident once it starts so re-entrant
        # calls from the worker itself run inline instead of deadlocking.
        self._rpc_req_q: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._rpc_worker_ident: int | None = None
        self._rpc_worker_handle: threading.Thread | None = None
        self._bg_rpc_req_q: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._bg_rpc_worker_ident: int | None = None
        self._bg_rpc_worker_handle: threading.Thread | None = None
        # Captured in on_mount; used to marshal UI updates that originate off
        # the UI thread (e.g. _set_backend_status from the RPC worker).
        self._ui_thread_id: int | None = None
        # De-dupe concurrent capability fetches per target so a busy render
        # loop can't enqueue a burst of identical probes.
        self._cap_fetch_inflight: set[str] = set()
        self._proc_cap_fetch_inflight: set[str] = set()
        # Guards against overlapping snapshot refreshes (the interval can fire
        # again while a slow refresh RPC is still in flight on the worker).
        self._snapshot_refresh_inflight = False

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
        self._process_telemetry_cache: dict[str, Json] = {}
        self._heartbeat_cache: dict[str, Json] = {}
        self._config_cache: dict[str, Json] = {}
        self._config_errors: dict[str, str] = {}
        self._config_fetch_inflight: set[str] = set()

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
        self._errors: deque[Json] = deque(maxlen=200)
        # Bumped whenever _errors changes; _render_errors_table skips a full
        # rebuild when the rendered revision already matches (the errors table
        # is a pure function of _errors, mutated only via _record_error).
        self._errors_rev = 0
        self._errors_rendered_rev = -1
        self._seen_error_fingerprints: set[str] = set()
        self._seen_error_fingerprint_order: deque[str] = deque(maxlen=2000)
        self._last_manager_log_t_mono: float | None = None
        self._log_tail_bootstrap_limit = 250
        self._last_toast_by_key: dict[str, tuple[str, float]] = {}
        self._toast_cooldown_s = 2.0
        self._toast_repeat_s = 30.0
        self._pub_drain_max = 500
        self._telemetry_columns_device = (
            "signal",
            "value",
            "units",
            "quality",
            "age",
        )
        self._telemetry_columns_process = self._telemetry_columns_device
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
            "hb_age",
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
        # Throttle for the render-path capability fetch of the *selected*
        # device/process. The render path uses a forced fetch (same as the
        # manual Refresh) so federated targets auto-populate instead of getting
        # stuck: for processes the non-force path is gated by the exponential
        # backoff above; for devices the non-force render gate treats a mirror
        # (no local driver pid) as "stopped" and skips the fetch. We cap the
        # attempt rate here so rapid ticks / cursor-scrolling can't hammer a
        # peer whose capabilities aren't available yet.
        self._proc_cap_render_attempt_mono: dict[str, float] = {}
        self._dev_cap_render_attempt_mono: dict[str, float] = {}
        self._cap_render_retry_s: float = 1.0
        self._members_last: dict[str, list[dict[str, Any]]] = {}
        self._proc_members_last: dict[str, list[dict[str, Any]]] = {}
        self._members_source: str = "device"
        self._inspector_mode: str = "device"
        self._members_context_key: str | None = None
        self._members_rendered_fingerprint: dict[str, int] = {}
        self._inspector_dirty = True
        self._last_inspector_render = 0.0
        self._inspector_min_period_s = 0.2
        self._error_counts: dict[str, int] = {}
        # _bump_error fires from the main thread today, but the SUB
        # poll worker (_pub_thread) calls it via several code paths
        # (pub.open / pub.reconnect / pub.recv / pub.decode). Guard the
        # increment so the planned TUI worker conversion (Group F.19,
        # deferred) doesn't introduce a cross-thread race.
        self._error_counts_lock = threading.Lock()
        self._backend_status_text = "Backend: connecting"
        self._resource_filter = ""
        self._unhealthy_only = False
        self._resource_views: dict[str, ResourceView] = {}
        self._resource_rows: dict[str, tuple[str, ...]] = {}
        self._resource_order: list[str] = []
        self._resource_column_keys: dict[ResourceSortColumn, Any] = {}
        self._resource_sort_column: ResourceSortColumn = "kind"
        self._resource_sort_reverse = False
        self._selected_resource_key: str | None = None
        self._pending_resource_actions: set[str] = set()
        self._activity_open = False
        self._activity_return_focus: Widget | None = None
        self._activity_unread_warning = 0
        self._activity_unread_error = 0
        self._event_lines: deque[str] = deque(maxlen=self._event_log_max_lines)
        self._activity_hydration_lines: list[str] = []
        self._activity_hydration_index = 0
        self._latest_state_messages: dict[tuple[str, str], tuple[str, Json]] = {}
        self._state_lock = threading.Lock()
        self._pub_drain_budget_s = 0.014
        self._max_drain_slice_s = 0.0
        self._coalesced_pub_messages = 0
        self._narrow_layout = False
        self._narrow_show_inspector = False

    def compose(self) -> ComposeResult:
        yield Header(icon="")
        yield Static(self._health_summary_text([]), id="health_summary")
        yield Static(
            Text(
                f"ENTER inspector {self._symbols.separator} ESC resources "
                f"{self._symbols.separator} [ / ] tabs"
            ),
            id="navigation_help",
        )
        with Vertical(id="main"):
            with Vertical(id="navigator"):
                yield Input(placeholder="RESOURCE SEARCH  [/]", id="resource_filter")
                yield DataTable(id="resources_table")
                # Kept mounted as compatibility targets for the existing
                # low-level renderer tests; the operator UI uses resources_table.
                yield DataTable(id="devices_table")
                yield Label("Processes", id="processes_title")
                yield DataTable(id="processes_table")
            with Vertical(id="inspector"):
                yield Label("NO RESOURCE SELECTED", id="selected_resource_title")
                with Horizontal(id="action_strip"):
                    yield Button("START (s)", id="action_start", variant="success")
                    yield Button("STOP (x)", id="action_stop", variant="error")
                    yield Button("RESTART (r)", id="action_restart")
                    yield Button("CONNECT (c)", id="action_connect")
                    yield Button("DISCONNECT (d)", id="action_disconnect")
                    yield Button("RECOVER (v)", id="action_recover")
                with TabbedContent(initial="overview", id="inspector_tabs"):
                    with TabPane("OVERVIEW (1)", id="overview"):
                        with Vertical(id="overview_tables"):
                            yield Label("HEARTBEAT", id="heartbeat_title")
                            yield DataTable(id="heartbeat_table")
                            yield Label("RUNTIME", id="driver_title")
                            yield DataTable(id="driver_table")
                            yield Label("PROCESS", id="process_title")
                            yield DataTable(id="process_table")
                    with TabPane("TELEMETRY (2)", id="telemetry"):
                        yield Static("", id="telemetry_empty")
                        yield DataTable(id="telemetry_table")
                    with TabPane("COMMANDS (3)", id="commands"):
                        yield DataTable(id="members_table")
                        yield Static(
                            "Enter: invoke/get | e: set | R: refresh", id="cap_help"
                        )
                    with TabPane("CONFIG (4)", id="config"):
                        yield DataTable(id="config_table")
        yield Static(
            f"{self._symbols.activity_closed} ACTIVITY // A OPEN  |  NO UNREAD ALERTS",
            id="activity_summary",
        )
        with Vertical(id="activity_drawer"):
            with TabbedContent(initial="errors", id="activity_tabs"):
                with TabPane("ERRORS", id="errors"):
                    yield DataTable(id="errors_table")
                with TabPane("EVENT LOG", id="events"):
                    yield RichLog(id="event_log", max_lines=self._event_log_max_lines)
        with Horizontal(id="status_row"):
            yield Static(self._streaming_status_text(), id="streaming_status")
            yield Static(self._dropped_status_text(), id="dropped_status")
            yield Static(self._backend_status_renderable(), id="backend_status")
        yield Footer()

    def on_mount(self) -> None:
        self._ui_thread_id = threading.get_ident()
        # Start the single-owner RPC worker before anything issues an RPC.
        self._rpc_worker_handle = threading.Thread(
            target=self._rpc_worker_loop,
            name="tui-rpc-worker",
            daemon=True,
        )
        self._rpc_worker_handle.start()
        self._bg_rpc_worker_handle = threading.Thread(
            target=self._bg_rpc_worker_loop,
            name="tui-background-rpc-worker",
            daemon=True,
        )
        self._bg_rpc_worker_handle.start()
        self._setup_tables()
        # Bootstrap the log tail off the UI thread (blocking RPC).
        self._background_rpc_submit(
            {
                "type": "manager.logs.tail",
                "params": {"limit": self._log_tail_bootstrap_limit},
            },
            self._apply_manager_log_tail_bootstrap,
        )
        self.set_interval(self._snapshot_period_s, self._refresh_snapshot)
        self.set_interval(0.2, self._drain_pub_queue)
        self._pub_thread_handle = threading.Thread(target=self._pub_thread, daemon=True)
        self._pub_thread_handle.start()
        self._apply_responsive_layout(self.size.width)
        # The resource list is the operator's starting point; search is entered
        # deliberately with `/` rather than taking the initial text focus.
        self.call_later(self.action_focus_navigator)

    def on_unmount(self) -> None:
        self._stop_event.set()
        self._sub_reconnect_event.set()
        # Stop the RPC worker (it owns and closes the socket).
        self._rpc_req_q.put(None)
        self._bg_rpc_req_q.put(None)
        rpc_thread = self._rpc_worker_handle
        if rpc_thread is not None:
            rpc_thread.join(timeout=1.5)
        bg_rpc_thread = self._bg_rpc_worker_handle
        if bg_rpc_thread is not None:
            bg_rpc_thread.join(timeout=1.5)
        thread = self._pub_thread_handle
        if thread is not None:
            thread.join(timeout=1.5)
        try:
            self._rpc.close(0)
        except Exception:
            pass
        try:
            self._bg_rpc.close(0)
        except Exception:
            pass

    def _setup_tables(self) -> None:
        resources = self.query_one("#resources_table", DataTable)
        for key, label in _RESOURCE_COLUMNS:
            self._resource_column_keys[key] = resources.add_column(label, key=key)
        resources.cursor_type = "row"
        self._update_resource_sort_headers()

        devices = self.query_one("#devices_table", DataTable)
        _add_uppercase_columns(
            devices,
            "device_id",
            "liveness",
            "driver_proc",
            "pid",
            "hb_age",
            "telemetry_age",
            "driver_state",
            "device_state",
            "last_error",
        )
        devices.cursor_type = "row"

        processes = self.query_one("#processes_table", DataTable)
        _add_uppercase_columns(
            processes,
            "process_id",
            "state",
            "pid",
            "hb_age",
            "restart_count",
            "last_exit_code",
            "last_error",
        )
        processes.cursor_type = "row"

        telemetry = self.query_one("#telemetry_table", DataTable)
        _add_uppercase_columns(telemetry, *self._telemetry_columns_device)

        heartbeat = self.query_one("#heartbeat_table", DataTable)
        _add_uppercase_columns(heartbeat, *self._heartbeat_columns_device)

        driver = self.query_one("#driver_table", DataTable)
        _add_uppercase_columns(driver, *self._driver_columns)

        process = self.query_one("#process_table", DataTable)
        _add_uppercase_columns(process, "field", "value")

        config = self.query_one("#config_table", DataTable)
        _add_uppercase_columns(config, "setting", "value")

        errors = self.query_one("#errors_table", DataTable)
        _add_uppercase_columns(errors, "time", "sev", "source", "id", "message")
        errors.cursor_type = "row"

        members = self.query_one("#members_table", DataTable)
        _add_uppercase_columns(members, "name", "kind", "rw", "type", "source", "doc")
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
            _add_uppercase_columns(telemetry, *self._telemetry_columns_process)
            heartbeat.clear(columns=True)
            _add_uppercase_columns(heartbeat, *self._heartbeat_columns_process)
        else:
            telemetry.clear(columns=True)
            _add_uppercase_columns(telemetry, *self._telemetry_columns_device)
            heartbeat.clear(columns=True)
            _add_uppercase_columns(heartbeat, *self._heartbeat_columns_device)
        driver.clear(columns=True)
        _add_uppercase_columns(driver, *self._driver_columns)

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

    def _on_rpc_worker(self) -> bool:
        return (
            self._rpc_worker_ident is not None
            and threading.get_ident() == self._rpc_worker_ident
        )

    def _do_reset_rpc_socket(self) -> None:
        """Close and recreate the DEALER socket. MUST run on the RPC worker."""
        try:
            self._rpc.close(0)
        except Exception:
            pass
        self._rpc = self._new_rpc_socket()

    def _reset_rpc_socket(self) -> None:
        # The socket is owned by the RPC worker; only that thread may touch it.
        # If we're already on the worker (error path inside _do_rpc), reset
        # inline; otherwise dispatch the reset onto the worker queue.
        if self._on_rpc_worker():
            self._do_reset_rpc_socket()
        else:
            self._rpc_req_q.put(self._do_reset_rpc_socket)

    def _do_reset_bg_rpc_socket(self) -> None:
        try:
            self._bg_rpc.close(0)
        except Exception:
            pass
        self._bg_rpc = self._new_rpc_socket()

    def _reset_bg_rpc_socket(self) -> None:
        if (
            self._bg_rpc_worker_ident is not None
            and threading.get_ident() == self._bg_rpc_worker_ident
        ):
            self._do_reset_bg_rpc_socket()
        else:
            self._bg_rpc_req_q.put(self._do_reset_bg_rpc_socket)

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
        # Updates a widget, so must run on the UI thread. The RPC worker calls
        # this during round-trips — marshal back to the UI thread when off it.
        if (
            self._ui_thread_id is not None
            and threading.get_ident() != self._ui_thread_id
        ):
            try:
                self.call_from_thread(self._set_backend_status, text)
            except Exception:
                pass
            return
        self._backend_status_text = text
        try:
            self.query_one("#backend_status", Static).update(
                self._backend_status_renderable()
            )
        except Exception:
            pass
        if hasattr(self, "_resource_views"):
            self._update_health_summary(list(self._resource_views.values()))

    def _backend_status_renderable(self) -> Text:
        state = self._backend_status_text.partition(":")[2].strip() or "unknown"
        if state == "connected":
            style = HEALTHY_GREEN
        elif state == "unavailable":
            style = f"bold {FAULT_RED}"
        elif state in {"connecting", "reconnecting"}:
            style = WARNING_ORANGE
        else:
            style = MUTED_TEXT
        return Text.assemble(
            ("BACKEND // ", MUTED_TEXT),
            (state.upper(), style),
        )

    def _streaming_status_text(self) -> Text:
        state = "ACTIVE" if self.streaming_enabled else "INACTIVE"
        style = HEALTHY_GREEN if self.streaming_enabled else MUTED_TEXT
        return Text.assemble(
            ("STREAM // ", MUTED_TEXT), (state, style), (" (t)", DIM_TEXT)
        )

    def _dropped_status_text(self) -> Text:
        count = self._dropped_pub_messages
        style = WARNING_ORANGE if count else SECONDARY_TEXT
        return Text.assemble(("DROP // ", MUTED_TEXT), (str(count), style))

    def _health_summary_text(self, all_views: list[ResourceView]) -> Text:
        issue_views = [view for view in all_views if self._resource_has_issue(view)]
        issues = len(issue_views)
        healthy = sum(v.health == "healthy" for v in all_views)
        remote = sum(v.is_remote for v in all_views)
        text = self._backend_status_renderable()
        text.append("  |  ", style=DIM_TEXT)
        text.append("RESOURCES // ", style=MUTED_TEXT)
        text.append(str(len(all_views)), style=SECONDARY_TEXT)
        text.append("  |  ", style=DIM_TEXT)
        text.append("HEALTHY // ", style=MUTED_TEXT)
        text.append(str(healthy), style=HEALTHY_GREEN if healthy else SECONDARY_TEXT)
        text.append("  |  ", style=DIM_TEXT)
        text.append("ISSUES // ", style=MUTED_TEXT)
        issue_style = (
            FAULT_RED
            if any(
                view.health == "failed"
                or (
                    view.is_remote and state_name(view.connection) == "OFFLINE"
                )
                for view in issue_views
            )
            else WARNING_ORANGE
            if issue_views
            else SECONDARY_TEXT
        )
        text.append(str(issues), style=issue_style)
        text.append("  |  ", style=DIM_TEXT)
        text.append("REMOTE // ", style=MUTED_TEXT)
        text.append(str(remote), style=SECONDARY_TEXT)
        if self._unhealthy_only:
            text.append("  |  ISSUES ONLY", style=WARNING_ORANGE)
        return text

    def _next_request_id(self) -> str:
        self._rpc_seq += 1
        return f"tui-{self._rpc_seq}"

    def _rpc_worker_loop(self) -> None:
        """Single owner of the DEALER socket. Runs zero-arg jobs off the queue
        until a None sentinel is received; each job either does a round-trip
        (delivering results via a box and/or a UI-thread callback) or resets
        the socket. Because this is the only thread that touches `self._rpc`,
        there is no cross-thread socket race."""
        self._rpc_worker_ident = threading.get_ident()
        while True:
            job = self._rpc_req_q.get()
            if job is None:
                break
            try:
                job()
            except Exception:
                pass

    def _worker_do_rpc(
        self,
        payload: Json,
        on_result: Callable[[Json | None], None] | None,
        box: queue.Queue[Json | None] | None,
    ) -> None:
        """Job body executed on the RPC worker: do the round-trip, then hand
        the result to a blocking caller (box) and/or a UI-thread callback."""
        resp = self._do_rpc(payload)
        if box is not None:
            box.put(resp)
        if on_result is not None:
            try:
                self.call_from_thread(on_result, resp)
            except Exception:
                pass

    def _rpc_submit(
        self,
        payload: Json,
        on_result: Callable[[Json | None], None] | None = None,
    ) -> None:
        """Fire-and-forget RPC for the UI thread: enqueue the round-trip on the
        worker and (optionally) deliver the result to `on_result`, which runs
        on the UI thread via call_from_thread. Never blocks the caller."""
        self._rpc_req_q.put(lambda: self._worker_do_rpc(payload, on_result, None))

    def _rpc_call(self, payload: Json) -> Json | None:
        """Blocking RPC for NON-UI callers (Textual @work threads such as the
        bulk-action worker). Routes the round-trip through the single-owner
        worker and waits for the result, so the socket stays single-owned.

        MUST NOT be called on the UI thread — it would block the event loop.
        UI-thread code uses _rpc_submit instead. If invoked from the worker
        itself (re-entrancy), run inline to avoid a self-deadlock."""
        if self._on_rpc_worker():
            return self._do_rpc(payload)
        box: queue.Queue[Json | None] = queue.Queue(maxsize=1)
        self._rpc_req_q.put(lambda: self._worker_do_rpc(payload, None, box))
        timeout_s = (self._rpc_timeout_ms / 1000.0) + 5.0
        try:
            return box.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def _do_rpc(self, payload: Json) -> Json | None:
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
            # We're on the RPC worker (the only caller of _do_rpc), so reset the
            # socket inline rather than dispatching back onto our own queue.
            self._do_reset_rpc_socket()
            return None

    _normalize_log_severity = staticmethod(normalize_log_severity_for_tui)
    _severity_rank = staticmethod(severity_rank_for_tui)
    _normalize_topic_set = staticmethod(normalize_topic_set)

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

    def _apply_manager_log_tail_bootstrap(self, resp: Json | None) -> None:
        """Apply the bootstrap log-tail response (runs on the UI thread via the
        RPC worker callback)."""
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

    def _ingest_manager_log_entry(
        self, entry: Json, *, from_tail: bool = False
    ) -> bool:
        """Record a manager.log entry as an error if it clears the severity
        threshold and is not a duplicate. Returns True iff a new error was
        appended (so callers can avoid a redundant errors-table render)."""
        severity = self._normalize_log_severity(entry.get("severity"))
        if self._severity_rank(severity) < self._severity_rank("warning"):
            ts = entry.get("ts")
            if isinstance(ts, dict):
                log_t_mono = ts.get("t_mono")
                if isinstance(log_t_mono, (int, float)):
                    t_mono_f = float(log_t_mono)
                    if (
                        self._last_manager_log_t_mono is None
                        or t_mono_f > self._last_manager_log_t_mono
                    ):
                        self._last_manager_log_t_mono = t_mono_f
            return False

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
            self._last_manager_log_t_mono is None
            or t_mono > self._last_manager_log_t_mono
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
            return False

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

        if not from_tail and self._severity_rank(severity) >= self._severity_rank(
            "error"
        ):
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
        return True

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

    @staticmethod
    def _command_payload(device_id: str, action: str, params: Json | None) -> Json:
        return {
            "type": "command",
            "device_id": device_id,
            "action": action,
            "params": params or {},
            "source_kind": "tui",
            "source_id": "manager_tui",
        }

    @staticmethod
    def _normalize_command_resp(resp: Json | None) -> Json | None:
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

    def _device_command(
        self, device_id: str, action: str, params: Json | None = None
    ) -> Json | None:
        return self._normalize_command_resp(
            self._rpc_call(self._command_payload(device_id, action, params))
        )

    def _device_command_submit(
        self,
        device_id: str,
        action: str,
        params: Json | None,
        on_result: Callable[[Json | None], None],
    ) -> None:
        """Non-blocking device command for UI-thread callers. `on_result` runs
        on the UI thread with the normalized response."""
        self._rpc_submit(
            self._command_payload(device_id, action, params),
            lambda resp: on_result(self._normalize_command_resp(resp)),
        )

    @staticmethod
    def _process_rpc_payload(process_id: str, request: Json) -> Json:
        return {
            "type": "manager.processes.rpc",
            "process_id": process_id,
            "request": request,
            "source_kind": "tui",
            "source_id": "manager_tui",
        }

    def _process_rpc(self, process_id: str, request: Json) -> Json | None:
        return self._rpc_call(self._process_rpc_payload(process_id, request))

    def _process_rpc_submit(
        self,
        process_id: str,
        request: Json,
        on_result: Callable[[Json | None], None],
    ) -> None:
        """Non-blocking process RPC for UI-thread callers. `on_result` runs on
        the UI thread with the raw response."""
        self._rpc_submit(self._process_rpc_payload(process_id, request), on_result)

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
        if not self._process_is_registered(proc):
            return False
        # Federated (mirrored) processes carry a synthetic state ("FEDERATED"),
        # not the local "RUNNING", so gate them on the peer link being live
        # rather than the local state — otherwise their capabilities are never
        # probed and the detail panel stays empty.
        is_remote = bool(proc.get("is_remote")) or (
            str(proc.get("source_kind", "") or "").strip().lower() == "federated"
        )
        if is_remote:
            liveness = str(proc.get("liveness", "") or "").strip().upper()
            return liveness in ("", "ONLINE")
        state = str(proc.get("state", "") or "").strip().upper()
        return state == "RUNNING"

    def _process_capabilities_retry_allowed(self, process_id: str) -> bool:
        next_retry = self._proc_cap_retry_next_mono.get(process_id)
        if next_retry is None:
            return True
        return time.monotonic() >= float(next_retry)

    def _schedule_process_capabilities_retry(self, process_id: str) -> None:
        current = float(
            self._proc_cap_retry_delay_s.get(process_id, self._proc_cap_retry_initial_s)
        )
        delay = max(self._proc_cap_retry_initial_s, current)
        self._proc_cap_retry_next_mono[process_id] = time.monotonic() + delay
        self._proc_cap_retry_delay_s[process_id] = min(
            self._proc_cap_retry_max_s, delay * 2.0
        )

    def _reset_process_capabilities_retry(self, process_id: str) -> None:
        self._proc_cap_retry_next_mono.pop(process_id, None)
        self._proc_cap_retry_delay_s.pop(process_id, None)

    def _apply_device_capabilities(self, device_id: str, resp: Json | None) -> bool:
        """Fold a device `capabilities` response into the caches. Runs on the
        UI thread (from the RPC-worker callback). Returns True on success."""
        if not resp or not resp.get("ok"):
            return False
        result = resp.get("result")
        if not isinstance(result, dict):
            return False
        self._cap_cache[device_id] = result
        self._cap_cache_mono[device_id] = time.monotonic()
        members = result.get("members", [])
        if isinstance(members, list):
            self._members_last[device_id] = [m for m in members if isinstance(m, dict)]
        return True

    def _request_device_capabilities(
        self,
        device_id: str,
        *,
        force: bool = False,
        on_done: Callable[[bool], None] | None = None,
    ) -> None:
        """Non-blocking device-capability fetch. The round-trip runs on the RPC
        worker; the cache update, re-render, and `on_done(ok)` run on the UI
        thread. De-duped per device so a busy render loop can't pile up probes."""
        if device_id in self._cap_fetch_inflight:
            return
        if not force:
            status = self._device_status.get(device_id)
            if status is not None and str(status.liveness or "").upper() in {
                "STALE",
                "OFFLINE",
                "DISCONNECTED",
            }:
                if on_done is not None:
                    on_done(False)
                return
            t0 = self._cap_cache_mono.get(device_id)
            if t0 is not None and (time.monotonic() - t0) < self._cap_ttl_s:
                if on_done is not None:
                    on_done(True)
                return

        self._cap_fetch_inflight.add(device_id)

        def _after(resp: Json | None) -> None:
            self._cap_fetch_inflight.discard(device_id)
            ok = self._apply_device_capabilities(
                device_id, self._normalize_command_resp(resp)
            )
            if ok:
                self._render_members_table()
            if on_done is not None:
                on_done(ok)

        self._background_rpc_submit(
            self._command_payload(device_id, "capabilities", {}), _after
        )

    def _get_member_spec(self, device_id: str, name: str) -> dict[str, Any] | None:
        members = self._members_last.get(device_id, [])
        for member in members:
            if str(member.get("name", "")) == name:
                return member
        return None

    def _apply_process_capabilities(self, process_id: str, resp: Json | None) -> bool:
        """Fold a process `capabilities` response into the caches. Runs on the
        UI thread (from the RPC-worker callback). Returns True on success."""
        if not resp or not resp.get("ok"):
            self._schedule_process_capabilities_retry(process_id)
            return False
        result = resp.get("result")
        if not isinstance(result, dict):
            self._schedule_process_capabilities_retry(process_id)
            return False
        self._proc_cap_cache[process_id] = result
        self._reset_process_capabilities_retry(process_id)
        members = result.get("members", [])
        if isinstance(members, list):
            self._proc_members_last[process_id] = [
                m for m in members if isinstance(m, dict)
            ]
        return True

    def _request_process_capabilities(
        self,
        process_id: str,
        *,
        force: bool = False,
        on_done: Callable[[bool], None] | None = None,
    ) -> None:
        """Non-blocking process-capability fetch, mirroring
        _request_device_capabilities. Honors the probe-ready gate and the
        backoff/retry schedule so an unavailable peer isn't hammered."""
        if process_id in self._proc_cap_fetch_inflight:
            return
        if not self._process_capabilities_probe_ready(process_id):
            if on_done is not None:
                on_done(False)
            return
        if not force and not self._process_capabilities_retry_allowed(process_id):
            return
        if not force and process_id in self._proc_cap_cache:
            if on_done is not None:
                on_done(True)
            return

        self._proc_cap_fetch_inflight.add(process_id)

        def _after(resp: Json | None) -> None:
            self._proc_cap_fetch_inflight.discard(process_id)
            ok = self._apply_process_capabilities(process_id, resp)
            if ok:
                self._render_members_table()
            if on_done is not None:
                on_done(ok)

        self._background_rpc_submit(
            self._process_rpc_payload(
                process_id, {"type": "process.capabilities", "params": {}}
            ),
            _after,
        )

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

    def _device_label(self, status: DeviceStatus) -> str:
        if status.is_remote:
            return f"{self._symbols.remote_prefix} {status.device_id}"
        return status.device_id

    @property
    def _symbols(self):
        return tui_symbols(ascii_only=getattr(self, "_ascii_only", False))

    def _refresh_snapshot(self) -> None:
        # Interval callback on the UI thread. The two status RPCs are blocking,
        # so run them on the RPC worker and apply the results back on the UI
        # thread. Skip if a prior refresh is still in flight (slow backend).
        if self._snapshot_refresh_inflight:
            return
        self._snapshot_refresh_inflight = True
        pending: dict[str, Json | None] = {}

        def _after_devices(dev_resp: Json | None) -> None:
            pending["devices"] = dev_resp
            self._background_rpc_submit(
                {"type": "manager.processes.list"}, _after_procs
            )

        def _after_procs(proc_resp: Json | None) -> None:
            try:
                self._apply_snapshot(pending.get("devices"), proc_resp)
            finally:
                self._snapshot_refresh_inflight = False

        self._background_rpc_submit({"type": "device.list_status"}, _after_devices)

    def _apply_snapshot(self, resp: Json | None, proc_resp: Json | None) -> None:
        # Runs on the UI thread (RPC-worker callback). Mutates snapshot state
        # and renders — all UI-thread work, serialized with render/actions.
        snapshot_changed = False

        old_status = self._device_status
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
                        liveness=str(item.get("liveness"))
                        if item.get("liveness")
                        else None,
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
                        driver_proc_state=str(proc.get("state"))
                        if proc.get("state")
                        else None,
                        driver_pid=proc.get("pid"),
                        driver_restart_count=int(proc.get("restart_count") or 0),
                        driver_last_exit_code=proc.get("last_exit_code"),
                        driver_last_error=proc.get("last_error"),
                        is_remote=bool(item.get("is_remote"))
                        or str(item.get("source_kind", "")).strip().lower()
                        == "federated",
                        owner_peer_id=str(item.get("owner_peer_id"))
                        if item.get("owner_peer_id")
                        else None,
                    )
                    next_status[status.device_id] = status
                    prev = old_status.get(status.device_id)
                    if prev and prev.driver_pid != status.driver_pid:
                        self._heartbeat_cache.pop(status.device_id, None)
                        self._telemetry_cache.pop(status.device_id, None)
                self._device_status = next_status
                self._prune_device_caches(active_device_ids=set(next_status.keys()))
                snapshot_changed = True

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
                    prev_proc = old_proc_map.get(pid)
                    if isinstance(prev_proc, dict):
                        prev_pid = prev_proc.get("pid")
                        next_pid = proc.get("pid")
                        if prev_pid != next_pid:
                            self._process_telemetry_cache.pop(pid, None)
                            self._proc_cap_cache.pop(pid, None)
                            self._proc_members_last.pop(pid, None)
                            self._proc_cap_render_attempt_mono.pop(pid, None)
                            self._reset_process_capabilities_retry(pid)
                    if not self._process_is_registered(proc):
                        self._proc_cap_cache.pop(pid, None)
                        self._proc_members_last.pop(pid, None)
                        self._proc_cap_render_attempt_mono.pop(pid, None)
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
            if hasattr(self, "_resource_views"):
                self._render_resources_table()
            else:
                # Compatibility for lightweight unit-test harnesses which
                # construct the app without running its initializer.
                self._render_devices_table()
                self._render_processes_table()
            self._mark_inspector_dirty()
            self._render_inspector_if_needed(force=True)

    @staticmethod
    def _device_health(status: DeviceStatus) -> str:
        process_state = state_name(status.driver_proc_state)
        driver_state = state_name(status.driver_state)
        device_state = state_name(status.device_state)
        liveness = state_name(status.liveness)
        if process_state in {"FAILED", "CRASHLOOP"}:
            return "failed"
        if device_state == "FAULT" or driver_state == "FAULT":
            return "failed"
        if liveness == "STALE":
            return "stale"
        if device_state == "DEGRADED" or driver_state == "DEGRADED":
            return "degraded"
        if process_state in {"STARTING", "STOPPING"}:
            return "transition"
        if liveness == "ONLINE" and device_state == "OK":
            return "healthy"
        if device_state == "DISCONNECTED" and liveness == "DISCONNECTED":
            return "neutral"
        if process_state in {"STOPPED", "EXITED"} or liveness == "OFFLINE":
            return "neutral"
        return "unknown"

    @staticmethod
    def _process_health(proc: Json) -> str:
        state = state_name(proc.get("state"))
        if state in {"FAILED", "CRASHLOOP"}:
            return "failed"
        if state in {"STARTING", "STOPPING"}:
            return "transition"
        if state == "RUNNING":
            return "healthy"
        return "neutral"

    @staticmethod
    def _device_connection(status: DeviceStatus) -> str:
        liveness = state_name(status.liveness)
        if liveness == "ONLINE":
            return "connected"
        if liveness == "DISCONNECTED":
            return "disconnected"
        if liveness == "STALE":
            return "stale"
        if liveness == "OFFLINE":
            return "offline"
        device_state = state_name(status.device_state)
        if device_state == "DISCONNECTED":
            return "disconnected"
        return "unknown"

    @staticmethod
    def _device_link(status: DeviceStatus) -> str:
        """Return the liveness-derived link value used by the navigator."""
        liveness = state_name(status.liveness)
        return liveness or "UNKNOWN"

    def _connection_cell(self, connection: str | None, *, health: str = "neutral") -> Text:
        return render_link_state(
            connection,
            failure_related=(connection or "").casefold() == "offline"
            and health == "failed",
            ascii_only=getattr(self, "_ascii_only", False),
        )

    def _health_cell(self, health: str) -> Text:
        return render_health_state(
            health, ascii_only=getattr(self, "_ascii_only", False)
        )

    def _runtime_cell(self, state: str) -> Text:
        return render_run_state(
            state, ascii_only=getattr(self, "_ascii_only", False)
        )

    @staticmethod
    def _error_cell(message: str) -> Text | str:
        return Text(message, style=FAULT_RED) if message else ""

    def _collect_resource_views(self) -> list[ResourceView]:
        views: list[ResourceView] = []
        for device_id in sorted(self._device_status):
            status = self._device_status[device_id]
            views.append(
                ResourceView(
                    kind="device",
                    resource_id=device_id,
                    health=self._device_health(status),
                    state=state_name(status.driver_proc_state),
                    age_s=status.hb_age_s,
                    error=status.last_error or status.driver_last_error,
                    connection=self._device_link(status),
                    search_terms=(
                        state_name(status.liveness),
                        state_name(status.device_state),
                        state_name(status.driver_state),
                    ),
                    is_remote=status.is_remote,
                    owner_peer_id=status.owner_peer_id,
                )
            )
        for proc in self._processes:
            process_id = str(proc.get("process_id", ""))
            if not process_id:
                continue
            is_remote = bool(proc.get("is_remote")) or (
                str(proc.get("source_kind", "")).strip().lower() == "federated"
            )
            age = proc.get("hb_age_s")
            views.append(
                ResourceView(
                    kind="process",
                    resource_id=process_id,
                    health=self._process_health(proc),
                    state=state_name(proc.get("state")),
                    age_s=float(age) if isinstance(age, (int, float)) else None,
                    error=str(proc.get("last_error"))
                    if proc.get("last_error")
                    else None,
                    connection=(
                        state_name(proc.get("liveness"))
                        if is_remote and proc.get("liveness")
                        else None
                    ),
                    search_terms=(state_name(proc.get("liveness")),),
                    is_remote=is_remote,
                    owner_peer_id=str(proc.get("owner_peer_id"))
                    if proc.get("owner_peer_id")
                    else None,
                )
            )
        return views

    @staticmethod
    def _resource_sort_value(
        view: ResourceView, column: ResourceSortColumn
    ) -> str | float | None:
        if column == "kind":
            return view.kind
        if column == "resource":
            return view.resource_id.casefold()
        if column == "connection":
            return view.connection.casefold() if view.connection else None
        if column == "health":
            return view.health.casefold()
        if column == "runtime":
            return view.state.casefold() if view.state else None
        if column == "age_s":
            return view.age_s
        return view.error.casefold() if view.error else None

    def _sort_resource_views(self, views: list[ResourceView]) -> list[ResourceView]:
        """Sort mixed resources while keeping empty cells at the end."""
        column = self._resource_sort_column
        populated: list[tuple[ResourceView, str | float]] = []
        empty: list[ResourceView] = []
        for view in views:
            value = self._resource_sort_value(view, column)
            if value is None or value == "":
                empty.append(view)
            else:
                populated.append((view, value))

        # Start from a deterministic resource-name order so equal primary values
        # do not jump around as live status updates arrive.
        populated.sort(key=lambda item: (item[0].resource_id.casefold(), item[0].kind))
        populated.sort(key=lambda item: item[1], reverse=self._resource_sort_reverse)
        empty.sort(key=lambda view: (view.resource_id.casefold(), view.kind))
        return [view for view, _ in populated] + empty

    def _update_resource_sort_headers(self) -> None:
        try:
            table = self.query_one("#resources_table", DataTable)
            for key, label in _RESOURCE_COLUMNS:
                marker = (
                    f" {self._symbols.sort_descending}"
                    if self._resource_sort_reverse
                    else f" {self._symbols.sort_ascending}"
                )
                column = table.columns[self._resource_column_keys[key]]
                column.label = Text(
                    f"{label}{marker if key == self._resource_sort_column else ''}"
                )
                # Textual only measures auto-width columns when cells change.
                # Reserve enough header space here, otherwise an indicator wider
                # than every cell is clipped (for example ``kind ▲``).
                column.content_width = max(column.content_width, column.label.cell_len)
            table.refresh(layout=True)
        except Exception:
            pass

    def _set_resource_sort(self, column: ResourceSortColumn) -> None:
        if column == self._resource_sort_column:
            self._resource_sort_reverse = not self._resource_sort_reverse
        else:
            self._resource_sort_column = column
            self._resource_sort_reverse = False
        self._resource_order = []
        self._update_resource_sort_headers()
        self._render_resources_table(preserve_scroll=False)

    def _visible_resource_views(self) -> list[ResourceView]:
        query = self._resource_filter.strip().lower()
        return self._sort_resource_views(
            [
                view
                for view in self._collect_resource_views()
                if (
                    not self._unhealthy_only or self._resource_has_issue(view)
                )
                and (not query or query in view.searchable_text)
            ]
        )

    @staticmethod
    def _resource_has_issue(view: ResourceView) -> bool:
        """Return whether a resource needs operator attention."""
        if view.health in {"failed", "stale", "degraded"}:
            return True
        return view.is_remote and state_name(view.connection) in {"OFFLINE", "STALE"}

    def _update_health_summary(self, all_views: list[ResourceView]) -> None:
        try:
            self.query_one("#health_summary", Static).update(
                self._health_summary_text(all_views)
            )
        except Exception:
            pass

    def _restore_resource_scroll(self, scroll_x: float, scroll_y: float) -> None:
        table = self.query_one("#resources_table", DataTable)
        table.scroll_x = min(scroll_x, table.max_scroll_x)
        table.scroll_y = min(scroll_y, table.max_scroll_y)
        table.scroll_target_x = table.scroll_x
        table.scroll_target_y = table.scroll_y

    def _render_resources_table(self, *, preserve_scroll: bool = True) -> None:
        table = self.query_one("#resources_table", DataTable)
        scroll_x = table.scroll_x
        scroll_y = table.scroll_y
        all_views = self._collect_resource_views()
        self._resource_views = {view.key: view for view in all_views}
        views = self._sort_resource_views(
            [
                view
                for view in all_views
                if (
                    not self._unhealthy_only or self._resource_has_issue(view)
                )
                and (
                    not self._resource_filter.strip()
                    or self._resource_filter.strip().lower() in view.searchable_text
                )
            ]
        )
        next_order = [view.key for view in views]
        # A filter handler deliberately invalidates ``_resource_order`` to
        # force a rebuild.  When it produces no rows both lists are empty,
        # so also consult the mounted table; otherwise stale rows remain
        # visible after filtering an all-healthy stack to "Problems only".
        order_changed = next_order != self._resource_order or (
            not next_order and table.row_count > 0
        )
        if order_changed:
            table.clear()
            self._resource_rows.clear()
            for view in views:
                label = (
                    f"{self._symbols.remote_prefix} {view.resource_id}"
                    if view.is_remote
                    else view.resource_id
                )
                row = (
                    "dev" if view.kind == "device" else "proc",
                    label,
                    view.connection or "",
                    view.health,
                    view.state,
                    format_age(view.age_s),
                    (view.error or "")[:40],
                )
                table.add_row(
                    row[0],
                    row[1],
                    self._connection_cell(row[2], health=view.health),
                    self._health_cell(row[3]),
                    self._runtime_cell(row[4]),
                    row[5],
                    self._error_cell(row[6]),
                    key=view.key,
                )
                self._resource_rows[view.key] = row
            self._resource_order = next_order
        else:
            columns = (
                "kind",
                "resource",
                "connection",
                "health",
                "runtime",
                "age_s",
                "error",
            )
            for view in views:
                label = (
                    f"{self._symbols.remote_prefix} {view.resource_id}"
                    if view.is_remote
                    else view.resource_id
                )
                row = (
                    "dev" if view.kind == "device" else "proc",
                    label,
                    view.connection or "",
                    view.health,
                    view.state,
                    format_age(view.age_s),
                    (view.error or "")[:40],
                )
                previous = self._resource_rows.get(view.key)
                if previous != row:
                    health_changed = previous is None or previous[3] != view.health
                    for index, (column, value) in enumerate(
                        zip(columns, row, strict=True)
                    ):
                        if (
                            previous is None
                            or previous[index] != value
                            or (column == "connection" and health_changed)
                        ):
                            if column == "health":
                                cell: str | Text = self._health_cell(value)
                            elif column == "connection":
                                cell = self._connection_cell(value, health=view.health)
                            elif column == "runtime":
                                cell = self._runtime_cell(value)
                            elif column == "error":
                                cell = self._error_cell(value)
                            else:
                                cell = value
                            table.update_cell(view.key, column, cell)
                    self._resource_rows[view.key] = row

        if self._selected_resource_key not in next_order:
            self._selected_resource_key = next_order[0] if next_order else None
            selected = self._resource_views.get(self._selected_resource_key or "")
            if selected is not None and selected.kind == "device":
                self._selected_device_id = selected.resource_id
                self._inspector_mode = "device"
                self._members_source = "device"
            elif selected is not None:
                self._selected_process_id = selected.resource_id
                self._inspector_mode = "process"
                self._members_source = "process"
        if self._selected_resource_key in next_order:
            table.move_cursor(
                row=next_order.index(self._selected_resource_key), column=0
            )
        if preserve_scroll and order_changed:
            self.call_after_refresh(self._restore_resource_scroll, scroll_x, scroll_y)
        self._update_health_summary(all_views)
        self._refresh_selected_resource_chrome()

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

        stale_render_attempts = (
            set(self._dev_cap_render_attempt_mono) - active_device_ids
        )
        for device_id in stale_render_attempts:
            self._dev_cap_render_attempt_mono.pop(device_id, None)

        stale_member_fingerprints = [
            key
            for key in self._members_rendered_fingerprint
            if key.startswith("device:")
            and key.split(":", 1)[1] not in active_device_ids
        ]
        for key in stale_member_fingerprints:
            self._members_rendered_fingerprint.pop(key, None)

        config_cache = getattr(self, "_config_cache", {})
        config_errors = getattr(self, "_config_errors", {})
        for key in list(config_cache):
            if (
                key.startswith("device:")
                and key.split(":", 1)[1] not in active_device_ids
            ):
                config_cache.pop(key, None)
                config_errors.pop(key, None)

    def _prune_process_caches(self, *, active_process_ids: set[str]) -> None:
        process_telemetry = getattr(self, "_process_telemetry_cache", {})
        stale_telemetry = set(process_telemetry) - active_process_ids
        for process_id in stale_telemetry:
            process_telemetry.pop(process_id, None)

        stale_proc_caps = set(self._proc_cap_cache) - active_process_ids
        for process_id in stale_proc_caps:
            self._proc_cap_cache.pop(process_id, None)
            self._proc_members_last.pop(process_id, None)
            self._reset_process_capabilities_retry(process_id)

        stale_render_attempts = (
            set(self._proc_cap_render_attempt_mono) - active_process_ids
        )
        for process_id in stale_render_attempts:
            self._proc_cap_render_attempt_mono.pop(process_id, None)

        stale_member_fingerprints = [
            key
            for key in self._members_rendered_fingerprint
            if key.startswith("process:")
            and key.split(":", 1)[1] not in active_process_ids
        ]
        for key in stale_member_fingerprints:
            self._members_rendered_fingerprint.pop(key, None)

        config_cache = getattr(self, "_config_cache", {})
        config_errors = getattr(self, "_config_errors", {})
        for key in list(config_cache):
            if (
                key.startswith("process:")
                and key.split(":", 1)[1] not in active_process_ids
            ):
                config_cache.pop(key, None)
                config_errors.pop(key, None)

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
                "hb_age",
                "telemetry_age",
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
                    format_age(status.hb_age_s),
                    format_age(status.telemetry_age_s),
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
                "hb_age",
                "restart_count",
                "last_exit_code",
                "last_error",
            ]
            for proc in self._processes:
                pid = str(proc.get("process_id", ""))
                is_remote = bool(proc.get("is_remote")) or (
                    str(proc.get("source_kind", "")).strip().lower() == "federated"
                )
                # Display-only ⇄ badge for federated rows; the row KEY stays the
                # raw process_id so cell updates/removals keep working.
                label = f"{self._symbols.remote_prefix} {pid}" if is_remote else pid
                hb_age = ""
                hb_age_val = proc.get("hb_age_s")
                if isinstance(hb_age_val, (int, float)):
                    hb_age = format_age(max(0.0, float(hb_age_val)))
                else:
                    last_hb_t_mono = proc.get("last_hb_t_mono")
                    if isinstance(last_hb_t_mono, (int, float)):
                        hb_age = format_age(
                            max(0.0, time.monotonic() - float(last_hb_t_mono))
                        )
                row_values = [
                    label,
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
        with self._error_counts_lock:
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
        self.query_one("#process_table", DataTable).clear()
        if self._members_source == "process":
            self._render_process_inspector()
        else:
            self._render_device_inspector()
        self._render_members_table()
        telemetry = self.query_one("#telemetry_table", DataTable)
        empty = self.query_one("#telemetry_empty", Static)
        if telemetry.row_count:
            empty.display = False
        else:
            noun = "process" if self._members_source == "process" else "device"
            empty.update(f"No {noun} telemetry published")
            empty.display = True

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
                if isinstance(ts, dict) and "t_mono_recv" in ts:
                    age = max(0.0, time.monotonic() - float(ts["t_mono_recv"]))
                elif isinstance(ts, dict) and "t_mono" in ts:
                    age = max(0.0, time.monotonic() - float(ts["t_mono"]))
                telemetry.add_row(
                    name,
                    str(entry.get("value")),
                    str(entry.get("units")),
                    str(entry.get("quality")),
                    format_age(age),
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
        details = self.query_one("#process_table", DataTable)
        self.query_one("#process_title", Label).update("CONNECTION")
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
                for field, value in (
                    ("connection", self._device_connection(status)),
                    ("registered", status.registered),
                    ("reachable", status.device_reachable),
                    ("liveness", status.liveness),
                    (
                        "heartbeat_age",
                        format_age(status.hb_age_s)
                        if status.hb_age_s is not None
                        else None,
                    ),
                    (
                        "telemetry_age",
                        format_age(status.telemetry_age_s)
                        if status.telemetry_age_s is not None
                        else None,
                    ),
                    ("source", "federated" if status.is_remote else "local"),
                    ("owner_peer", status.owner_peer_id),
                ):
                    if value is not None:
                        details.add_row(field, str(value))

    def _render_process_inspector(self) -> None:
        process_id = self._selected_process_id
        telemetry = self.query_one("#telemetry_table", DataTable)
        telemetry.clear()
        heartbeat = self.query_one("#heartbeat_table", DataTable)
        heartbeat.clear()
        driver = self.query_one("#driver_table", DataTable)
        driver.clear()
        details = self.query_one("#process_table", DataTable)
        self.query_one("#process_title", Label).update("PROCESS")

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

        for key in (
            "registered",
            "rpc_endpoint",
            "process_data_endpoint",
            "popen_pid",
            "heartbeat_pid",
            "rss_bytes",
            "hb_age_s",
            "last_error_kind",
            "exit_code_description",
            "source_kind",
            "owner_peer_id",
        ):
            value = selected.get(key)
            if value not in (None, ""):
                display_key = "heartbeat_age" if key == "hb_age_s" else key
                if key == "hb_age_s" and isinstance(value, (int, float)):
                    value = format_age(float(value))
                details.add_row(display_key, str(value)[:120])

        hb_age = ""
        last_hb_t_mono = selected.get("last_hb_t_mono")
        hb_age_val = selected.get("hb_age_s")
        if isinstance(hb_age_val, (int, float)):
            hb_age = format_age(max(0.0, float(hb_age_val)))
        elif isinstance(last_hb_t_mono, (int, float)):
            hb_age = format_age(max(0.0, time.monotonic() - float(last_hb_t_mono)))
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

        for name, entry in self._process_telemetry_cache.get(process_id, {}).items():
            if not isinstance(entry, dict):
                continue
            ts = entry.get("ts", {})
            age = None
            if isinstance(ts, dict) and "t_mono_recv" in ts:
                age = max(0.0, time.monotonic() - float(ts["t_mono_recv"]))
            elif isinstance(ts, dict) and "t_mono" in ts:
                age = max(0.0, time.monotonic() - float(ts["t_mono"]))
            telemetry.add_row(
                name,
                str(entry.get("value")),
                str(entry.get("units", "")),
                str(entry.get("quality", "")),
                format_age(age),
            )

    @staticmethod
    def _members_render_fingerprint(members_render: list[dict[str, Any]]) -> int:
        """Hash of only the fields that determine the rendered member rows
        (name, kind, readable, settable, type annotation, source, doc)."""
        return hash(
            tuple(
                (
                    str(m.get("name", "")),
                    str(m.get("kind", "")),
                    bool(m.get("readable", False)),
                    bool(m.get("settable", False)),
                    str(m.get("return_annotation") or ""),
                    str(m.get("value_annotation") or ""),
                    str(m.get("source", "")),
                    str(m.get("doc", "") or "")[:40],
                )
                for m in members_render
            )
        )

    def _render_members_table(self) -> None:
        table = self.query_one("#members_table", DataTable)
        if self._members_source == "process":
            process_id = self._selected_process_id
            if not process_id:
                table.clear()
                return
            context_key = f"process:{process_id}"
            proc = None
            for item in self._processes:
                if str(item.get("process_id", "")) == process_id:
                    proc = item
                    break
            if process_id not in self._proc_members_last:
                # Forced fetch (like the manual Refresh), throttled per process,
                # so federated processes auto-populate instead of getting stuck
                # behind the non-force path's retry backoff. probe_ready gates on
                # RUNNING + registered (same as the manual path).
                if self._process_capabilities_probe_ready(process_id):
                    now = time.monotonic()
                    last = self._proc_cap_render_attempt_mono.get(process_id, 0.0)
                    if (now - last) >= self._cap_render_retry_s:
                        self._proc_cap_render_attempt_mono[process_id] = now
                        # Non-blocking: fetch on the RPC worker; when it lands
                        # the callback updates the cache and re-renders. The
                        # render itself stays I/O-free.
                        self._request_process_capabilities(process_id, force=True)
            members = self._proc_members_last.get(process_id, [])
            # Federated processes: hide actions the federation link denies (the
            # hub annotates each member with federation_allowed).
            if proc and (
                bool(proc.get("is_remote"))
                or str(proc.get("source_kind", "")).strip().lower() == "federated"
            ):
                members = [
                    m
                    for m in members
                    if isinstance(m, dict) and m.get("federation_allowed") is not False
                ]
        else:
            device_id = self._selected_device_id
            if not device_id:
                table.clear()
                return
            context_key = f"device:{device_id}"

            if device_id not in self._members_last:
                status = self._device_status.get(device_id)
                # A federated (mirrored) device has no local driver process, so
                # _status_driver_stopped() is True (driver_pid is None) and the
                # normal gate would skip it forever — the caps live on the peer.
                # Treat is_remote devices as eligible and fetch (forced, like the
                # manual Refresh) as long as they aren't DISCONNECTED. Throttled
                # per device so an unavailable peer can't be hammered.
                if (
                    status
                    and status.device_state != "DISCONNECTED"
                    and (status.is_remote or not self._status_driver_stopped(status))
                ):
                    # Non-blocking: fetch on the RPC worker; the callback
                    # updates the cache and re-renders. Render stays I/O-free.
                    if status.is_remote:
                        now = time.monotonic()
                        last = self._dev_cap_render_attempt_mono.get(device_id, 0.0)
                        if (now - last) >= self._cap_render_retry_s:
                            self._dev_cap_render_attempt_mono[device_id] = now
                            self._request_device_capabilities(device_id, force=True)
                    else:
                        self._request_device_capabilities(device_id)

            members = self._members_last.get(device_id, [])

        preserve_scroll = context_key == self._members_context_key
        self._members_context_key = context_key

        members_render = sorted(
            [m for m in members if isinstance(m, dict)],
            key=lambda d: (str(d.get("kind", "")), str(d.get("name", ""))),
        )
        # Change-detection fingerprint over exactly the fields that affect the
        # rendered rows. A tuple-hash is ~4–5× cheaper than json.dumps of the
        # full member dicts (which this render runs on every inspector tick).
        fingerprint = self._members_render_fingerprint(members_render)
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
        # Skip the full clear+rebuild (≈7 ms at the 200-row cap) when the error
        # set has not changed since the last render.
        if self._errors_rev == self._errors_rendered_rev:
            return
        self._errors_rendered_rev = self._errors_rev
        table = self.query_one("#errors_table", DataTable)
        table.clear()
        for row_index, entry in enumerate(reversed(self._errors)):
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
            message = (
                message.replace("\r\n", " | ").replace("\n", " | ").replace("\r", " | ")
            )
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
                # A fingerprint identifies the error *content*, not an individual
                # displayed occurrence.  Multiple sources may legitimately emit
                # the same fingerprint, whereas Textual requires table row keys
                # to be unique.
                key=f"error-{row_index}",
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
        severity_value: Literal["information", "warning", "error"]
        if severity == "information":
            severity_value = "information"
        elif severity == "error":
            severity_value = "error"
        else:
            severity_value = "warning"
        try:
            self.notify(message, severity=severity_value)
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
        self._errors_rev += 1
        if hasattr(self, "_activity_open") and not self._activity_open:
            if severity in {"error", "critical"}:
                self._activity_unread_error += 1
            else:
                self._activity_unread_warning += 1
            self._update_activity_summary()
        if render and getattr(self, "_activity_open", True):
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
                    if (
                        device_id
                        and device_id != "None"
                        and stream
                        and stream != "None"
                    ):
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
        if topic in {
            "manager.telemetry_update",
            "manager.heartbeat",
            "manager.process_telemetry_update",
        }:
            key = self._state_message_key(topic, payload)
            if key is not None:
                with self._state_lock:
                    if key in self._latest_state_messages:
                        self._coalesced_pub_messages += 1
                    self._latest_state_messages[key] = (topic, payload)
                return
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

    @staticmethod
    def _state_message_key(topic: str, payload: Json) -> tuple[str, str] | None:
        resource_id = str(payload.get("device_id") or payload.get("process_id") or "")
        if not resource_id:
            return None
        if topic == "manager.chunk_ready":
            stream = str(payload.get("stream") or "")
            return (topic, f"{resource_id}:{stream}")
        return (topic, resource_id)

    def _drain_pub_queue(self) -> None:
        if not self.streaming_enabled:
            return

        slice_started = time.perf_counter()
        deadline = slice_started + self._pub_drain_budget_s
        log = self.query_one("#event_log", RichLog)
        errors_dirty = False
        with self._chunk_lock:
            cached = list(self._chunk_cache.values())
            self._chunk_cache.clear()
        with self._state_lock:
            latest = list(self._latest_state_messages.values())
            self._latest_state_messages.clear()
        pending_state = cached + latest

        processed = 0
        while processed < self._pub_drain_max:
            if pending_state:
                topic, payload = pending_state.pop()
            else:
                try:
                    topic, payload = self._pub_queue.get_nowait()
                except queue.Empty:
                    break
            processed += 1

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
            elif topic == "manager.process_telemetry_update":
                process_raw = payload.get("process_id")
                if process_raw is None:
                    continue
                process_id = str(process_raw)
                signals = payload.get("signals", {})
                if isinstance(signals, dict):
                    process_cache = self._process_telemetry_cache.setdefault(
                        process_id, {}
                    )
                    process_cache.update(signals)
                    if process_id == self._selected_process_id:
                        self._mark_inspector_dirty()
            elif topic == "manager.device_config":
                device_id = str(payload.get("device_id", ""))
                config_key = f"device:{device_id}"
                display_config = payload.get("display_config")
                if device_id and isinstance(display_config, dict):
                    config = redact_config(display_config)
                    connect_check = payload.get("connect_check")
                    if isinstance(connect_check, dict):
                        config["connect_check"] = redact_config(connect_check)
                    self._config_cache[config_key] = config
                    self._config_errors.pop(config_key, None)
                    if config_key == self._selected_resource_key:
                        self._render_config_table()
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
                if self._ingest_manager_log_entry(payload):
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
                        line = self._format_manager_log_event_line(payload)
                    elif topic == "manager.device_config":
                        line = (
                            "manager.device_config "
                            f"device_id={payload.get('device_id', '')} "
                            f"revision={payload.get('metadata_revision', '')}"
                        )
                    else:
                        try:
                            payload_text = json.dumps(payload)
                        except Exception:
                            payload_text = str(payload)
                        line = f"{topic} {payload_text[:200]}"
                    self._event_lines.append(line)
                    if self._activity_open:
                        log.write(line)
                except Exception:
                    self._bump_error("log.write")
            if time.perf_counter() >= deadline:
                break
        if pending_state:
            with self._state_lock:
                for topic, payload in pending_state:
                    key = self._state_message_key(topic, payload)
                    if key is not None:
                        self._latest_state_messages[key] = (topic, payload)
        if errors_dirty and self._activity_open:
            self._render_errors_table()
        dropped = self.query_one("#dropped_status", Static)
        dropped.update(self._dropped_status_text())

        self._render_inspector_if_needed()
        elapsed = time.perf_counter() - slice_started
        self._max_drain_slice_s = max(self._max_drain_slice_s, elapsed)
        if not self._pub_queue.empty() or self._latest_state_messages:
            self.call_later(self._drain_pub_queue)

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

    @work(thread=True, exit_on_error=False, group="bulk-rpc")
    def _run_bulk_rpc_worker(
        self,
        *,
        items: list[tuple[str, Json]],
        label: str,
        summary_label: str,
        error_log_prefix: str | None = None,
        skipped_count: int = 0,
    ) -> None:
        """Run a sequence of blocking _rpc_calls on a worker thread.

        Used by the TUI's bulk action handlers (start-all / stop-all)
        so the UI event loop stays responsive during what was
        previously an N * rpc_timeout_ms freeze. UI updates (notify,
        log-write, error-record) are scheduled back to the main
        thread via call_from_thread, which Textual's @work decorator
        provides transparently when the called methods are themselves
        thread-safe.

        Parameters:
            items: list of (id, payload) tuples to invoke. The id is
                used for notifications and error records.
            label: short verb-noun used in success notifications
                (e.g. "Process start" -> "Process start sent: foo").
            summary_label: text for the final aggregated notification
                ("Start all processes: 5 ok, 0 failed").
            error_log_prefix: when set, failures additionally write
                a one-line entry to the event log (used by
                action_drivers_stop_all's verbose error reporting).
                When None, only the success/error notification fires.
        """
        ok_count = 0
        fail_count = 0
        for item_id, payload in items:
            if not item_id:
                continue
            # _rpc_call is the blocking ZMQ round-trip; running it on
            # a worker thread is the whole point of this helper.
            resp = self._rpc_call(payload)
            if resp is not None and resp.get("ok"):
                ok_count += 1
                # Per-item success notification (cheap; Notify is
                # thread-safe in Textual >= 0.50).
                self.call_from_thread(self.notify, f"{label} sent: {item_id}")
            else:
                fail_count += 1
                if resp is None:
                    err: Any = "timeout"
                else:
                    err = resp.get("error", "unknown error")
                err_text = json.dumps(err) if isinstance(err, dict) else str(err)
                # Per-item error notification.
                self.call_from_thread(
                    self.notify,
                    f"{label} failed: {item_id} ({err_text})",
                    severity="error",
                )
                # Error record (drives the errors-table view).
                self.call_from_thread(
                    self._record_action_error,
                    source="device" if "device_id" in payload else "process",
                    id_=item_id,
                    message=f"{label} failed: {item_id} ({err_text})",
                )
                if error_log_prefix is not None:
                    self.call_from_thread(
                        self._log_action_result,
                        f"{error_log_prefix} {item_id} -> error {err_text}",
                    )
        # Final aggregated summary so operators see the total even if
        # they missed the per-item toasts.
        summary = f"{summary_label}: {ok_count} ok, {fail_count} failed"
        if skipped_count:
            summary += f", {skipped_count} skipped"
        self.call_from_thread(self.notify, summary)

    def _reconnect_backend(self) -> None:
        # Non-blocking. The socket reset and the identity probe are enqueued on
        # the RPC worker (FIFO: reset runs first); the result is applied on the
        # UI thread. The UI thread never blocks on the round-trip.
        self._set_backend_status("Backend: reconnecting")
        self._log_action_result("Reconnecting backend...")
        self._reset_rpc_socket()
        self._reset_bg_rpc_socket()
        self._request_sub_reconnect()

        def _after(resp: Json | None) -> None:
            if not resp:
                self._set_backend_status("Backend: unavailable")
                self._log_action_result("Backend reconnect failed")
                self.notify("Backend reconnect failed", severity="warning")
                return
            self._rpc_submit(
                {
                    "type": "manager.logs.tail",
                    "params": {"limit": self._log_tail_bootstrap_limit},
                },
                self._apply_manager_log_tail_bootstrap,
            )
            self._refresh_snapshot()
            self._set_backend_status("Backend: connected")
            self._log_action_result("Backend reconnected")
            self.notify("Backend reconnected")

        self._rpc_submit({"type": "manager.info.identity"}, _after)

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

    @staticmethod
    def _format_result_pretty(result: Any) -> str:
        try:
            return json.dumps(result, indent=2, ensure_ascii=False, default=str)
        except Exception:
            return str(result)

    def _show_command_result(self, title: str, result: Any) -> None:
        if result is None:
            return
        self.push_screen(ResultScreen(title, self._format_result_pretty(result)))

    @staticmethod
    def _flatten_config(value: Any, prefix: str = "") -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if isinstance(value, dict):
            for key in sorted(value, key=str):
                path = f"{prefix}.{key}" if prefix else str(key)
                rows.extend(ManagerTUI._flatten_config(value[key], path))
        elif isinstance(value, list):
            rows.append((prefix, json.dumps(value, ensure_ascii=False, default=str)))
        elif value is not None:
            rows.append((prefix, str(value)))
        return rows

    def _ensure_selected_config(self) -> None:
        key = self._selected_resource_key
        if not key or key in self._config_cache or key in self._config_fetch_inflight:
            return
        kind, resource_id = key.split(":", 1)
        if kind == "device":
            payload = {
                "type": "device.config.get",
                "device_id": resource_id,
                "redact_sensitive": True,
            }
        else:
            payload = {
                "type": "manager.processes.config.get",
                "process_id": resource_id,
            }
        self._config_fetch_inflight.add(key)

        def _after(resp: Json | None) -> None:
            self._config_fetch_inflight.discard(key)
            if resp and resp.get("ok") and isinstance(resp.get("result"), dict):
                result = redact_config(dict(resp["result"]))
                result.pop("yaml_text", None)
                if kind == "device":
                    result = {
                        name: result[name]
                        for name in ("driver", "init_kwargs", "connect_check")
                        if name in result
                    }
                self._config_cache[key] = result
                self._config_errors.pop(key, None)
            else:
                error = resp.get("error") if isinstance(resp, dict) else "unavailable"
                if isinstance(error, dict):
                    error = error.get("message") or error.get("code") or "unavailable"
                self._config_errors[key] = str(error)
            if self._selected_resource_key == key:
                self._render_config_table()

        self._background_rpc_submit(payload, _after)

    def _render_config_table(self) -> None:
        table = self.query_one("#config_table", DataTable)
        table.clear()
        key = self._selected_resource_key
        if not key:
            return
        config = self._config_cache.get(key)
        if config is None:
            message = self._config_errors.get(key, "Loading configuration...")
            table.add_row("status", message)
            return
        rows = self._flatten_config(config)
        if not rows:
            table.add_row("status", "No configuration exposed")
            return
        for setting, value in rows:
            table.add_row(setting, value[:240])

    def _select_resource_key(self, key: str, *, user: bool = True) -> None:
        view = self._resource_views.get(key)
        if view is None:
            return
        self._selected_resource_key = key
        if view.kind == "device":
            self._selected_device_id = view.resource_id
            if user:
                self._has_user_selection = True
            self._set_inspector_mode("device")
        else:
            self._selected_process_id = view.resource_id
            if user:
                self._has_user_process_selection = True
            self._set_inspector_mode("process")
        self._refresh_selected_resource_chrome()
        self._ensure_selected_config()
        self._render_config_table()
        self._mark_inspector_dirty()
        self._render_inspector_if_needed(force=True)

    def _refresh_selected_resource_chrome(self) -> None:
        view = self._resource_views.get(self._selected_resource_key or "")
        title: Text | str = "NO RESOURCE SELECTED"
        if view is not None:
            statuses = (
                (
                    "LINK",
                    self._connection_cell(view.connection, health=view.health),
                    view.connection.upper() if view.connection else "",
                ),
                ("HEALTH", self._health_cell(view.health), view.health.upper()),
                (
                    "RUN",
                    self._runtime_cell(view.state or "unknown"),
                    (view.state or "unknown").upper(),
                ),
            )

            def _build_title(*, compact: bool) -> Text:
                result = Text.assemble(
                    (f"{view.kind.upper()} // ", MUTED_TEXT),
                    (view.resource_id, SECONDARY_TEXT),
                )
                for label, indicator, state in statuses:
                    result.append("  |  ", style=DIM_TEXT)
                    if not compact:
                        result.append(f"{label} ", style=DIM_TEXT)
                    result.append(indicator.plain, style=indicator.style)
                    if state:
                        result.append(f" {state}", style=indicator.style)
                if view.is_remote:
                    result.append("  |  REMOTE // ", style=DIM_TEXT)
                    result.append(
                        (view.owner_peer_id or "peer").upper(), style=SECONDARY_TEXT
                    )
                return result

            full_title = _build_title(compact=False)
            available_width = max(0, self.size.width - 2)
            title = (
                full_title
                if full_title.cell_len <= available_width
                else _build_title(compact=True)
            )
        try:
            self.query_one("#selected_resource_title", Label).update(title)
        except Exception:
            pass

        enabled_actions: set[str] = set()
        if view is not None and view.kind == "device":
            status = self._device_status.get(view.resource_id)
            enabled_actions = self._device_enabled_actions(status)
        elif view is not None:
            proc = self._get_process_record(view.resource_id)
            if self._status_process_stopped(proc):
                enabled_actions.add("start")
            else:
                enabled_actions.add("stop")
            if proc is not None:
                enabled_actions.add("restart")

        pending = bool(
            self._selected_resource_key
            and self._selected_resource_key in self._pending_resource_actions
        )
        for action in ("start", "stop", "restart", "connect", "disconnect", "recover"):
            try:
                self.query_one(f"#action_{action}", Button).disabled = (
                    action not in enabled_actions or pending
                )
            except Exception:
                pass

    def _device_enabled_actions(self, status: DeviceStatus | None) -> set[str]:
        if status is None:
            return set()
        enabled: set[str] = set()
        connection = self._device_connection(status)
        if not status.is_remote:
            if self._status_driver_stopped(status):
                enabled.add("start")
            else:
                enabled.update({"stop", "restart"})
        if status.is_remote or not self._status_driver_stopped(status):
            if connection == "disconnected":
                enabled.add("connect")
            elif connection in {"connected", "stale"}:
                enabled.add("disconnect")
        if self._device_health(status) in {"failed", "stale"}:
            enabled.add("recover")
        return enabled

    def _submit_resource_action(
        self,
        *,
        resource_key: str,
        payload: Json,
        on_result: Callable[[Json | None], None] | None = None,
    ) -> None:
        if resource_key in self._pending_resource_actions:
            return
        self._pending_resource_actions.add(resource_key)
        self._refresh_selected_resource_chrome()

        def _after(resp: Json | None) -> None:
            self._pending_resource_actions.discard(resource_key)
            self._refresh_selected_resource_chrome()
            if on_result is not None:
                on_result(resp)

        self._rpc_submit(payload, _after)

    def _bg_rpc_worker_loop(self) -> None:
        """Own the polling/capability DEALER independently from actions."""
        self._bg_rpc_worker_ident = threading.get_ident()
        while True:
            job = self._bg_rpc_req_q.get()
            if job is None:
                break
            try:
                job()
            except Exception:
                pass

    def _background_do_rpc(self, payload: Json) -> Json | None:
        try:
            request = dict(payload)
            expected_request_id = request.get("request_id")
            if expected_request_id is None:
                self._bg_rpc_seq += 1
                expected_request_id = f"tui-bg-{self._bg_rpc_seq}"
                request["request_id"] = expected_request_id
            while self._bg_rpc.poll(0, zmq.POLLIN):
                _ = self._bg_rpc.recv(zmq.NOBLOCK)
            self._bg_rpc.send(json_dumps(request))
            deadline = time.monotonic() + (self._rpc_timeout_ms / 1000.0)
            while True:
                remaining_s = deadline - time.monotonic()
                if remaining_s <= 0:
                    raise TimeoutError
                if not self._bg_rpc.poll(
                    int(max(1.0, remaining_s * 1000.0)), zmq.POLLIN
                ):
                    raise TimeoutError
                raw = self._bg_rpc.recv()
                resp = safe_json_loads(raw)
                if not isinstance(resp, dict):
                    continue
                if (
                    resp.get("request_id") is not None
                    and resp.get("request_id") != expected_request_id
                ):
                    continue
                self._set_backend_status("Backend: connected")
                return resp
        except Exception:
            self._set_backend_status("Backend: unavailable")
            self._do_reset_bg_rpc_socket()
            return None

    def _background_rpc_submit(
        self,
        payload: Json,
        on_result: Callable[[Json | None], None] | None = None,
    ) -> None:
        def _job() -> None:
            resp = self._background_do_rpc(payload)
            if on_result is not None:
                try:
                    self.call_from_thread(on_result, resp)
                except Exception:
                    pass

        self._bg_rpc_req_q.put(_job)

    @on(DataTable.HeaderSelected, "#resources_table")
    def _on_resource_header_selected(self, event: DataTable.HeaderSelected) -> None:
        selected_key = self._row_key_str(event.column_key)
        for column, _label in _RESOURCE_COLUMNS:
            if column == selected_key:
                self._set_resource_sort(column)
                return

    @on(DataTable.RowHighlighted, "#resources_table")
    def _on_resource_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._select_resource_key(self._row_key_str(event.row_key), user=True)

    @on(DataTable.RowSelected, "#resources_table")
    def _on_resource_selected(self, event: DataTable.RowSelected) -> None:
        self._select_resource_key(self._row_key_str(event.row_key), user=True)
        if self._narrow_layout:
            self._narrow_show_inspector = True
            self._apply_responsive_layout(self.size.width)

    @on(Input.Changed, "#resource_filter")
    def _on_resource_filter_changed(self, event: Input.Changed) -> None:
        self._resource_filter = event.value
        self._resource_order = []
        self._render_resources_table()

    @on(Button.Pressed, "#action_strip Button")
    def _on_action_button(self, event: Button.Pressed) -> None:
        action_by_id = {
            "action_start": self.action_driver_start,
            "action_stop": self.action_driver_stop,
            "action_restart": self.action_driver_restart,
            "action_connect": self.action_device_connect,
            "action_disconnect": self.action_device_disconnect,
            "action_recover": self.action_device_recover,
        }
        action = action_by_id.get(str(event.button.id))
        if action is not None:
            action()

    @on(DataTable.RowSelected, "#errors_table")
    def _on_error_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#errors_table", DataTable)
        try:
            row = table.get_row(event.row_key)
        except Exception:
            return
        if len(row) < 4:
            return
        source = str(row[2]).lower()
        resource_id = str(row[3])
        kind = "process" if source == "process" else "device"
        key = f"{kind}:{resource_id}"
        if key not in self._resource_views:
            return
        self._resource_filter = ""
        self._unhealthy_only = False
        try:
            self.query_one("#resource_filter", Input).value = ""
        except Exception:
            pass
        self._resource_order = []
        self._render_resources_table()
        self._select_resource_key(key)
        self.action_inspector_overview()
        if self._narrow_layout:
            self._narrow_show_inspector = True
            self._apply_responsive_layout(self.size.width)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        del width
        # The navigator now uses the full terminal width above the inspector,
        # so both regions remain useful even on narrow terminals.
        self._narrow_layout = False
        self._narrow_show_inspector = False
        try:
            self.screen.set_class(False, "-narrow")
            navigator = self.query_one("#navigator", Vertical)
            inspector = self.query_one("#inspector", Vertical)
            navigator.display = True
            inspector.display = True
        except Exception:
            pass

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
        self._selected_process_id = self._row_key_str(event.row_key)
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
        self._selected_process_id = self._row_key_str(event.row_key)
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
        status.update(self._streaming_status_text())

    async def action_quit(self) -> None:
        # Send the shutdown RPC from a worker thread so the (blocking) ZMQ
        # poll doesn't freeze the TUI event loop. The exit() call waits for
        # the worker to finish via call_from_thread.
        def _shutdown_then_exit() -> None:
            try:
                self._rpc_call({"type": "manager.control.shutdown"})
            except Exception:
                pass
            self.call_from_thread(self.exit)

        worker = threading.Thread(
            target=_shutdown_then_exit,
            name="tui-shutdown-rpc",
            daemon=True,
        )
        worker.start()

    def action_reconnect_backend(self) -> None:
        try:
            self._reconnect_backend()
        except Exception as exc:
            self._set_backend_status("Backend: unavailable")
            self._log_action_result(f"Backend reconnect error: {exc}")
            self.notify(f"Backend reconnect error: {exc}", severity="error")

    def action_capabilities_refresh(self) -> None:
        if self._members_source == "process":
            process_id = self._selected_process_id
            if not process_id:
                return

            def _done_proc(ok: bool) -> None:
                self.notify(
                    f"Capabilities refreshed: {process_id}"
                    if ok
                    else f"Capabilities refresh failed: {process_id}"
                )

            self._request_process_capabilities(
                process_id, force=True, on_done=_done_proc
            )
            return

        device_id = self._selected_device()
        if not device_id:
            return

        def _done_dev(ok: bool) -> None:
            self.notify(
                f"Capabilities refreshed: {device_id}"
                if ok
                else f"Capabilities refresh failed: {device_id}"
            )

        self._request_device_capabilities(device_id, force=True, on_done=_done_dev)

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

                def _after_proc_call(resp: Json | None) -> None:
                    if resp and resp.get("ok"):
                        result = resp.get("result")
                        text = (
                            self._format_result(result) if result is not None else "ok"
                        )
                        self._log_action_result(
                            f"PROC CALL {process_id}.{name} kwargs={{}} -> {text}"
                        )
                        self.notify(f"Call ok: {process_id}.{name}")
                        self._show_command_result(
                            f"Result {self._symbols.separator} {process_id}.{name}", result
                        )
                    else:
                        err = resp.get("error", "unknown error") if resp else "timeout"
                        err_text = (
                            json.dumps(err) if isinstance(err, dict) else str(err)
                        )
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

                self._process_rpc_submit(
                    process_id, {"type": name, "params": {}}, _after_proc_call
                )
                return

            def _on_dismiss(params: dict[str, Any] | None) -> None:
                if params is None:
                    return

                def _after(resp: Json | None) -> None:
                    if resp and resp.get("ok"):
                        result = resp.get("result")
                        text = (
                            self._format_result(result) if result is not None else "ok"
                        )
                        self._log_action_result(
                            f"PROC CALL {process_id}.{name} kwargs={json.dumps(params)} -> {text}"
                        )
                        self.notify(f"Call ok: {process_id}.{name}")
                        self._show_command_result(
                            f"Result {self._symbols.separator} {process_id}.{name}", result
                        )
                    else:
                        err = resp.get("error", "unknown error") if resp else "timeout"
                        err_text = (
                            json.dumps(err) if isinstance(err, dict) else str(err)
                        )
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

                self._process_rpc_submit(
                    process_id, {"type": name, "params": params}, _after
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

                def _after_dev_call(resp: Json | None) -> None:
                    if resp and resp.get("ok"):
                        result = resp.get("result")
                        text = (
                            self._format_result(result) if result is not None else "ok"
                        )
                        self._log_action_result(
                            f"CALL {device_id}.{name} kwargs={{}} -> {text}"
                        )
                        self.notify(f"Call ok: {device_id}.{name}")
                        self._show_command_result(
                            f"Result {self._symbols.separator} {device_id}.{name}", result
                        )
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

                self._device_command_submit(device_id, name, {}, _after_dev_call)
                return

            def _on_dismiss(params: dict[str, Any] | None) -> None:
                if params is None:
                    return

                def _after(resp: Json | None) -> None:
                    if resp and resp.get("ok"):
                        result = resp.get("result")
                        text = (
                            self._format_result(result) if result is not None else "ok"
                        )
                        self._log_action_result(
                            f"CALL {device_id}.{name} kwargs={json.dumps(params)} -> {text}"
                        )
                        self.notify(f"Call ok: {device_id}.{name}")
                        self._show_command_result(
                            f"Result {self._symbols.separator} {device_id}.{name}", result
                        )
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

                self._device_command_submit(device_id, name, params, _after)

            self.push_screen(InvokeMemberScreen(name, params_spec), _on_dismiss)
            return

        def _after_dev_get(resp: Json | None) -> None:
            if resp and resp.get("ok"):
                result = resp.get("result")
                text = self._format_result(result)
                self._log_action_result(f"GET {device_id}.{name} -> {text}")
                self.notify(f"Get ok: {device_id}.{name}")
                self._show_command_result(
                    f"Result {self._symbols.separator} {device_id}.{name}", result
                )
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

        self._device_command_submit(device_id, "get", {"name": name}, _after_dev_get)

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

            def _after(resp: Json | None) -> None:
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

            self._device_command_submit(
                device_id, "set", {"name": name, "value": value}, _after
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
            self._event_lines.clear()
            self._activity_hydration_lines.clear()
            self._activity_hydration_index = 0
            log.clear()
            self.notify("Event log cleared")
        except Exception:
            self._bump_error("log.clear")

    def action_device_connect(self) -> None:
        device_id = self._selected_device()
        if not device_id:
            return
        if "connect" not in self._device_enabled_actions(
            self._device_status.get(device_id)
        ):
            self.notify(f"Connect not available: {device_id}")
            return

        def _after(resp: Json | None) -> None:
            self._notify_rpc_result("Device connect", device_id, resp)
            if resp and resp.get("ok"):
                self._request_device_capabilities(device_id, force=True)

        self._submit_resource_action(
            resource_key=f"device:{device_id}",
            payload={"type": "device.connect", "device_id": device_id},
            on_result=_after,
        )

    def action_device_disconnect(self) -> None:
        device_id = self._selected_device()
        if not device_id:
            return
        if "disconnect" not in self._device_enabled_actions(
            self._device_status.get(device_id)
        ):
            self.notify(f"Disconnect not available: {device_id}")
            return

        def _on_dismiss(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self._submit_resource_action(
                resource_key=f"device:{device_id}",
                payload={"type": "device.disconnect", "device_id": device_id},
                on_result=lambda resp: self._notify_rpc_result(
                    "Device disconnect", device_id, resp
                ),
            )

        self.push_screen(ConfirmScreen(f"Disconnect device {device_id}?"), _on_dismiss)

    def action_driver_start(self) -> None:
        if self._action_target() == "process":
            process_id = self._selected_process()
            if not process_id:
                return
            proc = self._get_process_record(process_id)
            if self._status_process_started(proc):
                self.notify(f"Process already started: {process_id}")
                return
            self._submit_resource_action(
                resource_key=f"process:{process_id}",
                payload={"type": "manager.processes.start", "process_id": process_id},
                on_result=lambda resp: self._notify_rpc_result(
                    "Process start", process_id, resp
                ),
            )
            return

        device_id = self._selected_device()
        if not device_id:
            return
        status = self._device_status.get(device_id)
        if "start" not in self._device_enabled_actions(status):
            self.notify(f"Driver start not available: {device_id}")
            return
        self._submit_resource_action(
            resource_key=f"device:{device_id}",
            payload={"type": "device.driver.start", "device_id": device_id},
            on_result=lambda resp: self._notify_rpc_result(
                "Driver start", device_id, resp
            ),
        )

    def action_drivers_start_all(self) -> None:
        # Bulk start: previously this looped N blocking _rpc_calls on
        # the UI thread, freezing the TUI for ~N * rpc_timeout_ms even
        # in the happy case (each RPC is sync). Now the loop runs on
        # a Textual worker thread; UI updates are marshalled back via
        # call_from_thread (which @work + Notify do automatically).
        if self._action_target() == "process":
            items = [
                (
                    str(proc.get("process_id", "")),
                    {
                        "type": "manager.processes.start",
                        "process_id": str(proc.get("process_id", "")),
                    },
                )
                for proc in self._processes
                if str(proc.get("process_id", ""))
            ]
            self._run_bulk_rpc_worker(
                items=items,
                label="Process start",
                summary_label="Start all processes",
            )
            return

        items = [
            (device_id, {"type": "device.driver.start", "device_id": device_id})
            for device_id in list(self._device_status)
        ]
        self._run_bulk_rpc_worker(
            items=items,
            label="Driver start",
            summary_label="Start all drivers",
        )

    def action_devices_connect_all(self) -> None:
        eligible = [
            device_id
            for device_id, status in self._device_status.items()
            if not status.is_remote
            and "connect" in self._device_enabled_actions(status)
        ]
        skipped = len(self._device_status) - len(eligible)
        if not eligible:
            self.notify(f"Connect all: 0 connected, 0 failed, {skipped} skipped")
            return
        items = [
            (device_id, {"type": "device.connect", "device_id": device_id})
            for device_id in eligible
        ]
        self._run_bulk_rpc_worker(
            items=items,
            label="Device connect",
            summary_label="Connect all",
            skipped_count=skipped,
        )

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
                self._submit_resource_action(
                    resource_key=f"process:{process_id}",
                    payload={
                        "type": "manager.processes.stop",
                        "process_id": process_id,
                    },
                    on_result=lambda resp: self._notify_rpc_result(
                        "Process stop", process_id, resp
                    ),
                )

            self.push_screen(ConfirmScreen(f"Stop process {process_id}?"), _on_dismiss)
            return

        device_id = self._selected_device()
        if not device_id:
            return
        status = self._device_status.get(device_id)
        if "stop" not in self._device_enabled_actions(status):
            self.notify(f"Driver stop not available: {device_id}")
            return

        def _on_driver_stop_dismiss(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self._submit_resource_action(
                resource_key=f"device:{device_id}",
                payload={"type": "device.driver.stop", "device_id": device_id},
                on_result=lambda resp: self._notify_rpc_result(
                    "Driver stop", device_id, resp
                ),
            )

        self.push_screen(
            ConfirmScreen(f"Stop driver for {device_id}?"), _on_driver_stop_dismiss
        )

    def action_driver_restart(self) -> None:
        if self._action_target() == "process":
            process_id = self._selected_process()
            if not process_id:
                return

            def _on_process_restart_dismiss(confirmed: bool | None) -> None:
                if not confirmed:
                    return
                self._submit_resource_action(
                    resource_key=f"process:{process_id}",
                    payload={
                        "type": "manager.processes.restart",
                        "process_id": process_id,
                    },
                    on_result=lambda resp: self._notify_rpc_result(
                        "Process restart", process_id, resp
                    ),
                )

            self.push_screen(
                ConfirmScreen(f"Restart process {process_id}?"),
                _on_process_restart_dismiss,
            )
            return

        device_id = self._selected_device()
        if not device_id:
            return
        if "restart" not in self._device_enabled_actions(
            self._device_status.get(device_id)
        ):
            self.notify(f"Driver restart not available: {device_id}")
            return

        def _on_driver_restart_dismiss(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self._submit_resource_action(
                resource_key=f"device:{device_id}",
                payload={
                    "type": "device.driver.restart",
                    "device_id": device_id,
                    "reload_config": True,
                },
                on_result=lambda resp: self._notify_rpc_result(
                    "Driver restart", device_id, resp
                ),
            )

        self.push_screen(
            ConfirmScreen(f"Restart driver for {device_id}?"),
            _on_driver_restart_dismiss,
        )

    async def on_key(self, event: events.Key) -> None:  # type: ignore[override]
        # Work around cases where focused widgets swallow app bindings.
        if isinstance(self.screen, ModalScreen):
            event.stop()
            return
        if isinstance(self.focused, Input):
            if event.key == "escape":
                self.focused.value = ""
                self.query_one("#resources_table", DataTable).focus()
                event.stop()
            return
        if event.character and event.character.isupper():
            return
        key = event.key
        if key == "escape":
            inspector = self.query_one("#inspector", Vertical)
            if inspector.has_focus_within:
                self.action_focus_navigator()
                event.stop()
                return
        elif key == "x":
            self.action_driver_stop()
            event.stop()
        elif key == "r":
            self.action_driver_restart()
            event.stop()
        elif key == "enter":
            resources = self.query_one("#resources_table", DataTable)
            if self.focused is resources:
                if self._selected_resource_key:
                    self._select_resource_key(self._selected_resource_key)
                    self.action_focus_inspector()
                event.stop()
                return
            members = self.query_one("#members_table", DataTable)
            if self.focused is members:
                self.action_member_primary()
                event.stop()
        elif key == "left_square_bracket":
            self.action_inspector_previous()
            event.stop()
        elif key == "right_square_bracket":
            self.action_inspector_next()
            event.stop()

    def action_focus_search(self) -> None:
        search = self.query_one("#resource_filter", Input)
        search.focus()

    def action_toggle_unhealthy(self) -> None:
        self._unhealthy_only = not self._unhealthy_only
        self._resource_order = []
        self._render_resources_table()

    def action_resource_sort_next(self) -> None:
        columns = [column for column, _label in _RESOURCE_COLUMNS]
        current = columns.index(self._resource_sort_column)
        self._set_resource_sort(columns[(current + 1) % len(columns)])

    def action_resource_sort_reverse(self) -> None:
        self._set_resource_sort(self._resource_sort_column)

    def action_focus_navigator(self) -> None:
        self.query_one("#resources_table", DataTable).focus()

    def _focus_active_inspector(self) -> None:
        tabs = self.query_one("#inspector_tabs", TabbedContent)
        target_id = {
            "overview": "#driver_table",
            "telemetry": "#telemetry_table",
            "commands": "#members_table",
            "config": "#config_table",
        }.get(tabs.active, "#driver_table")
        self.query_one(target_id, DataTable).focus()

    def action_focus_inspector(self) -> None:
        self._focus_active_inspector()

    def _set_inspector_tab(self, tab_id: str) -> None:
        inspector = self.query_one("#inspector", Vertical)
        restore_focus = inspector.has_focus_within
        self.query_one("#inspector_tabs", TabbedContent).active = tab_id
        if tab_id == "commands":
            self._render_members_table()
        elif tab_id == "config":
            self._ensure_selected_config()
            self._render_config_table()
        if restore_focus:
            self._focus_active_inspector()

    def _cycle_inspector_tab(self, step: int) -> None:
        tabs = self.query_one("#inspector_tabs", TabbedContent)
        try:
            index = _INSPECTOR_TAB_IDS.index(tabs.active)
        except ValueError:
            index = 0
        self._set_inspector_tab(
            _INSPECTOR_TAB_IDS[(index + step) % len(_INSPECTOR_TAB_IDS)]
        )

    def action_inspector_previous(self) -> None:
        if self._activity_open and self.query_one(
            "#activity_drawer", Vertical
        ).has_focus_within:
            self._cycle_activity_tab(-1)
        else:
            self._cycle_inspector_tab(-1)

    def action_inspector_next(self) -> None:
        if self._activity_open and self.query_one(
            "#activity_drawer", Vertical
        ).has_focus_within:
            self._cycle_activity_tab(1)
        else:
            self._cycle_inspector_tab(1)

    def action_inspector_overview(self) -> None:
        self._set_inspector_tab("overview")

    def action_inspector_telemetry(self) -> None:
        self._set_inspector_tab("telemetry")

    def action_inspector_commands(self) -> None:
        self._set_inspector_tab("commands")

    def action_inspector_config(self) -> None:
        self._set_inspector_tab("config")

    def _focus_active_activity(self) -> None:
        tabs = self.query_one("#activity_tabs", TabbedContent)
        target = "#event_log" if tabs.active == "events" else "#errors_table"
        self.query_one(target, Widget).focus()

    def _cycle_activity_tab(self, step: int) -> None:
        tabs = self.query_one("#activity_tabs", TabbedContent)
        try:
            index = _ACTIVITY_TAB_IDS.index(tabs.active)
        except ValueError:
            index = 0
        tabs.active = _ACTIVITY_TAB_IDS[(index + step) % len(_ACTIVITY_TAB_IDS)]
        self._focus_active_activity()

    def _update_activity_summary(self) -> None:
        verb = "CLOSE" if self._activity_open else "OPEN"
        arrow = (
            self._symbols.activity_open
            if self._activity_open
            else self._symbols.activity_closed
        )
        summary = self.query_one("#activity_summary", Static)
        summary.set_class(bool(self._activity_unread_warning), "has-warning")
        summary.set_class(bool(self._activity_unread_error), "has-error")
        if self._activity_unread_error or self._activity_unread_warning:
            text = Text.assemble(
                (f"{arrow} ACTIVITY // A {verb}  |  ", MUTED_TEXT),
                (
                    f"ERRORS // {self._activity_unread_error}",
                    FAULT_RED if self._activity_unread_error else SECONDARY_TEXT,
                ),
                ("  |  ", DIM_TEXT),
                (
                    f"WARNINGS // {self._activity_unread_warning}",
                    WARNING_ORANGE if self._activity_unread_warning else SECONDARY_TEXT,
                ),
            )
        else:
            text = Text.assemble(
                (f"{arrow} ACTIVITY // A {verb}  |  ", MUTED_TEXT),
                ("NO UNREAD ALERTS", SECONDARY_TEXT),
            )
        try:
            summary.update(text)
        except Exception:
            pass

    def action_toggle_activity(self) -> None:
        self._activity_open = not self._activity_open
        drawer = self.query_one("#activity_drawer", Vertical)
        drawer.set_class(self._activity_open, "open")
        if self._activity_open:
            self._activity_return_focus = self.focused
            self._activity_unread_error = 0
            self._activity_unread_warning = 0
            self._render_errors_table()
            log = self.query_one("#event_log", RichLog)
            log.clear()
            self._activity_hydration_lines = list(self._event_lines)
            self._activity_hydration_index = 0
            self.call_later(self._hydrate_activity_log)
            self.call_after_refresh(self._focus_active_activity)
        else:
            return_focus = self._activity_return_focus
            self._activity_return_focus = None
            if return_focus is not None and return_focus.is_mounted:
                return_focus.focus()
            else:
                self.action_focus_navigator()
        self._update_activity_summary()

    @on(events.Click, "#activity_summary")
    def _on_activity_summary_clicked(self) -> None:
        self.action_toggle_activity()

    def _hydrate_activity_log(self) -> None:
        if not self._activity_open:
            return
        log = self.query_one("#event_log", RichLog)
        end = min(
            self._activity_hydration_index + 100, len(self._activity_hydration_lines)
        )
        for line in self._activity_hydration_lines[
            self._activity_hydration_index : end
        ]:
            log.write(line)
        self._activity_hydration_index = end
        if end < len(self._activity_hydration_lines):
            self.call_later(self._hydrate_activity_log)

    def action_device_recover(self) -> None:
        device_id = self._selected_device()
        if not device_id:
            return
        if "recover" not in self._device_enabled_actions(
            self._device_status.get(device_id)
        ):
            self.notify(f"Recover not available: {device_id}")
            return

        def _on_dismiss(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self._submit_resource_action(
                resource_key=f"device:{device_id}",
                payload={"type": "device.recover", "device_id": device_id},
            )

        self.push_screen(ConfirmScreen(f"Recover device {device_id}?"), _on_dismiss)

    def action_drivers_stop_all(self) -> None:
        # Bulk stop: same pattern as action_drivers_start_all — the
        # per-item loop runs on a worker thread so the UI stays
        # responsive while N processes/drivers shut down sequentially.
        if self._action_target() == "process":

            def _on_processes_stop_all_dismiss(confirmed: bool | None) -> None:
                if not confirmed:
                    return
                items = [
                    (
                        str(proc.get("process_id", "")),
                        {
                            "type": "manager.processes.stop",
                            "process_id": str(proc.get("process_id", "")),
                        },
                    )
                    for proc in self._processes
                    if str(proc.get("process_id", ""))
                ]
                self._run_bulk_rpc_worker(
                    items=items,
                    label="Process stop",
                    summary_label="Stop all processes",
                    error_log_prefix="PROC STOP",
                )

            self.push_screen(
                ConfirmScreen("Stop all processes?"), _on_processes_stop_all_dismiss
            )
            return

        def _on_drivers_stop_all_dismiss(confirmed: bool | None) -> None:
            if not confirmed:
                return
            items = [
                (device_id, {"type": "device.driver.stop", "device_id": device_id})
                for device_id in list(self._device_status)
            ]
            self._run_bulk_rpc_worker(
                items=items,
                label="Driver stop",
                summary_label="Stop all drivers",
                error_log_prefix="DRIVER STOP",
            )

        self.push_screen(
            ConfirmScreen("Stop all drivers?"), _on_drivers_stop_all_dismiss
        )


def main() -> None:
    app = ManagerTUI()
    app.run()


if __name__ == "__main__":
    main()
