"""Phase 1 mode-dependent instance view tests.

Verifies that parallel agent spawns get distinct per-instance tracking
on the graph model, that running-mode rendering expands them into
virtual nodes, that all-mode stays aggregated, and that cross-highlight
writes the canonical base id rather than the virtual instance id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness_visual.app import HarnessVisualApp
from harness_visual.events import EventType, HarnessEvent
from harness_visual.graph_model import MAX_BREAKDOWN_TOOLS, ROOT_ID, CallGraph


def _agent_use(
    subagent: str, *, parent: str | None = None, tid: str = "t1"
) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=parent,
        payload={
            "tool_name": "Agent",
            "tool_use_id": tid,
            "input": {"subagent_type": subagent},
        },
    )


def _result(
    tid: str, *, error: bool = False, linked: str | None = None
) -> HarnessEvent:
    payload: dict[str, object] = {"tool_use_id": tid, "is_error": error}
    if linked is not None:
        payload["linked_subagent_uuid"] = linked
    return HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload=payload,
    )


def _subagent_tool_use(
    tool: str,
    subagent_uuid: str,
    *,
    tid: str = "nested-1",
    subagent_type: str = "writer",
) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": tool,
            "tool_use_id": tid,
            "subagent_uuid": subagent_uuid,
            "input": {"subagent_type": subagent_type},
        },
    )


# ---------------------------------------------------------------------
# graph_model.Instance lifecycle
# ---------------------------------------------------------------------


def test_handle_tool_use_creates_instance_on_node() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("planner", tid="t1"))
    node = g.nodes["agent:planner"]
    assert len(node._instances) == 1
    inst = node._instances["t1"]
    assert inst.tool_use_id == "t1"
    assert inst.status == "running"
    assert inst.ended_ts is None


def test_handle_tool_result_updates_instance_status() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))
    inst = g.nodes["agent:planner"]._instances["t1"]
    assert inst.status == "done"
    assert inst.ended_ts is not None


def test_two_parallel_spawns_create_two_instances_on_same_node() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    g.update_from_event(_agent_use("executor", tid="t2"))
    node = g.nodes["agent:executor"]
    assert node.call_count == 2
    assert len(node._instances) == 2
    assert node._instances["t1"].status == "running"
    assert node._instances["t2"].status == "running"


def test_instance_linked_subagent_uuid_set_from_tool_result() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_result("t1", linked="sub-uuid-abc"))
    inst = g.nodes["agent:planner"]._instances["t1"]
    assert inst.subagent_uuid == "sub-uuid-abc"
    assert inst.status == "done"


# ---------------------------------------------------------------------
# running-mode expansion
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_subgraph_expands_instances(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None
        fc.add_event(_agent_use("executor", tid="tid-aaaaaa"))
        fc.add_event(_agent_use("executor", tid="tid-bbbbbb"))
        await pilot.pause()

        sub = fc._running_subgraph()
        vids = [nid for nid in sub.nodes if nid.startswith("agent:executor")]
        assert len(vids) == 2
        for vid in vids:
            assert vid != "agent:executor"
            assert "#" in vid
            assert sub.nodes[vid].status == "running"
            assert (ROOT_ID, vid) in sub.edges


@pytest.mark.asyncio
async def test_all_mode_unchanged_by_instances(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None
        fc.add_event(_agent_use("executor", tid="t1"))
        fc.add_event(_agent_use("executor", tid="t2"))
        await pilot.pause()

        # All-mode uses the raw graph — still one aggregated node.
        assert "agent:executor" in fc._graph.nodes
        assert fc._graph.nodes["agent:executor"].call_count == 2
        # And only one executor entry in the aggregated keyset.
        executor_keys = [
            nid for nid in fc._graph.nodes if nid.startswith("agent:executor")
        ]
        assert executor_keys == ["agent:executor"]


@pytest.mark.asyncio
async def test_nested_node_without_instances_falls_back_to_single(
    tmp_path: Path,
) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None
        # Main-session spawn of a parent agent + link to a subagent file.
        fc.add_event(_agent_use("planner", tid="t1"))
        fc.add_event(_result("t1", linked="sub-planner"))
        # Nested spawn: a Task inside the planner subagent file.
        fc.add_event(
            _subagent_tool_use(
                "Agent", "sub-planner", tid="nested-1", subagent_type="writer"
            )
        )
        await pilot.pause()

        # Nested child exists on the graph but has NO _instances (Phase 1
        # leaves nested aggregated).
        assert "agent:writer" in fc._graph.nodes
        assert fc._graph.nodes["agent:writer"]._instances == {}

        sub = fc._running_subgraph()
        # The nested node should appear exactly once, under its
        # canonical id (no '#<tid>' suffix).
        writer_ids = [nid for nid in sub.nodes if nid.startswith("agent:writer")]
        assert writer_ids == ["agent:writer"]


# ---------------------------------------------------------------------
# cross-highlight
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_highlight_sets_base_id_not_virtual(tmp_path: Path) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None
        fc.add_event(_agent_use("executor", tid="tid-aaaaaa"))
        fc.add_event(_agent_use("executor", tid="tid-bbbbbb"))
        # Flip flowchart into running mode so the layout contains the
        # virtual instance ids.
        if fc.get_mode() != "running":
            fc.toggle_mode()
        await pilot.pause()

        virtual_ids = [
            nid for nid in fc._layout.nodes if nid.startswith("agent:executor#")
        ]
        assert virtual_ids, "expected running-mode to expand executor"

        # Simulate a click on the first virtual instance box.
        class _ClickEvent:
            def __init__(self, x: int, y: int) -> None:
                self.x = x
                self.y = y

        pos = fc._layout.nodes[virtual_ids[0]]
        fc.on_click(_ClickEvent(x=pos.col + 1, y=pos.row + 1))
        await pilot.pause()

        assert app.selected_agent_id == "agent:executor"


# ---------------------------------------------------------------------
# Phase 2a: per-instance tool_breakdown
# ---------------------------------------------------------------------


def _leaf_tool_use(tool: str, subagent_uuid: str) -> HarnessEvent:
    """Subagent-file leaf tool_use (Read/Edit/etc.) — no nested spawn."""
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": tool,
            "tool_use_id": f"leaf-{tool}",
            "subagent_uuid": subagent_uuid,
            "input": {},
        },
    )


def _user_msg(text: str = "next turn please") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": text},
    )


def test_instance_breakdown_updates_independently_per_instance() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    g.update_from_event(_agent_use("executor", tid="t2"))
    g.update_from_event(_result("t1", linked="u1"))
    g.update_from_event(_result("t2", linked="u2"))

    g.update_from_event(_leaf_tool_use("Read", "u1"))
    g.update_from_event(_leaf_tool_use("Read", "u1"))
    g.update_from_event(_leaf_tool_use("Edit", "u2"))

    node = g.nodes["agent:executor"]
    assert node._instances["t1"].tool_breakdown == {"Read": 2}
    assert node._instances["t2"].tool_breakdown == {"Edit": 1}
    assert node.tool_breakdown == {"Read": 2, "Edit": 1}


@pytest.mark.asyncio
async def test_running_subgraph_uses_instance_breakdown_not_node(
    tmp_path: Path,
) -> None:
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None
        fc.add_event(_agent_use("executor", tid="tid-aaaaaa"))
        fc.add_event(_agent_use("executor", tid="tid-bbbbbb"))
        fc.add_event(_result("tid-aaaaaa", linked="u1"))
        fc.add_event(_result("tid-bbbbbb", linked="u2"))
        fc.add_event(_leaf_tool_use("Read", "u1"))
        fc.add_event(_leaf_tool_use("Read", "u1"))
        fc.add_event(_leaf_tool_use("Edit", "u2"))
        await pilot.pause()

        sub = fc._running_subgraph()
        base_id = "agent:executor"
        vids = sorted(
            nid for nid in sub.nodes if nid.startswith(f"{base_id}#")
        )
        assert len(vids) == 2

        # Map virtual ids back to which instance they correspond to via
        # the suffix.  Find each virtual node by iterating for a key that
        # starts with the base_id + "#" prefix — no hardcoded slice length.
        def _find_vid(tid: str) -> str:
            for nid in sub.nodes:
                if nid.startswith(f"{base_id}#") and nid.endswith(tid[-8:]):
                    return nid
            raise KeyError(f"no virtual node for tid={tid!r}")

        vid_a = _find_vid("tid-aaaaaa")
        vid_b = _find_vid("tid-bbbbbb")
        assert sub.nodes[vid_a].tool_breakdown == {"Read": 2}
        assert sub.nodes[vid_b].tool_breakdown == {"Edit": 1}
        assert sub.nodes[vid_a].tool_breakdown != sub.nodes[vid_b].tool_breakdown
        # And both differ from the node-level aggregate.
        node_bd = fc._graph.nodes["agent:executor"].tool_breakdown
        assert node_bd == {"Read": 2, "Edit": 1}
        assert sub.nodes[vid_a].tool_breakdown != node_bd
        assert sub.nodes[vid_b].tool_breakdown != node_bd


def test_flush_clears_instance_index_but_keeps_node_breakdown() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    g.update_from_event(_result("t1", linked="u1"))
    g.update_from_event(_leaf_tool_use("Read", "u1"))
    g.update_from_event(_leaf_tool_use("Edit", "u1"))

    node = g.nodes["agent:executor"]
    assert node.tool_breakdown == {"Read": 1, "Edit": 1}
    assert node._instances["t1"].tool_breakdown == {"Read": 1, "Edit": 1}

    g.update_from_event(_user_msg("hello, new turn"))

    # Node-level aggregate is preserved across flush.
    assert g.nodes["agent:executor"].tool_breakdown == {"Read": 1, "Edit": 1}
    assert g.nodes["agent:executor"]._instances == {}
    assert g._subagent_uuid_to_instance == {}


def test_orphan_instance_breakdown_goes_to_node_only() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    # NO tool_result fired → no link → instance has no subagent_uuid.
    # But subagent tool_use events arrive anyway with some random uuid.
    # They should NOT route to the instance (no link) but the node still
    # needs the linked uuid registered for the leaf branch to fire. This
    # documents the orphan case where the instance lacks a back-reference
    # so subagent events bypass the per-instance update entirely.
    g._subagent_uuid_to_node["orphan-uuid"] = "agent:executor"
    g.update_from_event(_leaf_tool_use("Read", "orphan-uuid"))

    node = g.nodes["agent:executor"]
    assert node.tool_breakdown == {"Read": 1}
    assert node._instances["t1"].tool_breakdown == {}


def test_breakdown_cap_per_instance_independent_from_node() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    g.update_from_event(_result("t1", linked="u1"))

    for i in range(25):
        g.update_from_event(_leaf_tool_use(f"Tool{i:02d}", "u1"))

    node = g.nodes["agent:executor"]
    assert len(node.tool_breakdown) == MAX_BREAKDOWN_TOOLS == 20
    assert len(node._instances["t1"].tool_breakdown) == MAX_BREAKDOWN_TOOLS == 20


def test_second_flush_cycle_starts_clean_instance() -> None:
    """Second turn spawns a fresh instance with no tool_breakdown leak from
    the first turn, and stale subagent_uuid events after flush only update
    the node-level aggregate (orphan path)."""
    g = CallGraph()

    # --- Turn 1 ---
    g.update_from_event(_agent_use("executor", tid="t1"))
    g.update_from_event(_result("t1", linked="u1"))
    g.update_from_event(_leaf_tool_use("Read", "u1"))

    node = g.nodes["agent:executor"]
    assert node._instances["t1"].tool_breakdown == {"Read": 1}

    # Real user prompt flushes instances and the uuid→instance index.
    g.update_from_event(_user_msg("second turn"))

    assert node._instances == {}
    assert g._subagent_uuid_to_instance == {}

    # --- Turn 2 ---
    g.update_from_event(_agent_use("executor", tid="t2"))
    g.update_from_event(_result("t2", linked="u2"))
    g.update_from_event(_leaf_tool_use("Edit", "u2"))

    # Only t2 exists; t1 was cleared.
    assert list(node._instances.keys()) == ["t2"]
    assert node._instances["t2"].tool_breakdown == {"Edit": 1}

    # Feed a stale u1 event (uuid→instance map was cleared; u1 is NOT in
    # _subagent_uuid_to_instance, but the uuid→node map is still live so the
    # node-level aggregate still accumulates via the orphan branch).
    g.update_from_event(_leaf_tool_use("Read", "u1"))

    # Instance t2 must not be contaminated.
    assert node._instances["t2"].tool_breakdown == {"Edit": 1}

    # Node-level aggregate is cumulative: Read from turn 1 + stale Read + Edit.
    assert node.tool_breakdown == {"Read": 2, "Edit": 1}


def test_leaf_event_before_tool_result_goes_to_node_only() -> None:
    """Leaf events that arrive before the tool_result link is established
    update only the node-level breakdown (orphan degradation path).  Once
    the link is established subsequent events update both."""
    g = CallGraph()

    g.update_from_event(_agent_use("executor", tid="t1"))

    # Manually register the uuid→node mapping as the subagent watcher would,
    # but do NOT fire tool_result yet → _subagent_uuid_to_instance is empty.
    g._subagent_uuid_to_node["u1"] = "agent:executor"

    # First leaf event: link not yet established → node only.
    g.update_from_event(_leaf_tool_use("Read", "u1"))

    node = g.nodes["agent:executor"]
    assert node.tool_breakdown == {"Read": 1}
    assert node._instances["t1"].tool_breakdown == {}

    # Now fire tool_result to establish the instance link.
    g.update_from_event(_result("t1", linked="u1"))

    # Second leaf event: link is established → both node and instance.
    g.update_from_event(_leaf_tool_use("Read", "u1"))

    assert node.tool_breakdown == {"Read": 2}
    assert node._instances["t1"].tool_breakdown == {"Read": 1}


@pytest.mark.asyncio
async def test_virtual_node_tool_breakdown_copied_from_instance_object(
    tmp_path: Path,
) -> None:
    """Virtual nodes in _running_subgraph copy tool_breakdown from the
    Instance object, not from the node-level aggregate.  A 'ghost' tool
    that only exists in the node aggregate must NOT appear in either
    virtual node's breakdown."""
    app = HarnessVisualApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        fc = app._flowchart
        assert fc is not None

        fc.add_event(_agent_use("executor", tid="tid-aaaaaa"))
        fc.add_event(_agent_use("executor", tid="tid-bbbbbb"))
        fc.add_event(_result("tid-aaaaaa", linked="u1"))
        fc.add_event(_result("tid-bbbbbb", linked="u2"))

        # Instance-level breakdowns: distinct per instance.
        fc.add_event(_leaf_tool_use("Read", "u1"))
        fc.add_event(_leaf_tool_use("Edit", "u2"))

        # Inject a "ghost" tool into the node aggregate that neither instance
        # knows about — simulates cumulative history from a previous turn.
        node = fc._graph.nodes["agent:executor"]
        node.tool_breakdown["Ghost"] = 99

        await pilot.pause()

        sub = fc._running_subgraph()

        # Locate virtual nodes by prefix, without hardcoding slice lengths.
        base_id = "agent:executor"
        vids = [nid for nid in sub.nodes if nid.startswith(f"{base_id}#")]
        assert len(vids) == 2

        # Map suffix → breakdown for the two virtual nodes.
        bd_by_suffix = {
            nid.split("#", 1)[1]: sub.nodes[nid].tool_breakdown for nid in vids
        }

        # Derive suffixes from production logic (tid[-8:] when len >= 8).
        suffix_a = "tid-aaaaaa"[-8:]
        suffix_b = "tid-bbbbbb"[-8:]

        assert bd_by_suffix[suffix_a] == {"Read": 1}
        assert bd_by_suffix[suffix_b] == {"Edit": 1}

        # The ghost tool must NOT appear in either virtual node.
        for bd in bd_by_suffix.values():
            assert "Ghost" not in bd

        # Confirm the ghost IS present on the node aggregate (sanity check).
        assert node.tool_breakdown.get("Ghost") == 99
