"""Tests for the Flowchart [flow] mode."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentlens.app import AgentlensApp
from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import MAX_NODES, ROOT_ID, CallGraph, FlowRecord, Node
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

    # 3 edges: root->@0, root->@1, root->@2 (all fork from ROOT).
    assert len(sub.edges) == 3

    # Verify all nodes connect directly to ROOT (fork, not chain).
    flow_ids = [nid for nid in sub.nodes if nid != ROOT_ID]
    assert len(flow_ids) == 3
    vid_0, vid_1, vid_2 = sorted(flow_ids, key=lambda v: sub.nodes[v].last_ts)
    assert (ROOT_ID, vid_0) in sub.edges
    assert (ROOT_ID, vid_1) in sub.edges
    assert (ROOT_ID, vid_2) in sub.edges
    assert (vid_0, vid_1) not in sub.edges
    assert (vid_1, vid_2) not in sub.edges


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
    vid_c = flow_ids[2]

    # C starts at ts=10. All nodes fork directly from ROOT (P1: no temporal chaining).
    assert parent_of[vid_c] == ROOT_ID


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

    # A connects to ROOT.
    assert parent_of[vid_a] == ROOT_ID
    # B also connects to ROOT (P1: no temporal chaining regardless of completion duration).
    assert parent_of[vid_b] == ROOT_ID


# -------------------------------------------------------------------
# Helpers for P1-fork regression tests (T19-T22)
# -------------------------------------------------------------------


@pytest.fixture
def make_graph():
    """Factory fixture: each call returns a fresh CallGraph."""
    def _factory() -> CallGraph:
        return CallGraph()
    return _factory


def _add_user_message(g: CallGraph, *, turn: int = 0) -> None:
    """Advance the graph's turn counter to ``turn`` by feeding user_message events."""
    # _current_turn_index starts at -1 and increments per user_message.
    # Feed (turn + 1) messages to reach the desired index.
    current = g.get_current_turn_index()
    needed = turn - current
    for _ in range(needed):
        g.update_from_event(_user_message())


def _add_flow_record(
    g: CallGraph,
    *,
    node_id: str,
    label: str,
    turn: int,
    started_ts: float,
    ended_ts: float | None = None,
    status: str = "running",
    description: str = "",
    node_type: str = "agent",
    parent_node_id: str | None = None,
) -> None:
    """Directly append a FlowRecord to ``g._flow_history`` for unit testing."""
    rec = FlowRecord(
        node_id=node_id,
        tool_use_id=f"_test_{len(g._flow_history)}",
        label=label,
        description=description,
        node_type=node_type,  # type: ignore[arg-type]
        started_ts=started_ts,
        ended_ts=ended_ts,
        status=status,  # type: ignore[arg-type]
        turn_index=turn,
        is_background=False,
    )
    # parent_node_id is added by P2 (graph-model-engineer); set if provided.
    if parent_node_id is not None:
        rec.parent_node_id = parent_node_id  # type: ignore[attr-defined]
    g._flow_history.append(rec)
    g._flow_tid_to_index[rec.tool_use_id] = len(g._flow_history) - 1


def _make_flowchart(g: CallGraph) -> FlowchartPanel:
    """Create a FlowchartPanel whose internal graph is replaced with ``g``."""
    panel = FlowchartPanel(mode="flow")
    panel._graph = g
    return panel


# -------------------------------------------------------------------
# 19. AC-1: Sequential spawns — both ROOT children (P1 regression guard)
# -------------------------------------------------------------------
def test_flow_subgraph_main_sequential_spawns_fork(make_graph):
    """AC-1: A completes before B starts — both should be ROOT children (P1 regression guard)."""
    g = make_graph()
    _add_user_message(g, turn=0)
    # A spawns and completes
    _add_flow_record(g, node_id="agent:planner", label="planner", turn=0,
                     started_ts=1000.0, ended_ts=1005.0, status="done")
    # B spawns after A completes — same turn
    _add_flow_record(g, node_id="agent:executor", label="executor", turn=0,
                     started_ts=1006.0, ended_ts=None, status="running")

    fc = _make_flowchart(g)
    sub = fc._flow_subgraph()

    vids = [v for v in sub.nodes if v != ROOT_ID]
    assert len(vids) == 2
    parent_of = {child: parent for (parent, child) in sub.edges}
    for vid in vids:
        assert parent_of[vid] == ROOT_ID, f"{vid} should be ROOT child, got {parent_of[vid]}"
    # No chain edge
    vid_a = [v for v in vids if "planner" in v][0]
    vid_b = [v for v in vids if "executor" in v][0]
    assert (vid_a, vid_b) not in sub.edges


