from __future__ import annotations

import json
from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Static


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
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
