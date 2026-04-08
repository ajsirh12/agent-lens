"""Integration tests for FlowchartPanel inside the Textual app."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness_visual.app import HarnessVisualApp
from harness_visual.events import EventType, HarnessEvent


def _task(subagent: str, tid: str = "t1") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Task",
            "tool_use_id": tid,
            "input": {"subagent_type": subagent},
        },
    )


@pytest.mark.asyncio
async def test_flowchart_mounts_with_root_only(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        flowchart = app._flowchart
        assert flowchart is not None
        # Root only.
        assert flowchart.get_node_count() == 1
        assert flowchart.get_edge_count() == 0


@pytest.mark.asyncio
async def test_flowchart_task_event_increases_node_count(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        flowchart = app._flowchart
        assert flowchart is not None
        before = flowchart.get_node_count()
        flowchart.add_event(_task("planner", tid="t1"))
        await pilot.pause()
        assert flowchart.get_node_count() == before + 1
        assert flowchart.get_edge_count() == 1


@pytest.mark.asyncio
async def test_flowchart_cross_highlight_no_error(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        flowchart = app._flowchart
        assert flowchart is not None
        flowchart.add_event(_task("planner", tid="t1"))
        await pilot.pause()
        # Should not error.
        app.selected_agent_id = "agent:planner"
        await pilot.pause()
        assert app.selected_agent_id == "agent:planner"


def _result(tid: str) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_use_id": tid, "is_error": False},
    )


def _user_message() -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": "next"},
    )


@pytest.mark.asyncio
async def test_flowchart_default_orientation_is_leftright(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._flowchart is not None
        assert app._flowchart.get_orientation() == "leftright"
        assert app._flowchart.get_mode() == "all"


@pytest.mark.asyncio
async def test_flowchart_toggle_orientation_via_action(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._flowchart is not None
        start = app._flowchart.get_orientation()
        await pilot.press("o")
        await pilot.pause()
        assert app._flowchart.get_orientation() != start
        await pilot.press("o")
        await pilot.pause()
        assert app._flowchart.get_orientation() == start


@pytest.mark.asyncio
async def test_flowchart_toggle_mode_filters_done_nodes(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None
        # Two agents: runner still going, finisher already done — both
        # spawned within the current turn.
        fc.add_event(_task("runner", tid="t1"))
        fc.add_event(_task("finisher", tid="t2"))
        fc.add_event(_result("t2"))
        await pilot.pause()

        # Mode=all: both visible.
        assert fc.get_mode() == "all"
        assert "agent:runner" in fc._layout.nodes
        assert "agent:finisher" in fc._layout.nodes

        # Toggle to running-only: both still visible because finisher is
        # still in the current turn (sticky running until next user_message).
        await pilot.press("m")
        await pilot.pause()
        assert fc.get_mode() == "running"
        assert "agent:runner" in fc._layout.nodes
        assert "agent:finisher" in fc._layout.nodes

        # Flush the turn with a user_message event. Now finisher drops
        # out of the running-only view (it's truly done and no longer in
        # the current turn), while runner stays.
        fc.add_event(_user_message())
        await pilot.pause()
        assert "agent:runner" in fc._layout.nodes
        assert "agent:finisher" not in fc._layout.nodes

        # Toggle back to all: finisher returns.
        await pilot.press("m")
        await pilot.pause()
        assert fc.get_mode() == "all"
        assert "agent:finisher" in fc._layout.nodes


@pytest.mark.asyncio
async def test_flowchart_is_scrollable_container(tmp_path: Path) -> None:
    from textual.containers import ScrollableContainer
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app._flowchart, ScrollableContainer)


@pytest.mark.asyncio
async def test_flowchart_scroll_actions_do_not_error(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        # Populate enough nodes that the layout exceeds the small viewport.
        for i in range(20):
            app._flowchart.add_event(_task(f"agent_{i}", tid=f"t{i}"))
        await pilot.pause()
        # Fire all scroll actions — none should raise.
        await pilot.press("shift+h")
        await pilot.press("shift+l")
        await pilot.press("pageup")
        await pilot.press("pagedown")
        await pilot.press("home")
        await pilot.press("end")
        await pilot.pause()
        # App still alive and flowchart still populated.
        assert app._flowchart.get_node_count() == 21  # root + 20


@pytest.mark.asyncio
async def test_toggle_pane_layout_applies_vpanes_class(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        main = app.query_one("#main")
        assert "vpanes" not in main.classes
        # Press p → vertical
        await pilot.press("p")
        await pilot.pause()
        assert "vpanes" in main.classes
        # Press p → horizontal again
        await pilot.press("p")
        await pilot.pause()
        assert "vpanes" not in main.classes