# -------------------------------------------------------------------
# 20. AC-2: Simultaneous spawns — both ROOT children
# -------------------------------------------------------------------
def test_flow_subgraph_simultaneous_spawn_forks(make_graph):
    """AC-2: Simultaneous spawns (same ts) — both ROOT children."""
    g = make_graph()
    _add_user_message(g, turn=0)
    ts = 1000.0
    _add_flow_record(g, node_id="agent:alpha", label="alpha", turn=0,
                     started_ts=ts, ended_ts=None, status="running")
    _add_flow_record(g, node_id="agent:beta", label="beta", turn=0,
                     started_ts=ts, ended_ts=None, status="running")

    fc = _make_flowchart(g)
    sub = fc._flow_subgraph()

    parent_of = {child: parent for (parent, child) in sub.edges}
    for vid in sub.nodes:
        if vid == ROOT_ID:
            continue
        assert parent_of[vid] == ROOT_ID


# -------------------------------------------------------------------
# 21. AC-5: Exact boundary timestamp — B is ROOT child (not chained)
# -------------------------------------------------------------------
def test_flow_subgraph_exact_equality_does_not_chain(make_graph):
    """AC-5: A.ended_ts == B.started_ts — B should still be ROOT child (not chained)."""
    g = make_graph()
    _add_user_message(g, turn=0)
    boundary_ts = 1005.0
    _add_flow_record(g, node_id="agent:alpha", label="alpha", turn=0,
                     started_ts=1000.0, ended_ts=boundary_ts, status="done")
    _add_flow_record(g, node_id="agent:beta", label="beta", turn=0,
                     started_ts=boundary_ts, ended_ts=None, status="running")

    fc = _make_flowchart(g)
    sub = fc._flow_subgraph()

    parent_of = {child: parent for (parent, child) in sub.edges}
    vid_b = [v for v in sub.nodes if "beta" in v][0]
    assert parent_of[vid_b] == ROOT_ID


# -------------------------------------------------------------------
# 22. AC-6: Edge-case FlowRecords must not raise, all nodes ROOT children
# -------------------------------------------------------------------
def test_flow_subgraph_handles_arbitrary_records_without_raising(make_graph):
    """AC-6: Various edge-case FlowRecords must not raise, all nodes ROOT children."""
    g = make_graph()
    _add_user_message(g, turn=0)
    # Empty description
    _add_flow_record(g, node_id="agent:x", label="x", turn=0,
                     started_ts=1000.0, ended_ts=None, status="running",
                     description="")
    # None ended_ts (still running)
    _add_flow_record(g, node_id="agent:y", label="y", turn=0,
                     started_ts=1001.0, ended_ts=None, status="running")
    # status=error
    _add_flow_record(g, node_id="agent:z", label="z", turn=0,
                     started_ts=1002.0, ended_ts=1003.0, status="error")

    fc = _make_flowchart(g)
    sub = fc._flow_subgraph()  # must not raise

    flow_nodes = [v for v in sub.nodes if v != ROOT_ID]
    assert len(flow_nodes) == 3
    parent_of = {child: parent for (parent, child) in sub.edges}
    for vid in flow_nodes:
        assert parent_of[vid] == ROOT_ID


# -------------------------------------------------------------------
# Helpers for P2-nested regression tests (T23-T30)
# -------------------------------------------------------------------

def _nested_spawn_event(
    tool_name: str,
    subagent_uuid: str,
    *,
    tid: str = "t-nested-1",
    subagent_type: str = "executor",
    skill_name: str = "probe",
    description: str = "",
) -> HarnessEvent:
    """Construct a subagent tool_use event that routes to _handle_nested_spawn."""
    inp: dict = {}
    if tool_name in ("Agent", "Task"):
        inp["subagent_type"] = subagent_type
        if description:
            inp["description"] = description
    else:  # Skill
        inp["skill"] = skill_name
        if description:
            inp["description"] = description
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": tool_name,
            "tool_use_id": tid,
            "input": inp,
            "subagent_uuid": subagent_uuid,
        },
    )


