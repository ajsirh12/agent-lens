"""Tests for Turn-based Navigation Phase 1."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import CallGraph, TurnRecord, _is_real_user_prompt


def _real_user_message(text: str = "Hello world", ts: datetime | None = None) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=ts or datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": text},
    )


def _system_user_message(text: str = "<task-notification>done</task-notification>") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": text},
    )


def _agent_use(subagent: str, *, tid: str = "t1") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Agent",
            "tool_use_id": tid,
            "input": {"subagent_type": subagent, "description": "desc"},
        },
    )


def _result(tid: str) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_use_id": tid},
    )


# --- 1. test_turn_record_created_on_real_user_message ---

def test_turn_record_created_on_real_user_message() -> None:
    g = CallGraph()
    ev = _real_user_message("Fix the parser bug please")
    g.update_from_event(ev)
    turns = g.get_turns()
    assert len(turns) == 1
    assert turns[0].index == 0
    assert turns[0].prompt_preview == "Fix the parser bug please"
    assert turns[0].end_ts is None
    assert g.get_current_turn_index() == 0


# --- 2. test_turn_boundary_closes_previous_turn ---

def test_turn_boundary_closes_previous_turn() -> None:
    g = CallGraph()
    g.update_from_event(_real_user_message("First prompt"))
    g.update_from_event(_real_user_message("Second prompt"))
    turns = g.get_turns()
    assert len(turns) == 2
    assert turns[0].end_ts is not None
    assert turns[0].end_ts == turns[1].start_ts
    assert turns[1].end_ts is None
    assert g.get_current_turn_index() == 1


# --- 3. test_system_message_does_not_create_turn ---

def test_system_message_does_not_create_turn() -> None:
    g = CallGraph()
    g.update_from_event(_system_user_message())
    turns = g.get_turns()
    assert len(turns) == 0
    assert g.get_current_turn_index() == -1


# --- 4. test_flow_record_gets_turn_index ---

def test_flow_record_gets_turn_index() -> None:
    g = CallGraph()
    g.update_from_event(_real_user_message("Turn zero"))
    g.update_from_event(_agent_use("planner", tid="t1"))
    assert len(g._flow_history) == 1
    assert g._flow_history[0].turn_index == 0

    g.update_from_event(_real_user_message("Turn one"))
    g.update_from_event(_agent_use("executor", tid="t2"))
    assert len(g._flow_history) == 2
    assert g._flow_history[1].turn_index == 1


# --- 5. test_timeline_inserts_turn_marker_row ---

@pytest.mark.asyncio
async def test_timeline_inserts_turn_marker_row(tmp_path: Path) -> None:
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
        ev = _real_user_message("Hello from user")
        timeline.add_event(ev)
        await pilot.pause()
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        assert len(rows) >= 1
        # Check that the marker row contains "Turn" in the tool column
        found_turn_marker = False
        for rk in rows:
            idx = timeline._row_index(rk)
            cell = str(timeline._table.get_cell_at((idx, 1)))
            if "Turn" in cell:
                found_turn_marker = True
                break
        assert found_turn_marker, "No turn marker row found in timeline"
        # Check _row_agent for __turn: prefix
        assert any(
            str(v).startswith("__turn:") for v in timeline._row_agent.values()
        )


# --- 6. test_turn_navigation_keys ---

@pytest.mark.asyncio
async def test_turn_navigation_keys(tmp_path: Path) -> None:
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None
        # Feed 3 real user_messages to create 3 turns
        for i in range(3):
            fc._graph.update_from_event(_real_user_message(f"Prompt {i}"))
        turns = fc._graph.get_turns()
        assert len(turns) == 3

        # Initially LIVE
        assert app._active_turn is None

        # Press [ -> go to second-to-last turn (index 1)
        await pilot.press("[")
        assert app._active_turn == 1

        # Press [ -> go to turn 0
        await pilot.press("[")
        assert app._active_turn == 0

        # Press [ again -> stays at 0 (can't go below)
        await pilot.press("[")
        assert app._active_turn == 0

        # Press ] -> go to turn 1
        await pilot.press("]")
        assert app._active_turn == 1

        # Press ] -> go to turn 2 (last)
        await pilot.press("]")
        assert app._active_turn == 2

        # Press ] -> go to LIVE (None)
        await pilot.press("]")
        assert app._active_turn is None

        # Press ] again while LIVE -> stays LIVE
        await pilot.press("]")
        assert app._active_turn is None

        # Press \ -> LIVE
        await pilot.press("[")  # go to some turn first
        assert app._active_turn is not None
        await pilot.press("\\")
        assert app._active_turn is None


# --- 7. test_clear_resets_turns ---

def test_clear_resets_turns() -> None:
    g = CallGraph()
    g.update_from_event(_real_user_message("A"))
    g.update_from_event(_real_user_message("B"))
    assert len(g.get_turns()) == 2
    assert g.get_current_turn_index() == 1

    g.clear_turns()
    assert len(g.get_turns()) == 0
    assert g.get_current_turn_index() == -1
