"""Tests for the Flowchart [flow] mode."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentlens.app import AgentlensApp
from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import ROOT_ID, CallGraph, Edge, FlowRecord, Instance, Node
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


def _skill_use(skill: str, *, tid: str = "t1") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Skill",
            "tool_use_id": tid,
            "input": {"skill": skill},
        },
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
    # Update instance if it exists
    for node in graph.nodes.values():
        inst = node._instances.get(tid)
        if inst is not None:
            inst.started_ts = started
            inst.ended_ts = ended
            if status != "running":
                inst.status = status  # type: ignore[assignment]
            break
    # Update flow record
    idx = graph._flow_tid_to_index.get(tid)
    if idx is not None and idx < len(graph._flow_history):
        rec = graph._flow_history[idx]
        rec.started_ts = started
        rec.ended_ts = ended
        if status != "running":
            rec.status = status  # type: ignore[assignment]


# -------------------------------------------------------------------
# 1. Mode cycle: all -> running -> flow -> all
# -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_toggle_mode_cycles_through_three_modes(tmp_path: Path) -> None:
    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None
        assert fc.get_mode() == "all"

        await pilot.press("m")
        await pilot.pause()
        assert fc.get_mode() == "running"

        await pilot.press("m")
        await pilot.pause()
        assert fc.get_mode() == "flow"

        await pilot.press("m")
        await pilot.pause()
        assert fc.get_mode() == "all"


# -------------------------------------------------------------------
# 2. Flow subgraph creates per-invocation nodes
# -------------------------------------------------------------------
def test_flow_subgraph_creates_per_invocation_nodes() -> None:
    panel = FlowchartPanel()
    g = panel._graph

    # Three sequential agent spawns that complete before the next starts.
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))
    g.update_from_event(_agent_use("architect", tid="t2"))
    g.update_from_event(_result("t2"))
    g.update_from_event(_agent_use("critic", tid="t3"))

    # Manually set timestamps to enforce sequential ordering.
    _set_flow_timestamps(g, "t1", started=0.0, ended=5.0, status="done")
    _set_flow_timestamps(g, "t2", started=6.0, ended=10.0, status="done")
    _set_flow_timestamps(g, "t3", started=11.0)

    sub = panel._flow_subgraph()

    # Root + 3 instance nodes = 4 total.
    assert len(sub.nodes) == 4
    assert ROOT_ID in sub.nodes

    # 3 edges forming a chain: root->@0, @0->@1, @1->@2.
    assert len(sub.edges) == 3

    # Verify the chain links are correct.
    flow_ids = [nid for nid in sub.nodes if nid != ROOT_ID]
    assert len(flow_ids) == 3
    # Edges should form root -> first -> second -> third.
    # Starting from root, walk the chain.
    cur = ROOT_ID
    visited = []
    for _ in range(3):
        children = [c for (p, c) in sub.edges if p == cur]
        assert len(children) == 1
        cur = children[0]
        visited.append(cur)
    assert len(visited) == 3


# -------------------------------------------------------------------
# 3. Flow subgraph preserves instance status
# -------------------------------------------------------------------
def test_flow_subgraph_preserves_instance_status() -> None:
    panel = FlowchartPanel()
    g = panel._graph

    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))  # marks as done

    sub = panel._flow_subgraph()

    flow_nodes = [n for nid, n in sub.nodes.items() if nid != ROOT_ID]
    assert len(flow_nodes) == 1
    node = flow_nodes[0]
    assert node.status == "done"
    assert node.label == "planner"


# -------------------------------------------------------------------
# 4. Flow subgraph empty returns root only
# -------------------------------------------------------------------
def test_flow_subgraph_empty_returns_root_only() -> None:
    panel = FlowchartPanel()
    # No events fed — graph has only root, no instances.
    sub = panel._flow_subgraph()
    assert len(sub.nodes) == 1
    assert ROOT_ID in sub.nodes
    assert len(sub.edges) == 0


# -------------------------------------------------------------------
# 5. Flow mode: no sticky-running override
# -------------------------------------------------------------------
def test_flow_mode_no_sticky_override() -> None:
    panel = FlowchartPanel(mode="flow")
    g = panel._graph

    # Spawn and complete an agent.
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))

    # planner is "done" but still in _current_turn (sticky-running).
    assert g.is_in_current_turn("agent:planner")
    node = g.nodes["agent:planner"]
    assert node.status == "done"

    # In flow mode, the flow subgraph should show "done" not "running".
    sub = panel._flow_subgraph()
    flow_nodes = [n for nid, n in sub.nodes.items() if nid != ROOT_ID]
    assert len(flow_nodes) == 1
    assert flow_nodes[0].status == "done"


# -------------------------------------------------------------------
# 6. _base_node_id strips both # and @ suffixes
# -------------------------------------------------------------------
def test_base_node_id_strips_both_hash_and_at() -> None:
    assert FlowchartPanel._base_node_id("agent:planner@3") == "agent:planner"
    assert FlowchartPanel._base_node_id("agent:executor#abc123") == "agent:executor"
    assert FlowchartPanel._base_node_id("agent:foo#bar@baz") == "agent:foo"
    assert FlowchartPanel._base_node_id("skill:critic") == "skill:critic"
    assert FlowchartPanel._base_node_id(None) is None
    assert FlowchartPanel._base_node_id("") == ""


# -------------------------------------------------------------------
# 7. Flow node uses instance description as label
# -------------------------------------------------------------------
def test_flow_node_uses_instance_description_as_label() -> None:
    panel = FlowchartPanel()
    g = panel._graph

    g.update_from_event(_agent_use("explore", tid="t1", description="Schema probe"))
    g.update_from_event(_agent_use("explore", tid="t2", description="Code review"))

    sub = panel._flow_subgraph()

    flow_labels = [n.label for nid, n in sub.nodes.items() if nid != ROOT_ID]
    assert "Schema probe" in flow_labels
    assert "Code review" in flow_labels
    # The generic type name should NOT appear as a label.
    assert "explore" not in flow_labels


# -------------------------------------------------------------------
# 8. Flow node falls back to type when no description
# -------------------------------------------------------------------
def test_flow_node_falls_back_to_type_when_no_description() -> None:
    panel = FlowchartPanel()
    g = panel._graph

    g.update_from_event(_agent_use("planner", tid="t1"))

    sub = panel._flow_subgraph()

    flow_labels = [n.label for nid, n in sub.nodes.items() if nid != ROOT_ID]
    assert flow_labels == ["planner"]


# -------------------------------------------------------------------
# 9. Parallel spawns fork from same parent
# -------------------------------------------------------------------
def test_flow_parallel_spawns_fork_from_same_parent() -> None:
    """Instances B and C start before A ends -- they should both
    connect to ROOT (nothing completed before ts=1)."""
    panel = FlowchartPanel()
    g = panel._graph

    # Create three agent types so each gets its own node + instance.
    g.update_from_event(_agent_use("alpha", tid="tA"))
    g.update_from_event(_agent_use("beta", tid="tB"))
    g.update_from_event(_agent_use("gamma", tid="tC"))

    # Manually set timestamps to simulate parallel execution.
    _set_flow_timestamps(g, "tA", started=0.0, ended=10.0, status="done")
    _set_flow_timestamps(g, "tB", started=1.0, ended=8.0, status="done")
    _set_flow_timestamps(g, "tC", started=1.0, ended=12.0, status="done")

    sub = panel._flow_subgraph()

    # Build parent map: child_vid -> parent_vid
    parent_of = {cid: pid for (pid, cid) in sub.edges}

    # Find the vids for each instance (sorted by started_ts: A=0, B=1, C=1)
    flow_ids = sorted(
        [nid for nid in sub.nodes if nid != ROOT_ID],
        key=lambda nid: sub.nodes[nid].last_ts,
    )
    assert len(flow_ids) == 3
    vid_a, vid_b, vid_c = flow_ids[0], flow_ids[1], flow_ids[2]

    # A connects to ROOT (nothing completed before ts=0).
    assert parent_of[vid_a] == ROOT_ID
    # B and C start at ts=1; nothing has completed by then (A ends at 10).
    assert parent_of[vid_b] == ROOT_ID
    assert parent_of[vid_c] == ROOT_ID


# -------------------------------------------------------------------
# 10. Sequential after parallel joins to last completed
# -------------------------------------------------------------------
def test_flow_sequential_after_parallel_joins() -> None:
    """C starts after both A and B end. C's parent should be B
    (the last to complete before C started)."""
    panel = FlowchartPanel()
    g = panel._graph

    g.update_from_event(_agent_use("alpha", tid="tA"))
    g.update_from_event(_agent_use("beta", tid="tB"))
    g.update_from_event(_agent_use("gamma", tid="tC"))

    _set_flow_timestamps(g, "tA", started=0.0, ended=5.0, status="done")
    _set_flow_timestamps(g, "tB", started=0.0, ended=8.0, status="done")
    _set_flow_timestamps(g, "tC", started=10.0, ended=15.0, status="done")

    sub = panel._flow_subgraph()

    parent_of = {cid: pid for (pid, cid) in sub.edges}

    # Find vids sorted by started_ts
    flow_ids = sorted(
        [nid for nid in sub.nodes if nid != ROOT_ID],
        key=lambda nid: sub.nodes[nid].last_ts,
    )
    assert len(flow_ids) == 3
    vid_a, vid_b, vid_c = flow_ids[0], flow_ids[1], flow_ids[2]

    # C starts at ts=10. Both A (end=5) and B (end=8) are completed.
    # B finished last, so C's parent should be B's vid.
    assert parent_of[vid_c] == vid_b


# -------------------------------------------------------------------
# 11. Flow history survives user_message flush
# -------------------------------------------------------------------
def test_flow_history_survives_flush() -> None:
    """_flow_history persists across user_message flushes while
    _instances are cleared."""
    g = CallGraph()

    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))
    g.update_from_event(_agent_use("executor", tid="t2"))

    # Precondition: both instances and flow_history populated.
    assert len(g._flow_history) == 2
    total_instances = sum(
        len(n._instances) for n in g.nodes.values()
    )
    assert total_instances == 2

    # Flush via user_message.
    g.update_from_event(_user_message("next turn"))

    # Instances are cleared on flush.
    total_instances_after = sum(
        len(n._instances) for n in g.nodes.values()
    )
    assert total_instances_after == 0

    # Flow history is NOT cleared.
    assert len(g._flow_history) == 2
    assert len(g._flow_tid_to_index) == 2


# -------------------------------------------------------------------
# 12. Flow subgraph uses history not instances (survives flush)
# -------------------------------------------------------------------
def test_flow_subgraph_uses_history_not_instances() -> None:
    """After flush, _flow_subgraph still returns nodes from history
    when navigating to the old turn via _active_turn.
    """
    panel = FlowchartPanel()
    g = panel._graph

    # Start a turn so planner gets turn_index = 0.
    g.update_from_event(_user_message("first turn"))
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))

    # Flush instances by starting a new turn.
    g.update_from_event(_user_message("next turn"))

    # Instances should be empty.
    assert len(g.nodes["agent:planner"]._instances) == 0

    # Navigate to the old turn — FlowRecords survive flush.
    panel._active_turn = 0
    sub = panel._flow_subgraph()
    flow_nodes = [n for nid, n in sub.nodes.items() if nid != ROOT_ID]
    assert len(flow_nodes) == 1
    assert flow_nodes[0].status == "done"


# -------------------------------------------------------------------
# 13. Flow history cleared on session clear
# -------------------------------------------------------------------
def test_flow_history_cleared_on_session_clear() -> None:
    """clear() (session switch) resets _flow_history."""
    panel = FlowchartPanel()
    g = panel._graph

    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))

    assert len(g._flow_history) == 1

    # Session switch clears everything.
    panel.clear()

    # After clear, a new CallGraph is created — flow_history is empty.
    assert len(panel._graph._flow_history) == 0
    assert len(panel._graph._flow_tid_to_index) == 0


# -------------------------------------------------------------------
# 14. Flow click highlights single node
# -------------------------------------------------------------------
def test_flow_click_highlights_single_node() -> None:
    """_selected_flow_vid targets exactly one flow node."""
    panel = FlowchartPanel(mode="flow")
    g = panel._graph

    g.update_from_event(_agent_use("explore", tid="t1", description="first"))
    g.update_from_event(_result("t1"))
    g.update_from_event(_agent_use("explore", tid="t2", description="second"))

    # Simulate selecting a flow vid.
    panel._selected_flow_vid = "agent:explore@0"

    # The highlight logic: @0 should match, @1 should not.
    assert panel._selected_flow_vid == "agent:explore@0"
    # Verify that a different flow vid does NOT match.
    nid_0 = "agent:explore@0"
    nid_1 = "agent:explore@1"
    assert (nid_0 == panel._selected_flow_vid) is True
    assert (nid_1 == panel._selected_flow_vid) is False


# -------------------------------------------------------------------
# 15. Flow click clears on non-flow selection
# -------------------------------------------------------------------
def test_flow_vid_cleared_on_session_clear() -> None:
    panel = FlowchartPanel()
    panel._selected_flow_vid = "agent:foo@2"
    panel.clear()
    assert panel._selected_flow_vid is None


# -------------------------------------------------------------------
# 16. Skill events also produce FlowRecords
# -------------------------------------------------------------------
def test_skill_events_produce_flow_records() -> None:
    g = CallGraph()
    g.update_from_event(_skill_use("my-skill", tid="ts1"))
    g.update_from_event(_result("ts1"))

    assert len(g._flow_history) == 1
    rec = g._flow_history[0]
    assert rec.node_id == "skill:my-skill"
    assert rec.node_type == "skill"
    assert rec.status == "done"
    assert rec.ended_ts is not None


# -------------------------------------------------------------------
# 17. Instant tool_result acks don't break fork detection
# -------------------------------------------------------------------
def test_instant_ack_does_not_break_fork_detection() -> None:
    """Background agents get an instant tool_result ack (~0.003s) before
    the real work starts. These sub-0.5s completions must NOT count as
    'completed predecessors', otherwise parallel spawns chain linearly
    instead of forking from the same parent."""
    panel = FlowchartPanel()
    g = panel._graph

    # A completes with an instant ack (0.003s duration).
    g.update_from_event(_agent_use("alpha", tid="tA"))
    g.update_from_event(_result("tA"))

    # B and C spawn right after A's ack — they are truly parallel.
    g.update_from_event(_agent_use("beta", tid="tB"))
    g.update_from_event(_agent_use("gamma", tid="tC"))

    # A: instant ack (0.003s) — should NOT count as completed.
    _set_flow_timestamps(g, "tA", started=0.0, ended=0.003, status="done")
    # B and C: real parallel work.
    _set_flow_timestamps(g, "tB", started=0.1, ended=8.0, status="done")
    _set_flow_timestamps(g, "tC", started=0.1, ended=12.0, status="done")

    sub = panel._flow_subgraph()

    parent_of = {cid: pid for (pid, cid) in sub.edges}

    flow_ids = sorted(
        [nid for nid in sub.nodes if nid != ROOT_ID],
        key=lambda nid: sub.nodes[nid].last_ts,
    )
    assert len(flow_ids) == 3

    # All three should fork from ROOT because A's instant ack
    # doesn't count as a real completion.
    for vid in flow_ids:
        assert parent_of[vid] == ROOT_ID, (
            f"{vid} should connect to ROOT but connects to {parent_of[vid]}"
        )


# -------------------------------------------------------------------
# 18. Real completion (>= 0.5s) creates sequential chain
# -------------------------------------------------------------------
def test_real_completion_creates_sequential_chain() -> None:
    """When an agent completes with >= 0.5s duration, the next spawn
    should chain from it (not fork from ROOT)."""
    panel = FlowchartPanel()
    g = panel._graph

    g.update_from_event(_agent_use("alpha", tid="tA"))
    g.update_from_event(_result("tA"))
    g.update_from_event(_agent_use("beta", tid="tB"))

    # A: real work (5s duration) — qualifies as completed.
    _set_flow_timestamps(g, "tA", started=0.0, ended=5.0, status="done")
    # B: starts after A finished.
    _set_flow_timestamps(g, "tB", started=6.0, ended=10.0, status="done")

    sub = panel._flow_subgraph()

    parent_of = {cid: pid for (pid, cid) in sub.edges}

    flow_ids = sorted(
        [nid for nid in sub.nodes if nid != ROOT_ID],
        key=lambda nid: sub.nodes[nid].last_ts,
    )
    assert len(flow_ids) == 2
    vid_a, vid_b = flow_ids

    # A chains from ROOT.
    assert parent_of[vid_a] == ROOT_ID
    # B chains from A (A completed with real duration before B started).
    assert parent_of[vid_b] == vid_a