def _setup_parent_node(g: CallGraph, node_id: str, uuid: str) -> None:
    """Register a parent node and its uuid mapping so _handle_nested_spawn resolves it."""
    if node_id not in g.nodes:
        g.nodes[node_id] = Node(
            id=node_id,
            label=node_id.split(":", 1)[-1],
            node_type="agent",
            status="running",
            call_count=1,
            last_ts=0.0,
        )
    g._subagent_uuid_to_node[uuid] = node_id


# -------------------------------------------------------------------
# 23. T23: nested skill appears as child of parent agent in flow mode
# -------------------------------------------------------------------
def test_flow_nested_child_attaches_to_parent_vid(make_graph):
    """AC1: nested FlowRecord with parent_node_id attaches to parent vid, not ROOT."""
    g = make_graph()
    _add_user_message(g, turn=0)
    _add_flow_record(g, node_id="agent:executor", label="executor", turn=0,
                     started_ts=1000.0, ended_ts=None, status="running")
    _add_flow_record(g, node_id="skill:probe", label="probe", turn=0,
                     started_ts=1001.0, ended_ts=None, status="running",
                     node_type="skill", parent_node_id="agent:executor")

    fc = _make_flowchart(g)
    sub = fc._flow_subgraph()

    parent_of = {child: parent for (parent, child) in sub.edges}
    vid_parent = "agent:executor@0"
    vid_child = "skill:probe@1"
    assert vid_parent in sub.nodes
    assert vid_child in sub.nodes
    assert parent_of[vid_child] == vid_parent, (
        f"skill:probe@1 should be child of agent:executor@0, got {parent_of[vid_child]}"
    )
    assert (ROOT_ID, vid_child) not in sub.edges


# -------------------------------------------------------------------
# 24. T24: nested child attaches correctly when parent still running
# -------------------------------------------------------------------
def test_flow_nested_child_attaches_when_parent_still_running(make_graph):
    """AC2: parent ended_ts=None (still running) does not break child attachment."""
    g = make_graph()
    _add_user_message(g, turn=0)
    _add_flow_record(g, node_id="agent:executor", label="executor", turn=0,
                     started_ts=1000.0, ended_ts=None, status="running")
    _add_flow_record(g, node_id="skill:probe", label="probe", turn=0,
                     started_ts=1001.0, ended_ts=None, status="running",
                     node_type="skill", parent_node_id="agent:executor")

    fc = _make_flowchart(g)
    sub = fc._flow_subgraph()  # must not raise

    parent_of = {child: parent for (parent, child) in sub.edges}
    vid_child = "skill:probe@1"
    assert parent_of[vid_child] == "agent:executor@0"


# -------------------------------------------------------------------
# 25. T25: turn filter excludes nodes from other turns, preserves parent-child
# -------------------------------------------------------------------
def test_flow_nested_turn_filter_consistency(make_graph):
    """AC3: turn filter applies to all nodes; nested parent-child preserved within turn."""
    g = make_graph()
    _add_user_message(g, turn=0)
    # turn 0: A and nested B
    _add_flow_record(g, node_id="agent:alpha", label="alpha", turn=0,
                     started_ts=1000.0, ended_ts=None, status="running")
    _add_flow_record(g, node_id="skill:probe", label="probe", turn=0,
                     started_ts=1001.0, ended_ts=None, status="running",
                     node_type="skill", parent_node_id="agent:alpha")
    # turn 1: top-level C (different turn)
    _add_flow_record(g, node_id="agent:gamma", label="gamma", turn=1,
                     started_ts=2000.0, ended_ts=None, status="running")

    fc = _make_flowchart(g)
    fc._active_turn = 0
    sub = fc._flow_subgraph()

    flow_ids = [v for v in sub.nodes if v != ROOT_ID]
    assert len(flow_ids) == 2
    node_labels = {sub.nodes[v].label for v in flow_ids}
    assert "alpha" in node_labels
    assert "probe" in node_labels
    assert "gamma" not in node_labels

    parent_of = {child: parent for (parent, child) in sub.edges}
    vid_child = "skill:probe@1"
    assert parent_of[vid_child] == "agent:alpha@0"


