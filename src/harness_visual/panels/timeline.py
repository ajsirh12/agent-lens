"""TimelinePanel — DataTable of tool_use / tool_result events."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import DataTable, Static

from ..events import EventType, HarnessEvent
from ..messages import HarnessEventMessage


class TimelinePanel(Container):
    """Two-column panel wrapping a DataTable and an empty-state placeholder."""

    DEFAULT_CSS = ""

    def __init__(self, *, max_rows: int = 2000, id: str | None = None) -> None:
        super().__init__(id=id)
        self._table: DataTable[Any] | None = None
        self._placeholder: Static | None = None
        self._pending_use: dict[str, float] = {}  # tool_use_id -> ts_epoch
        self._row_agent: dict[Any, str | None] = {}  # row_key -> agent_id
        self._row_message: dict[Any, str | None] = {}
        self._tool_use_row: dict[str, Any] = {}  # tool_use_id -> row_key
        self._updating = False
        self._row_count = 0
        self.max_rows = max_rows

    def compose(self) -> ComposeResult:
        self._placeholder = Static("waiting for events…", classes="placeholder")
        yield self._placeholder
        table: DataTable[Any] = DataTable(id="timeline-table")
        table.cursor_type = "row"
        table.zebra_stripes = True
        self._table = table
        yield table

    def on_mount(self) -> None:
        assert self._table is not None
        self._table.add_columns("ts", "tool", "agent", "status", "dur_ms")
        # Watch the app reactive for reverse cross-highlight.
        try:
            self.watch(self.app, "selected_agent_id", self._on_app_agent_changed)
        except Exception:
            pass

    # --- event ingestion -------------------------------------------------

    def on_harness_event_message(self, message: HarnessEventMessage) -> None:
        ev = message.event
        self.add_event(ev)

    def add_event(self, ev: HarnessEvent) -> None:
        """Public entrypoint (also used by tests) to append a row."""
        if self._table is None:
            return
        if ev.type == EventType.tool_use:
            tid = ev.tool_use_id or ""
            ts_str = ev.ts.strftime("%H:%M:%S")
            row_key = self._table.add_row(
                ts_str,
                ev.tool_name or "?",
                (ev.agent_id or "-")[:20],
                "running",
                "-",
            )
            self._row_count += 1
            self._row_agent[row_key] = ev.agent_id
            self._row_message[row_key] = ev.message_id
            if tid:
                self._tool_use_row[tid] = row_key
                self._pending_use[tid] = ev.ts.timestamp()
            self._hide_placeholder()
            self._enforce_cap()
        elif ev.type == EventType.tool_result:
            tid = ev.tool_use_id or ""
            if tid and tid in self._tool_use_row:
                row_key = self._tool_use_row[tid]
                try:
                    started = self._pending_use.pop(tid, None)
                    dur_ms = "-"
                    if started is not None:
                        dur_ms = str(max(0, int((ev.ts.timestamp() - started) * 1000)))
                    status = "err" if ev.is_error else "ok"
                    self._table.update_cell_at((self._row_index(row_key), 3), status)
                    self._table.update_cell_at((self._row_index(row_key), 4), dur_ms)
                except Exception:
                    pass
            else:
                # Orphan result — still surface it as a row.
                ts_str = ev.ts.strftime("%H:%M:%S")
                self._table.add_row(
                    ts_str,
                    "result",
                    (ev.agent_id or "-")[:20],
                    "err" if ev.is_error else "ok",
                    "-",
                )
                self._row_count += 1
                self._hide_placeholder()
                self._enforce_cap()

    def _row_index(self, row_key: Any) -> int:
        assert self._table is not None
        # DataTable.get_row_index is the supported lookup
        try:
            return self._table.get_row_index(row_key)
        except Exception:
            return 0

    def _enforce_cap(self) -> None:
        if self._table is None:
            return
        while self._row_count > self.max_rows:
            try:
                first_key = self._table.rows.__iter__().__next__()
                self._table.remove_row(first_key)
                self._row_count -= 1
                self._row_agent.pop(first_key, None)
                self._row_message.pop(first_key, None)
            except Exception:
                break

    def _hide_placeholder(self) -> None:
        if self._placeholder is not None and self._placeholder.display:
            self._placeholder.display = False

    # --- cross-highlight -------------------------------------------------

    def on_data_table_row_highlighted(self, event: Any) -> None:
        if self._updating or self._table is None:
            return
        row_key = getattr(event, "row_key", None)
        if row_key is None:
            return
        aid = self._row_agent.get(row_key)
        if aid is None:
            return
        try:
            self.app.selected_agent_id = aid  # type: ignore[attr-defined]
        except Exception:
            pass

    def _on_app_agent_changed(self, new_value: str | None) -> None:
        if self._updating or self._table is None or new_value is None:
            return
        # Find the most recent row with matching agent_id.
        target_key = None
        for rk, aid in reversed(list(self._row_agent.items())):
            if aid == new_value:
                target_key = rk
                break
        if target_key is None:
            return
        self._updating = True
        try:
            idx = self._row_index(target_key)
            self._table.move_cursor(row=idx)
        except Exception:
            pass
        finally:
            self._updating = False
