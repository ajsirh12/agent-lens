"""Tests for TimelinePanel: sanitization, turn-only mode."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentlens.events import EventType, HarnessEvent


def _make_event(
    tool_name: str = "Bash",
    tool_use_id: str = "tid1",
    agent_id: str | None = None,
    inp: object = None,
    ts: datetime | None = None,
) -> HarnessEvent:
    payload: dict = {"tool_name": tool_name, "tool_use_id": tool_use_id}
    if inp is not None:
        payload["input"] = inp
    return HarnessEvent(
        type=EventType.tool_use,
        ts=ts or datetime.now(timezone.utc),
        agent_id=agent_id,
        payload=payload,
    )


def _make_result_event(
    tool_use_id: str = "tid1",
    is_error: bool = False,
    agent_id: str | None = None,
    ts: datetime | None = None,
) -> HarnessEvent:
    payload: dict = {"tool_use_id": tool_use_id, "is_error": is_error}
    return HarnessEvent(
        type=EventType.tool_result,
        ts=ts or datetime.now(timezone.utc),
        agent_id=agent_id,
        payload=payload,
    )


def _make_user_message_event(
    text: str = "hello",
    ts: datetime | None = None,
) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=ts or datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": text, "role": "user"},
    )


@pytest.mark.asyncio
async def test_timeline_sanitizes_ansi_escape_in_cells(tmp_path: Path) -> None:
    """ANSI escape sequences in user prompt must be stripped from the turn row prompt column."""
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

        # user_message event with ANSI in text
        ev = HarnessEvent(
            type=EventType.user_message,
            ts=datetime.now(timezone.utc),
            agent_id=None,
            payload={"text": "\x1b[31mbad prompt\x1b[0m", "role": "user"},
        )
        timeline.add_event(ev)
        await pilot.pause()

        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        assert len(rows) >= 1, "Expected at least one turn row"
        row_key = rows[-1]
        # prompt is column index 2 in turn-only mode (ts, turn, prompt, tools, dur)
        prompt_cell = str(timeline._table.get_cell_at((timeline._row_index(row_key), 2)))
        assert "\x1b" not in prompt_cell, f"ANSI escape found in cell: {prompt_cell!r}"
        assert "bad prompt" in prompt_cell



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
            agent_id="main",
            ts=0.0,
            status="ok",
            duration_ms=42,
            input_summary="\rfoo",
            input_raw=None,
            output_preview=None,
            is_error=False,
            tool_use_id=None,
        )

        await app.push_screen(modal)
        await pilot.pause()

        # Inspect Static widgets inside the modal
        for widget in modal.query("Static"):
            rendered = str(widget.content)
            assert "\x1b" not in rendered, f"ANSI escape in rendered text: {rendered!r}"
            assert "\r" not in rendered, f"CR in rendered text: {rendered!r}"


@pytest.mark.asyncio
async def test_tool_use_event_does_not_add_row(tmp_path: Path) -> None:
    """tool_use event must not add any row in turn-only mode."""
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
        ev = _make_event(tool_name="Bash", tool_use_id="tid-no-row")
        timeline.add_event(ev)
        await pilot.pause()
        assert timeline._row_count == 0, f"Expected 0 rows, got {timeline._row_count}"


@pytest.mark.asyncio
async def test_tool_result_event_does_not_add_row(tmp_path: Path) -> None:
    """tool_result event must not add any row in turn-only mode."""
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
        result_ev = _make_result_event(tool_use_id="tid-no-row-result")
        timeline.add_event(result_ev)
        await pilot.pause()
        assert timeline._row_count == 0, f"Expected 0 rows, got {timeline._row_count}"


@pytest.mark.asyncio
async def test_turn_row_has_correct_columns(tmp_path: Path) -> None:
    """Turn row must have 5 columns: (ts, 'Turn N', prompt_preview, '0', 'LIVE')."""
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
        ev = _make_user_message_event(text="hello world")
        timeline.add_event(ev)
        await pilot.pause()
        assert timeline._table is not None
        assert timeline._row_count == 1
        rows = list(timeline._table.rows.keys())
        row_key = rows[0]
        idx = timeline._row_index(row_key)
        turn_cell = str(timeline._table.get_cell_at((idx, 1)))
        prompt_cell = str(timeline._table.get_cell_at((idx, 2)))
        tools_cell = str(timeline._table.get_cell_at((idx, 3)))
        dur_cell = str(timeline._table.get_cell_at((idx, 4)))
        assert turn_cell == "Turn 1", f"turn col: {turn_cell!r}"
        assert "hello world" in prompt_cell, f"prompt col: {prompt_cell!r}"
        assert tools_cell == "0", f"tools col: {tools_cell!r}"
        assert dur_cell == "LIVE", f"dur col: {dur_cell!r}"


@pytest.mark.asyncio
async def test_tool_use_increments_turn_tool_count(tmp_path: Path) -> None:
    """tool_use events after a turn row must increment the 'tools' cell count."""
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
        # Add a turn row first
        timeline.add_event(_make_user_message_event(text="start"))
        # Add 3 tool_use events
        for i in range(3):
            timeline.add_event(_make_event(tool_name="Bash", tool_use_id=f"tid-count-{i}"))
        await pilot.pause()
        assert timeline._table is not None
        # Only 1 turn row should exist
        assert timeline._row_count == 1
        rows = list(timeline._table.rows.keys())
        row_key = rows[0]
        idx = timeline._row_index(row_key)
        tools_cell = str(timeline._table.get_cell_at((idx, 3)))
        assert tools_cell == "3", f"Expected tools='3', got: {tools_cell!r}"


@pytest.mark.asyncio
async def test_turn_duration_computed_on_next_turn(tmp_path: Path) -> None:
    """When turn 2 starts, turn 1's 'dur' cell should be updated to NmSSs format."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    t1 = datetime(2025, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 1, 15, 14, 1, 35, tzinfo=timezone.utc)  # 95 seconds later
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        timeline.add_event(_make_user_message_event(text="turn one", ts=t1))
        timeline.add_event(_make_user_message_event(text="turn two", ts=t2))
        await pilot.pause()
        assert timeline._table is not None
        assert timeline._row_count == 2
        rows = list(timeline._table.rows.keys())
        turn1_key = rows[0]
        idx = timeline._row_index(turn1_key)
        dur_cell = str(timeline._table.get_cell_at((idx, 4)))
        # 95 seconds = 1m35s
        assert dur_cell == "1m35s", f"Expected '1m35s', got: {dur_cell!r}"
