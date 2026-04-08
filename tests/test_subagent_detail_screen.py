"""Tests for SubagentDetailScreen modal."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness_visual.app import HarnessVisualApp
from harness_visual.panels.subagent_detail import SubagentDetailScreen


@pytest.mark.asyncio
async def test_subagent_detail_screen_with_events(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state",
        no_attach=True,
    )
    (tmp_path / "empty.jsonl").write_text("")
    events = [
        {
            "ts": datetime(2026, 4, 8, 10, 15, 30, tzinfo=timezone.utc),
            "tool_name": "Read",
            "input_summary": "/tmp/file.py",
            "status": "done",
        },
        {
            "ts": datetime(2026, 4, 8, 10, 15, 31, tzinfo=timezone.utc),
            "tool_name": "Edit",
            "input_summary": "/tmp/file.py (old→new)",
            "status": "done",
        },
    ]
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SubagentDetailScreen("executor", events)
        await app.push_screen(screen)
        await pilot.pause()
        # The screen mounted: the title static is present on the screen.
        titles = screen.query("#subagent-detail-title")
        assert len(titles) == 1
        tables = screen.query("#subagent-detail-table")
        assert len(tables) == 1


@pytest.mark.asyncio
async def test_subagent_detail_screen_empty(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state",
        no_attach=True,
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SubagentDetailScreen("executor", [])
        await app.push_screen(screen)
        await pilot.pause()
        # Placeholder text "No tool calls recorded ..." appears on the
        # empty screen. Check every Static widget's renderable.
        # The empty-state path should have stored its marker on the
        # screen itself — the screen exposes ``events == []`` which is
        # the behavior we actually care about. Also assert that the
        # empty-state placeholder widget was mounted.
        assert screen.events == []
        assert screen._empty_placeholder is not None
        assert "No tool calls" in screen._empty_placeholder