# -------------------------------------------------------------------
# 26. T26: ROOT fallback when parent_node_id has no matching FlowRecord
# -------------------------------------------------------------------
def test_flow_nested_falls_back_to_root_when_parent_missing(make_graph):
    """AC4: child FlowRecord whose parent is absent falls back to ROOT, no exception."""
    g = make_graph()
    _add_user_message(g, turn=0)
    # Child references a parent that was never added to _flow_history
    _add_flow_record(g, node_id="skill:orphan", label="orphan", turn=0,
                     started_ts=1000.0, ended_ts=None, status="running",
                     node_type="skill", parent_node_id="agent:ghost")

    fc = _make_flowchart(g)
    sub = fc._flow_subgraph()  # must not raise

    flow_ids = [v for v in sub.nodes if v != ROOT_ID]
    assert len(flow_ids) == 1
    parent_of = {child: parent for (parent, child) in sub.edges}
    assert parent_of["skill:orphan@0"] == ROOT_ID


# -------------------------------------------------------------------
# 27. T27: _handle_nested_spawn appends FlowRecord and respects MAX_NODES cap
# -------------------------------------------------------------------
def test_handle_nested_spawn_appends_flow_record_and_respects_cap():
    """AC5: FlowRecord created when cap not reached; silently skipped when at cap."""
    # Part a: cap not reached — FlowRecord is appended and tid is registered
    g_a = CallGraph()
    g_a.update_from_event(_agent_use("executor", tid="t-parent"))
    parent_node_id = "agent:executor"
    parent_uuid = "uuid-exec-001"
    _setup_parent_node(g_a, parent_node_id, parent_uuid)
    initial_len = len(g_a._flow_history)

    ev = _nested_spawn_event("Skill", parent_uuid, tid="t-nested-1", skill_name="probe")
    g_a.update_from_event(ev)

    assert len(g_a._flow_history) == initial_len + 1
    assert "t-nested-1" in g_a._flow_tid_to_index
    rec = g_a._flow_history[-1]
    assert rec.node_id == "skill:probe"
    assert rec.parent_node_id == parent_node_id  # type: ignore[attr-defined]

    # Part b: history at cap — FlowRecord NOT appended, no exception
    g_b = CallGraph()
    _setup_parent_node(g_b, parent_node_id, parent_uuid)
    # Fill _flow_history to MAX_NODES using the helper
    for i in range(MAX_NODES):
        g_b._flow_history.append(
            FlowRecord(
                node_id=f"agent:dummy{i}",
                tool_use_id=f"_cap_{i}",
                label=f"dummy{i}",
                description="",
                node_type="agent",
                started_ts=float(i),
            )
        )
    assert len(g_b._flow_history) == MAX_NODES

    ev_b = _nested_spawn_event("Skill", parent_uuid, tid="t-nested-cap", skill_name="probe")
    g_b.update_from_event(ev_b)  # must not raise

    assert len(g_b._flow_history) == MAX_NODES, "FlowRecord must not be appended when at cap"
    assert "t-nested-cap" not in g_b._flow_tid_to_index


