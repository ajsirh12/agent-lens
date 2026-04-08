"""SubagentDetailScreen — modal listing a subagent's internal tool calls.

Rendered when the user presses ``d`` on an agent node in the flowchart
that has been linked to a subagent JSONL file. Events are pre-prepared
by the caller as a list of dicts with keys: ``ts`` (datetime),
``tool_name`` (str), ``input_summary`` (str), ``status`` (str).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static


class SubagentDetailScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]

    _EMPTY_PLACEHOLDER = "No tool calls recorded for this subagent yet."

    def __init__(self, node_label: str, events: list[dict[str, Any]]) -> None:
        super().__init__()
        self.node_label = node_label
        self.events = events
        # Exposed for tests and debuggability — mirrors the placeholder
        # text rendered in the empty state, or None otherwise.
        self._empty_placeholder: str | None = (
            self._EMPTY_PLACEHOLDER if not events else None
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="subagent-detail-body"):
            yield Static(f"Subagent: {self.node_label}", id="subagent-detail-title")
            if not self.events:
                yield Static(
                    self._EMPTY_PLACEHOLDER,
                    classes="placeholder",
                )
            else:
                with ScrollableContainer(id="subagent-detail-scroll"):
                    table: DataTable[str] = DataTable(
                        id="subagent-detail-table",
                        zebra_stripes=True,
                    )
                    table.add_columns("time", "tool", "input", "status")
                    for ev in self.events:
                        ts = ev.get("ts")
                        if isinstance(ts, datetime):
                            ts_str = ts.strftime("%H:%M:%S")
                        else:
                            ts_str = str(ts or "")
                        tool = str(ev.get("tool_name") or "")
                        preview = str(ev.get("input_summary") or "")[:40]
                        status = str(ev.get("status") or "")
                        table.add_row(ts_str, tool, preview, status)
                    yield table
            yield Static("(Esc / q to close)", classes="placeholder")

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)
