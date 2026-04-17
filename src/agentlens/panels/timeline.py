"""TimelinePanel — DataTable of turn markers."""

from __future__ import annotations

import logging
from typing import Any, Literal

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import DataTable, Static

from ..events import EventType, HarnessEvent
from ..graph_model import _is_real_user_prompt
from ..messages import HarnessEventMessage

log = logging.getLogger(__name__)


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
        self._row_agent: dict[Any, str | None] = {}  # row_key -> agent_id
        self._turn_tool_count: dict[int, int] = {}   # turn_num -> running tool count
        self._turn_start_ts: dict[int, float] = {}   # turn_num -> epoch timestamp
        self._turn_row_key: dict[int, Any] = {}       # turn_num -> DataTable row_key
        self._updating = False
        self._row_count = 0
        self.max_rows = max_rows
        # Follow-bottom is a Textual refresh-coalesced operation: many
        # add_events in one frame result in a single cursor move at the
        # next refresh tick instead of 500 redundant move_cursor calls.
        self._scroll_pending = False
        self._turn_counter: int = 0
        # When the timeline itself changes selected_agent_id (because the
        # user highlighted a row), we must NOT let _on_app_agent_changed
        # jump the cursor to the most-recent row for that agent — that
        # would fight the user's manual scroll. This flag persists across
        # the async watcher boundary.
        self._highlight_from_timeline: bool = False

    def compose(self) -> ComposeResult:
        self._placeholder = Static("waiting for events…", classes="placeholder")
        yield self._placeholder
        table: DataTable[Any] = DataTable(id="timeline-table")
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.can_focus = False
        self._table = table
        yield table

    def on_mount(self) -> None:
        assert self._table is not None
        self._table.add_columns("ts", "turn", "prompt", "tools", "dur")
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
        # Skip subagent-internal events — they come from separate JSONL
        # files (via SubagentWatcher) and arrive out of chronological
        # order relative to the main session, breaking the timeline's
        # time-sorted appearance. Subagent tool calls are already visible
        # in the Flowchart breakdown badges and the drill-down modal.
        if ev.payload.get("subagent_uuid"):
            return
        # Capture "follow mode" BEFORE we mutate the table. If the user
        # was already looking at the bottom row (the live tail) we want
        # the new row to be the new bottom and the cursor to stick to
        # it. If the user has scrolled up to inspect an older event we
        # leave them alone.
        was_at_bottom = self._was_at_bottom()
        if ev.type == EventType.tool_use:
            # No row added. Increment tool count for current turn.
            if self._turn_counter > 0:
                turn_num = self._turn_counter
                self._turn_tool_count[turn_num] = self._turn_tool_count.get(turn_num, 0) + 1
                rk = self._turn_row_key.get(turn_num)
                if rk is not None:
                    try:
                        idx = self._row_index(rk)
                        self._table.update_cell_at((idx, 3), str(self._turn_tool_count[turn_num]))
                    except Exception:
                        pass
        elif ev.type == EventType.tool_result:
            pass  # No rows in turn-only mode.
        elif ev.type == EventType.user_message:
            # Must match graph_model's turn boundary criteria exactly:
            # skip subagent user rows AND system-injected messages.
            if ev.payload.get("subagent_uuid"):
                return
            if _is_real_user_prompt(ev):
                self._turn_counter += 1
                turn_num = self._turn_counter
                prompt = str(ev.payload.get("text", ""))[:40]
                ts_str = ev.ts.strftime("%H:%M:%S")
                ts_epoch = ev.ts.timestamp()

                # Finalize previous turn's duration
                if turn_num > 1:
                    prev = turn_num - 1
                    prev_rk = self._turn_row_key.get(prev)
                    prev_start = self._turn_start_ts.get(prev)
                    if prev_rk is not None and prev_start is not None:
                        dur_secs = max(0, int(ts_epoch - prev_start))
                        dur_str = f"{dur_secs // 60}m{dur_secs % 60:02d}s"
                        try:
                            idx = self._row_index(prev_rk)
                            self._table.update_cell_at((idx, 4), dur_str)
                        except Exception:
                            pass

                row_key = self._table.add_row(
                    ts_str,
                    f"Turn {turn_num}",
                    _sanitize_cell(prompt),
                    "0",
                    "LIVE",
                )
                self._row_count += 1
                self._row_agent[row_key] = f"__turn:{turn_num}"
                self._turn_tool_count[turn_num] = 0
                self._turn_start_ts[turn_num] = ts_epoch
                self._turn_row_key[turn_num] = row_key
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

        Re-checks cursor position at callback time: if the user has
        scrolled more than one row away from the tail since the callback
        was scheduled, their intent takes priority and we skip the jump.
        """
        self._scroll_pending = False
        if self._table is None:
            return
        total = self._row_count
        if total == 0:
            return
        # Guard against overriding a manual scroll. If the user moved the
        # cursor more than 1 row above the last row between when
        # _scroll_to_end() was called and now, skip the jump.
        cursor = self._table.cursor_row
        if cursor is not None and cursor >= 0 and cursor < total - 1:
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
        # Mark that this agent change originated from the timeline so that
        # _on_app_agent_changed does NOT jump the cursor back to the
        # most-recent row — that would fight the user's manual scroll.
        self._highlight_from_timeline = True
        try:
            self.app.selected_agent_id = aid  # type: ignore[attr-defined]
            # In turn-only mode every row is a turn marker ("__turn:N").
            # Propagate the turn index to the flowchart so it filters to
            # that turn — same effect as pressing [ / ].
            if isinstance(aid, str) and aid.startswith("__turn:"):
                try:
                    turn_num = int(aid.split(":", 1)[1])
                    self.app._active_turn = turn_num - 1  # type: ignore[attr-defined]
                    self.app._propagate_turn_to_flowchart()  # type: ignore[attr-defined]
                    self.app._update_footer()  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception:
            self._highlight_from_timeline = False

    def _on_app_agent_changed(self, new_value: str | None) -> None:
        # If the change was initiated by the timeline itself (user scrolled),
        # skip the reverse cursor-jump — the flowchart will still highlight
        # the matching node, but the timeline stays where the user put it.
        if self._highlight_from_timeline:
            self._highlight_from_timeline = False
            return
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

    # --- click handling --------------------------------------------------

    def on_click(self, event: Any) -> None:  # noqa: ANN001
        """Switch app active_panel to 'timeline' when this panel is clicked.

        DataTable.can_focus is False, so clicks bubble up to this Container
        via Textual's event bubbling. try/except guards against app context
        being absent (e.g. standalone test instantiation).
        """
        try:
            self.app.active_panel = "timeline"  # type: ignore[attr-defined]
        except Exception:
            pass

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

    def get_selected_turn_index(self) -> int | None:
        """Return the turn index if the current row is a turn marker, else None.

        Turn markers carry ``_row_agent[row_key] == "__turn:<N>"`` where
        N is 1-indexed (human display). This method converts back to the
        0-indexed ``turn_index`` used by graph_model/FlowRecord.

        Uses the DataTable's row_key lookup (not dict order) so it is
        robust against row deletions from FIFO cap eviction.
        """
        if self._table is None:
            return None
        try:
            cursor_row = self._table.cursor_row
            # Find the row_key whose display index equals cursor_row.
            # _row_agent keys are a superset of turn marker rows — scan
            # only those (fast because turn markers are rare).
            for rk, val in self._row_agent.items():
                if not isinstance(val, str) or not val.startswith("__turn:"):
                    continue
                try:
                    idx = self._table.get_row_index(rk)
                except Exception:
                    continue
                if idx == cursor_row:
                    try:
                        human_num = int(val.split(":", 1)[1])
                        return human_num - 1  # 1-indexed → 0-indexed
                    except ValueError:
                        return None
            return None
        except Exception:
            return None

    def clear(self) -> None:
        """Reset the timeline to an empty state. Safe to call pre-mount."""
        self._row_agent = {}
        self._turn_tool_count = {}
        self._turn_start_ts = {}
        self._turn_row_key = {}
        self._updating = False
        self._row_count = 0
        self._scroll_pending = False
        self._turn_counter = 0
        self._highlight_from_timeline = False
        if self._table is None:
            return
        try:
            self._table.clear()
        except Exception:
            pass
        if self._placeholder is not None:
            self._placeholder.display = True

    def scroll_to_turn(self, turn_num: int) -> None:
        """Scroll the timeline to the row for the given turn marker.

        Searches for a row whose ``_row_agent`` is ``__turn:<num>``
        and moves the cursor there. Called by app's turn navigation
        actions so pressing ``[`` / ``]`` visually jumps to the turn.
        """
        if self._table is None:
            return
        target_key = None
        marker = f"__turn:{turn_num}"
        for rk, agent_val in self._row_agent.items():
            if agent_val == marker:
                target_key = rk
                break
        if target_key is None:
            return
        self._updating = True
        try:
            idx = self._row_index(target_key)
            self._table.move_cursor(row=idx, animate=False)
        except Exception:
            pass
        finally:
            self._updating = False

    def scroll_to_end_live(self) -> None:
        """Scroll to the last row (for LIVE mode return)."""
        self._scroll_to_end()

