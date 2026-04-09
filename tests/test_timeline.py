"""Tests for TimelinePanel: sanitization, pending_use cap, input_summary."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentlens.events import EventType, HarnessEvent
from agentlens.panels.timeline import MAX_PENDING, TimelinePanel


def _make_event(
    tool_name: str = "Bash",
    tool_use_id: str = "tid1",
    agent_id: str | None = None,
    inp: object = None,
) -> HarnessEvent:
    payload: dict = {"tool_name": tool_name, "tool_use_id": tool_use_id}
    if inp is not None:
        payload["input"] = inp
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=agent_id,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_timeline_sanitizes_ansi_escape_in_cells(tmp_path: Path) -> None:
    """ANSI escape sequences in tool_name must be stripped from DataTable cells."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        ev = _make_event(tool_name="\x1b[31mbad\x1b[0m", tool_use_id="tid-ansi")
        timeline.add_event(ev)
        await pilot.pause()
        # Inspect via public method
        # Move cursor to the row and check cells
        assert timeline._table is not None
        cells = timeline.get_selected_row_cells()
        # cells could be None if cursor not on our row yet; check the row directly
        # via _table rows
        rows = list(timeline._table.rows.keys())
        assert len(rows) >= 1
        row_key = rows[-1]
        row_cells = [timeline._table.get_cell_at((timeline._row_index(row_key), c)) for c in range(5)]
        tool_cell = str(row_cells[1])
        assert "\x1b" not in tool_cell, f"ANSI escape found in cell: {tool_cell!r}"
        assert "bad" in tool_cell  # content preserved without escape codes


@pytest.mark.asyncio
async def test_timeline_pending_use_cap_evicts_oldest(tmp_path: Path) -> None:
    """Feeding 2001 tool_use events should keep _pending_use at <= MAX_PENDING."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        for i in range(MAX_PENDING + 1):
            ev = _make_event(tool_name="Bash", tool_use_id=f"tid-{i}")
            timeline.add_event(ev)
        assert len(timeline._pending_use) <= MAX_PENDING


@pytest.mark.asyncio
async def test_action_show_detail_passes_populated_input_summary(tmp_path: Path) -> None:
    """action_show_detail must pass a non-empty input_summary when input has a command."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    captured: list = []

    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None

        ev = _make_event(
            tool_name="Bash",
            tool_use_id="tid-input",
            inp={"command": "echo hi"},
        )
        timeline.add_event(ev)
        await pilot.pause()

        # Move cursor to the new row (last row)
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        last_idx = len(rows) - 1
        timeline._table.move_cursor(row=last_idx)
        await pilot.pause()

        # Intercept push_screen
        original_push = app.push_screen

        def _capture_push(screen, *args, **kwargs):
            captured.append(screen)
            return original_push(screen, *args, **kwargs)

        app.push_screen = _capture_push  # type: ignore[method-assign]
        app.action_show_detail()
        await pilot.pause()

    assert len(captured) >= 1, "push_screen was not called"
    modal = captured[0]
    assert "echo hi" in modal.input_summary, (
        f"Expected 'echo hi' in input_summary, got: {modal.input_summary!r}"
    )


@pytest.mark.asyncio
async def test_detail_modal_sanitizes_fields(tmp_path: Path) -> None:
    """ToolDetailScreen must strip ANSI and CR from all fields in rendered output."""
    from agentlens.app import AgentlensApp
    from agentlens.panels.detail_modal import ToolDetailScreen

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()

        modal = ToolDetailScreen(
            tool_name="\x1b[31mbad\x1b[0m",
            input_summary="\rfoo",
            status="ok",
            duration_ms="42",
        )

        await app.push_screen(modal)
        await pilot.pause()

        # Inspect Static widgets inside the modal
        for widget in modal.query("Static"):
            rendered = str(widget.content)
            assert "\x1b" not in rendered, f"ANSI escape in rendered text: {rendered!r}"
            assert "\r" not in rendered, f"CR in rendered text: {rendered!r}"
