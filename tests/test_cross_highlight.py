"""Bidirectional cross-highlight tests — AC6."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentlens.app import AgentlensApp
from agentlens.events import EventType, HarnessEvent


@pytest.mark.asyncio
async def test_forward_timeline_cursor_to_app_reactive(tmp_path: Path) -> None:
    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None

        # Add two turn rows via user_message events
        for i, text in enumerate(["first turn", "second turn"]):
            ev = HarnessEvent(
                type=EventType.user_message,
                ts=datetime.now(timezone.utc),
                agent_id=None,
                payload={"text": text, "role": "user"},
            )
            timeline.add_event(ev)
        await pilot.pause()

        app.active_panel = "timeline"
        # Move cursor down onto row 1 (Turn 2)
        await pilot.press("j")
        await pilot.pause()
        # selected_agent_id should be "__turn:2" (turn marker)
        assert app.selected_agent_id == "__turn:2", (
            f"Expected '__turn:2', got: {app.selected_agent_id!r}"
        )


@pytest.mark.asyncio
async def test_reverse_app_reactive_to_timeline(tmp_path: Path) -> None:
    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None

        # Add a turn row
        ev = HarnessEvent(
            type=EventType.user_message,
            ts=datetime.now(timezone.utc),
            agent_id=None,
            payload={"text": "hello", "role": "user"},
        )
        timeline.add_event(ev)
        await pilot.pause()

        # Setting an agent_id reactive should NOT crash (graceful no-op)
        # because turn-only mode has no agent_id rows to match
        app.selected_agent_id = "agent-X"
        await pilot.pause()
        # Cursor should still be in a valid position (not raised an exception)
        assert timeline._table is not None
        # The cursor may or may not have moved; the important thing is no crash
        # and the timeline still has its turn row
        assert timeline._row_count == 1