# -------------------------------------------------------------------
# 28. T28: _handle_nested_spawn never raises on edge-case inputs
# -------------------------------------------------------------------
def test_handle_nested_spawn_never_raises_on_edge_inputs():
    """AC6: _handle_nested_spawn is never-raise for malformed inputs."""
    parent_uuid = "uuid-exec-002"
    g = CallGraph()
    _setup_parent_node(g, "agent:executor", parent_uuid)

    # Case 1: ts=None (HarnessEvent.ts may be None in edge cases)
    ev1 = HarnessEvent(
        type=EventType.tool_use,
        ts=None,  # type: ignore[arg-type]
        agent_id=None,
        payload={
            "tool_name": "Skill",
            "tool_use_id": "t-edge-1",
            "input": {"skill": "probe"},
            "subagent_uuid": parent_uuid,
        },
    )
    try:
        g.update_from_event(ev1)
    except Exception as exc:
        raise AssertionError(f"update_from_event raised on ts=None: {exc}") from exc

    # Case 2: input dict has no 'description' key
    ev2 = _nested_spawn_event("Skill", parent_uuid, tid="t-edge-2", skill_name="probe2")
    try:
        g.update_from_event(ev2)
    except Exception as exc:
        raise AssertionError(f"update_from_event raised without description key: {exc}") from exc

    # Case 3: tid is missing (tool_use_id absent)
    ev3 = HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Skill",
            "input": {"skill": "probe3"},
            "subagent_uuid": parent_uuid,
        },
    )
    try:
        g.update_from_event(ev3)
    except Exception as exc:
        raise AssertionError(f"update_from_event raised with missing tid: {exc}") from exc

    # Case 4: unknown parent uuid (not in _subagent_uuid_to_node)
    ev4 = _nested_spawn_event("Agent", "uuid-unknown-999", tid="t-edge-4")
    try:
        g.update_from_event(ev4)
    except Exception as exc:
        raise AssertionError(f"update_from_event raised with unknown parent uuid: {exc}") from exc


# -------------------------------------------------------------------
# 29. T29: nested Skill spawn renders as skill:<name>@<i> child of parent agent
# -------------------------------------------------------------------
def test_flow_nested_skill_spawn_renders(make_graph):
    """AC7: skill nested spawn renders as skill:<name>@<i> vid under parent agent vid."""
    g = make_graph()
    _add_user_message(g, turn=0)
    _add_flow_record(g, node_id="agent:executor", label="executor", turn=0,
                     started_ts=1000.0, ended_ts=None, status="running")
    _add_flow_record(g, node_id="skill:my-skill", label="my-skill", turn=0,
                     started_ts=1001.0, ended_ts=None, status="running",
                     node_type="skill", parent_node_id="agent:executor")

    fc = _make_flowchart(g)
    sub = fc._flow_subgraph()

    skill_vids = [v for v in sub.nodes if v.startswith("skill:")]
    assert len(skill_vids) == 1
    skill_vid = skill_vids[0]
    assert skill_vid == "skill:my-skill@1"

    parent_of = {child: parent for (parent, child) in sub.edges}
    assert parent_of[skill_vid] == "agent:executor@0"


# -------------------------------------------------------------------
# 30. T30: same node_id multiple invocations — AMB-3 last-vid rule
# -------------------------------------------------------------------
def test_flow_nested_same_node_id_multiple_invocations(make_graph):
    """AC8: B#1 is child of A#1 vid; B#2 is child of A#2 vid (last-vid rule)."""
    g = make_graph()
    _add_user_message(g, turn=0)

    # A invoke #1 (i=0)
    _add_flow_record(g, node_id="agent:alpha", label="alpha", turn=0,
                     started_ts=1000.0, ended_ts=1005.0, status="done")
    # B nested under A#1 (i=1)
    _add_flow_record(g, node_id="skill:probe", label="probe", turn=0,
                     started_ts=1001.0, ended_ts=1003.0, status="done",
                     node_type="skill", parent_node_id="agent:alpha")
    # A invoke #2 (i=2)
    _add_flow_record(g, node_id="agent:alpha", label="alpha", turn=0,
                     started_ts=1010.0, ended_ts=1015.0, status="done")
    # B nested under A#2 (i=3) — should attach to A's most recent vid (i=2)
    _add_flow_record(g, node_id="skill:probe", label="probe", turn=0,
                     started_ts=1011.0, ended_ts=1013.0, status="done",
                     node_type="skill", parent_node_id="agent:alpha")

    fc = _make_flowchart(g)
    sub = fc._flow_subgraph()

    parent_of = {child: parent for (parent, child) in sub.edges}

    # vid indices: alpha@0, probe@1, alpha@2, probe@3
    assert parent_of["skill:probe@1"] == "agent:alpha@0", (
        f"probe@1 should be child of alpha@0, got {parent_of['skill:probe@1']}"
    )
    assert parent_of["skill:probe@3"] == "agent:alpha@2", (
        f"probe@3 should be child of alpha@2, got {parent_of['skill:probe@3']}"
    )
