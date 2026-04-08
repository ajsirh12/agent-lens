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
from harness_visual.graph_model import ROOT_ID, CallGraph


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
