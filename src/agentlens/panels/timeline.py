"""TimelinePanel — DataTable of tool_use / tool_result events."""

from __future__ import annotations

import logging
from typing import Any, Literal

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import DataTable, Static

from ..events import EventType, HarnessEvent
from ..messages import HarnessEventMessage

log = logging.getLogger(__name__)

MAX_PENDING = 2000


def _sanitize_cell(s: object) -> str:
    """Strip non-printable / ANSI-escape characters and cap length."""
    text = str(s)
    text = "".join(c for c in text if (c.isprintable() or c == "\t") and c not in "\x1b\r")
    return text[:500]


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
        self._row_input: dict[Any, str] = {}  # row_key -> input preview
        self._updating = False
        self._row_count = 0
        self.max_rows = max_rows
        # Follow-bottom is a Textual refresh-coalesced operation: many
        # add_events in one frame result in a single cursor move at the
        # next refresh tick instead of 500 redundant move_cursor calls.
        self._scroll_pending = False

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
        # Capture "follow mode" BEFORE we mutate the table. If the user
        # was already looking at the bottom row (the live tail) we want
        # the new row to be the new bottom and the cursor to stick to
        # it. If the user has scrolled up to inspect an older event we
        # leave them alone.
        was_at_bottom = self._was_at_bottom()
        if ev.type == EventType.tool_use:
            tid = ev.tool_use_id or ""
            ts_str = ev.ts.strftime("%H:%M:%S")
            row_key = self._table.add_row(
                ts_str,
                _sanitize_cell(ev.tool_name or "?"),
                _sanitize_cell((ev.agent_id or "-")[:20]),
                "running",
                "-",
            )
            self._row_count += 1
            self._row_agent[row_key] = ev.agent_id
            self._row_message[row_key] = ev.message_id
            if tid:
                self._tool_use_row[tid] = row_key
                if len(self._tool_use_row) > MAX_PENDING:
                    oldest_key = next(iter(self._tool_use_row))
                    del self._tool_use_row[oldest_key]
                self._pending_use[tid] = ev.ts.timestamp()
                # Evict oldest if cap exceeded.
                if len(self._pending_use) > MAX_PENDING:
                    oldest_key = next(iter(self._pending_use))
                    del self._pending_use[oldest_key]
                    log.debug("pending_use cap hit: evicting %s", oldest_key)
            # Store input preview for modal.
            inp = ev.payload.get("input")
            input_preview = ""
            if isinstance(inp, dict):
                for key in ("command", "path", "file_path", "pattern", "description", "subagent_type", "skill"):
                    v = inp.get(key)
                    if isinstance(v, str) and v:
                        input_preview = v
                        break
                if not input_preview:
                    input_preview = str(inp)[:120]
            elif inp is not None:
                input_preview = str(inp)[:120]
            self._row_input[row_key] = input_preview
            # Evict oldest _row_input if cap exceeded.
            if len(self._row_input) > MAX_PENDING:
                oldest_key = next(iter(self._row_input))
                del self._row_input[oldest_key]
            self._hide_placeholder()
            self._enforce_cap()
            if was_at_bottom:
                self._scroll_to_end()
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
                if was_at_bottom:
                    self._scroll_to_end()

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

    # --- follow-the-bottom (tail -f style) ------------------------------

    def _was_at_bottom(self) -> bool:
        """True when the cursor is on the last row (or the table is
        empty / cursor-less). Call BEFORE mutating rows.

        Uses the cached ``_row_count`` (O(1)) instead of materializing
        the DataTable's row dict, so this is cheap to call on every
        add_event even in bursty ingestion scenarios.
        """
        if self._table is None:
            return True
        total = self._row_count
        if total == 0:
            return True
        cursor = self._table.cursor_row
        if cursor is None or cursor < 0:
            return True
        return cursor >= total - 1

    def _scroll_to_end(self) -> None:
        """Schedule a cursor-pull to the last row on the next refresh.

        Many add_events within a single frame (bulk ingestion, tests,
        catch-up at startup) would otherwise trigger N redundant
        move_cursor calls. We coalesce them into a single
        ``call_after_refresh`` callback via the ``_scroll_pending``
        flag — the first call arms it, subsequent calls in the same
        frame are no-ops, and the deferred handler runs once after
        the DataTable has settled.
        """
        if self._table is None or self._scroll_pending:
            return
        self._scroll_pending = True
        try:
            self.call_after_refresh(self._do_scroll_to_end)
        except Exception:
            # If call_after_refresh isn't available (pre-mount, test
            # shims, etc.), fall back to running the move synchronously.
            self._scroll_pending = False
            self._do_scroll_to_end()

    def _do_scroll_to_end(self) -> None:
        """Deferred callback that actually moves the cursor. Guarded
        with ``_updating`` so the resulting row-highlighted event does
        not recurse into the cross-highlight path.
        """
        self._scroll_pending = False
        if self._table is None:
            return
        total = self._row_count
        if total == 0:
            return
        self._updating = True
        try:
            try:
                self._table.move_cursor(row=total - 1, animate=False)
            except Exception:
                pass
        finally:
            self._updating = False

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

    # --- public API (used by app.py) -------------------------------------

    def move_cursor(self, direction: Literal["up", "down"]) -> None:
        """Move the DataTable cursor up or down one row."""
        if self._table is None:
            return
        try:
            if direction == "down":
                self._table.action_cursor_down()
            else:
                self._table.action_cursor_up()
        except Exception:
            pass

    def get_selected_row_cells(self) -> list[str] | None:
        """Return the 5 cell values for the currently-selected row, or None."""
        if self._table is None:
            return None
        try:
            row = self._table.cursor_row
            return [_sanitize_cell(self._table.get_cell_at((row, c))) for c in range(5)]
        except Exception:
            return None

    def clear(self) -> None:
        """Reset the timeline to an empty state. Safe to call pre-mount."""
        self._pending_use = {}
        self._tool_use_row = {}
        self._row_input = {}
        self._row_agent = {}
        self._row_message = {}
        self._updating = False
        self._row_count = 0
        self._scroll_pending = False
        if self._table is None:
            return
        try:
            self._table.clear()
        except Exception:
            pass
        if self._placeholder is not None:
            self._placeholder.display = True

    def get_selected_input_summary(self) -> str:
        """Return the stored input preview for the currently-selected row, or ''."""
        if self._table is None:
            return ""
        try:
            row = self._table.cursor_row
            # Find which row_key corresponds to the cursor row index.
            keys = list(self._table.rows.keys())
            if row < 0 or row >= len(keys):
                return ""
            row_key = keys[row]
            return self._row_input.get(row_key, "")
        except Exception:
            return ""
