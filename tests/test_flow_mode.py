"""Tests for the Flowchart [flow] mode."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentlens.app import AgentlensApp
from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import ROOT_ID, CallGraph, Edge, Instance, Node
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
    inst1 = g.nodes["agent:planner"]._instances["t1"]
    inst1.started_ts = 0.0
    inst1.ended_ts = 5.0
    inst2 = g.nodes["agent:architect"]._instances["t2"]
    inst2.started_ts = 6.0
    inst2.ended_ts = 10.0
    inst3 = g.nodes["agent:critic"]._instances["t3"]
    inst3.started_ts = 11.0

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
# 3. Flow subgraph preserves instance status and breakdown
# -------------------------------------------------------------------
def test_flow_subgraph_preserves_instance_status_and_breakdown() -> None:
    panel = FlowchartPanel()
    g = panel._graph

    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))  # marks instance as done

    # Manually add tool_breakdown to the instance for testing.
    inst = g.nodes["agent:planner"]._instances["t1"]
    inst.tool_breakdown["Read"] = 5
    inst.tool_breakdown["Edit"] = 3

    sub = panel._flow_subgraph()

    flow_nodes = [n for nid, n in sub.nodes.items() if nid != ROOT_ID]
    assert len(flow_nodes) == 1
    node = flow_nodes[0]
    assert node.status == "done"
    assert node.tool_breakdown == {"Read": 5, "Edit": 3}
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
    inst_a = g.nodes["agent:alpha"]._instances["tA"]
    inst_a.started_ts = 0.0
    inst_a.ended_ts = 10.0
    inst_a.status = "done"

    inst_b = g.nodes["agent:beta"]._instances["tB"]
    inst_b.started_ts = 1.0
    inst_b.ended_ts = 8.0
    inst_b.status = "done"

    inst_c = g.nodes["agent:gamma"]._instances["tC"]
    inst_c.started_ts = 1.0
    inst_c.ended_ts = 12.0
    inst_c.status = "done"

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

    inst_a = g.nodes["agent:alpha"]._instances["tA"]
    inst_a.started_ts = 0.0
    inst_a.ended_ts = 5.0
    inst_a.status = "done"

    inst_b = g.nodes["agent:beta"]._instances["tB"]
    inst_b.started_ts = 0.0
    inst_b.ended_ts = 8.0
    inst_b.status = "done"

    inst_c = g.nodes["agent:gamma"]._instances["tC"]
    inst_c.started_ts = 10.0
    inst_c.ended_ts = 15.0
    inst_c.status = "done"

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
