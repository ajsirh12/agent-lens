"""Tests for Phase 2a: Flowchart Turn Filter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentlens.app import AgentlensApp
from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import ROOT_ID, CallGraph, FlowRecord
from agentlens.panels.flowchart import FlowchartPanel


def _agent_use(
    subagent: str,
    *,
    tid: str = "t1",
    description: str = "",
) -> HarnessEvent:
    inp: dict = {"subagent_type": subagent}
    if description:
        inp["description"] = description
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Agent",
            "tool_use_id": tid,
            "input": inp,
        },
    )


def _result(tid: str, *, error: bool = False) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_use_id": tid, "is_error": error},
    )


def _user_message(text: str = "hello") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": text},
    )


def _set_flow_timestamps(
    graph: CallGraph,
    tid: str,
    started: float,
    ended: float | None = None,
    status: str = "running",
) -> None:
    """Set timestamps on both the Instance (if present) and FlowRecord."""
    for node in graph.nodes.values():
        inst = node._instances.get(tid)
        if inst is not None:
            inst.started_ts = started
            inst.ended_ts = ended
            if status != "running":
                inst.status = status  # type: ignore[assignment]
            break
    idx = graph._flow_tid_to_index.get(tid)
    if idx is not None and idx < len(graph._flow_history):
        rec = graph._flow_history[idx]
        rec.started_ts = started
        rec.ended_ts = ended
        if status != "running":
            rec.status = status  # type: ignore[assignment]


# -------------------------------------------------------------------
# 1. Flow subgraph filters by turn
# -------------------------------------------------------------------
def test_flow_subgraph_filters_by_turn() -> None:
    """When _active_turn is set, _flow_subgraph returns only records
    from that turn."""
    panel = FlowchartPanel(mode="flow")
    g = panel._graph

    # Turn 0: first user message creates turn 0
    g.update_from_event(_user_message("turn zero"))
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))

    # Turn 1
    g.update_from_event(_user_message("turn one"))
    g.update_from_event(_agent_use("executor", tid="t2"))
    g.update_from_event(_result("t2"))

    # Verify history has records in both turns.
    assert len(g._flow_history) == 2
    assert g._flow_history[0].turn_index == 0
    assert g._flow_history[1].turn_index == 1

    # Filter to turn 0 only.
    panel._active_turn = 0
    sub = panel._flow_subgraph()
    flow_nodes = [n for nid, n in sub.nodes.items() if nid != ROOT_ID]
    assert len(flow_nodes) == 1
    assert flow_nodes[0].label == "planner"


# -------------------------------------------------------------------
# 2. Flow subgraph None returns all (backward compat)
# -------------------------------------------------------------------
def test_flow_subgraph_live_shows_current_turn_only() -> None:
    """LIVE flow mode shows only the current turn, not all history."""
    panel = FlowchartPanel(mode="flow")
    g = panel._graph

    g.update_from_event(_user_message("turn zero"))
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))

    g.update_from_event(_user_message("turn one"))
    g.update_from_event(_agent_use("executor", tid="t2"))
    g.update_from_event(_result("t2"))

    panel._active_turn = None  # LIVE = current turn only
    sub = panel._flow_subgraph()
    flow_nodes = [n for nid, n in sub.nodes.items() if nid != ROOT_ID]
    # LIVE shows only the current turn (turn 1 = executor), not all turns
    assert len(flow_nodes) == 1


# -------------------------------------------------------------------
# 3. All subgraph filters by turn
# -------------------------------------------------------------------
def test_all_subgraph_filters_by_turn() -> None:
    """_all_subgraph returns only nodes active in the specified turn + ROOT."""
    panel = FlowchartPanel(mode="all")
    g = panel._graph

    g.update_from_event(_user_message("turn zero"))
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))

    g.update_from_event(_user_message("turn one"))
    g.update_from_event(_agent_use("executor", tid="t2"))
    g.update_from_event(_result("t2"))

    panel._active_turn = 1
    sub = panel._all_subgraph()
    assert ROOT_ID in sub.nodes
    assert "agent:executor" in sub.nodes
    assert "agent:planner" not in sub.nodes


# -------------------------------------------------------------------
# 4. All subgraph re-parents orphans
# -------------------------------------------------------------------
def test_all_subgraph_reparents_orphans() -> None:
    """A node whose parent is filtered out gets re-parented to ROOT."""
    panel = FlowchartPanel(mode="all")
    g = panel._graph

    # Turn 0: planner spawns
    g.update_from_event(_user_message("turn zero"))
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))

    # Turn 1: executor spawns (its parent edge in the full graph is root->executor)
    g.update_from_event(_user_message("turn one"))
    g.update_from_event(_agent_use("executor", tid="t2"))
    g.update_from_event(_result("t2"))

    # Filter to turn 1. Executor's parent (root) is always kept.
    panel._active_turn = 1
    sub = panel._all_subgraph()
    assert (ROOT_ID, "agent:executor") in sub.edges


# -------------------------------------------------------------------
# 5. Turn navigation updates flowchart
# -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_turn_navigation_updates_flowchart(tmp_path: Path) -> None:
    """Pressing [ sets flowchart._active_turn and recomputes layout."""
    jsonl = tmp_path / "test.jsonl"
    jsonl.write_text("")
    app = AgentlensApp(
        session_override=jsonl,
        state_dir_override=tmp_path / "state",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None

        # Feed events to create turns.
        g = fc._graph
        g.update_from_event(_user_message("turn zero"))
        g.update_from_event(_agent_use("planner", tid="t1"))
        g.update_from_event(_result("t1"))
        g.update_from_event(_user_message("turn one"))
        g.update_from_event(_agent_use("executor", tid="t2"))
        g.update_from_event(_result("t2"))

        await pilot.press("[")
        await pilot.pause()

        assert app._active_turn is not None
        assert fc._active_turn == app._active_turn


# -------------------------------------------------------------------
# 6. Live restores full graph
# -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_live_restores_full_graph(tmp_path: Path) -> None:
    """Pressing \\ resets flowchart to show all nodes."""
    jsonl = tmp_path / "test.jsonl"
    jsonl.write_text("")
    app = AgentlensApp(
        session_override=jsonl,
        state_dir_override=tmp_path / "state",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None

        g = fc._graph
        g.update_from_event(_user_message("turn zero"))
        g.update_from_event(_agent_use("planner", tid="t1"))
        g.update_from_event(_result("t1"))
        g.update_from_event(_user_message("turn one"))
        g.update_from_event(_agent_use("executor", tid="t2"))
        g.update_from_event(_result("t2"))

        # Set a turn filter.
        await pilot.press("[")
        await pilot.pause()
        assert app._active_turn is not None
        assert fc._active_turn is not None

        # Go back to LIVE.
        await pilot.press("\\")
        await pilot.pause()
        assert app._active_turn is None
        assert fc._active_turn is None


# -------------------------------------------------------------------
# 7. Mode cycle preserves turn
# -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mode_cycle_preserves_turn(tmp_path: Path) -> None:
    """Pressing m (mode toggle) preserves the current _active_turn."""
    jsonl = tmp_path / "test.jsonl"
    jsonl.write_text("")
    app = AgentlensApp(
        session_override=jsonl,
        state_dir_override=tmp_path / "state",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None

        g = fc._graph
        g.update_from_event(_user_message("turn zero"))
        g.update_from_event(_agent_use("planner", tid="t1"))
        g.update_from_event(_result("t1"))
        g.update_from_event(_user_message("turn one"))
        g.update_from_event(_agent_use("executor", tid="t2"))
        g.update_from_event(_result("t2"))

        # Navigate to a turn.
        await pilot.press("[")
        await pilot.pause()
        saved_turn = app._active_turn
        assert saved_turn is not None

        # Toggle mode — turn should persist.
        await pilot.press("m")
        await pilot.pause()
        assert app._active_turn == saved_turn
        assert fc._active_turn == saved_turn


# -------------------------------------------------------------------
# 8. Empty turn shows placeholder
# -------------------------------------------------------------------
def test_empty_turn_shows_placeholder() -> None:
    """Filtering to a turn with no FlowRecords produces root-only layout."""
    panel = FlowchartPanel(mode="flow")
    g = panel._graph

    # Create two turns but only put agents in turn 0.
    g.update_from_event(_user_message("turn zero"))
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))
    g.update_from_event(_user_message("turn one"))
    # No agent calls in turn 1.

    panel._active_turn = 1
    panel._layout = panel._compute_layout()

    # Layout should have only root.
    assert len(panel._layout.nodes) == 1
    assert ROOT_ID in panel._layout.nodes
    assert len(panel._layout.edges) == 0


# -------------------------------------------------------------------
# 9. Footer counts reflect filtered graph
# -------------------------------------------------------------------
def test_footer_counts_reflect_filtered_graph() -> None:
    """get_node_count / get_edge_count return layout (filtered) counts."""
    panel = FlowchartPanel(mode="all")
    g = panel._graph

    g.update_from_event(_user_message("turn zero"))
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))
    g.update_from_event(_user_message("turn one"))
    g.update_from_event(_agent_use("executor", tid="t2"))
    g.update_from_event(_result("t2"))

    # Full graph: root + planner + executor = 3 nodes.
    panel._layout = panel._compute_layout()
    assert panel.get_node_count() == 3

    # Filter to turn 1: root + executor = 2 nodes.
    panel._active_turn = 1
    panel._layout = panel._compute_layout()
    assert panel.get_node_count() == 2

    # Back to LIVE: all 3 nodes.
    panel._active_turn = None
    panel._layout = panel._compute_layout()
    assert panel.get_node_count() == 3


# -------------------------------------------------------------------
# 10. Border color changes when filtered
# -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_border_color_changes_when_filtered(tmp_path: Path) -> None:
    """_refresh_canvas sets border color to yellow when filtered."""
    jsonl = tmp_path / "test.jsonl"
    jsonl.write_text("")
    app = AgentlensApp(
        session_override=jsonl,
        state_dir_override=tmp_path / "state",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None

        g = fc._graph
        g.update_from_event(_user_message("turn zero"))
        g.update_from_event(_agent_use("planner", tid="t1"))
        g.update_from_event(_result("t1"))
        g.update_from_event(_user_message("turn one"))

        # Set turn filter and refresh.
        fc._active_turn = 0
        fc._layout = fc._compute_layout()
        fc._refresh_canvas()

        assert fc.border_title == "Turn 1/2"

        # Reset to LIVE.
        fc._active_turn = None
        fc._layout = fc._compute_layout()
        fc._refresh_canvas()

        assert fc.border_title == ""
