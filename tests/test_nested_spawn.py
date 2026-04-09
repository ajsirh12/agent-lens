"""Tests for nested subagent spawn tracking (Phase 1).

When a subagent spawns another Agent/Task/Skill inside its own JSONL
file, that nested call must appear as a child node on the flowchart
under the parent subagent's node — NOT aggregated into the parent's
tool_breakdown badge.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import (
    MAX_NESTED_DEPTH,
    ROOT_ID,
    CallGraph,
    Node,
)
from agentlens import graph_model as gm


def _agent_use(subagent: str, *, tid: str) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Agent",
            "tool_use_id": tid,
            "input": {"subagent_type": subagent},
        },
    )


def _tool_result_with_link(tid: str, link: str) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_use_id": tid,
            "content": [{"type": "text", "text": f"agentId: {link} ..."}],
            "is_error": False,
            "linked_subagent_uuid": link,
        },
    )


def _nested_agent_use(
    subagent: str, parent_link: str, *, tid: str
) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Agent",
            "tool_use_id": tid,
            "input": {"subagent_type": subagent},
            "subagent_uuid": parent_link,
        },
    )


def _nested_skill_use(
    skill: str, parent_link: str, *, tid: str
) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Skill",
            "tool_use_id": tid,
            "input": {"skill": skill},
            "subagent_uuid": parent_link,
        },
    )


def _spawn_chain_level(
    g: CallGraph,
    parent_link: str,
    child_name: str,
    *,
    tid: str,
    child_link: str,
) -> None:
    """Spawn an Agent from inside ``parent_link``'s file, then emit the
    matching tool_result so the new child's own subagent_uuid is
    registered. This lets us recurse into the child for deeper nesting.
    """
    g.update_from_event(_nested_agent_use(child_name, parent_link, tid=tid))
    # tool_result carrying linked_subagent_uuid=child_link. The result
    # event itself also carries subagent_uuid=parent_link because it's
    # emitted from the parent's JSONL file (the side where the Agent
    # tool_use lives).
    ev = HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_use_id": tid,
            "content": [{"type": "text", "text": f"agentId: {child_link}"}],
            "is_error": False,
            "linked_subagent_uuid": child_link,
            "subagent_uuid": parent_link,
        },
    )
    g.update_from_event(ev)


# ----------------------------------------------------------------------


def test_nested_agent_tool_use_creates_child_node() -> None:
    g = CallGraph()
    # main → planner
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_tool_result_with_link("t1", "planner-uuid"))
    # planner → architect (nested)
    changed = g.update_from_event(
        _nested_agent_use("architect", "planner-uuid", tid="t2")
    )
    assert changed is True
    assert "agent:architect" in g.nodes
    # Edge must be planner → architect, NOT main → architect.
    assert ("agent:planner", "agent:architect") in g.edges
    assert (ROOT_ID, "agent:architect") not in g.edges


def test_nested_skill_tool_use_creates_child_node() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_tool_result_with_link("t1", "planner-uuid"))
    changed = g.update_from_event(
        _nested_skill_use("code-review", "planner-uuid", tid="t2")
    )
    assert changed is True
    assert "skill:code-review" in g.nodes
    assert ("agent:planner", "skill:code-review") in g.edges


def test_depth_cap_5_drops_deeper_spawns() -> None:
    # main(0) → A(1) → B(2) → C(3) → D(4) → E(5) → F(6, dropped)
    g = CallGraph()
    g.update_from_event(_agent_use("A", tid="m1"))
    g.update_from_event(_tool_result_with_link("m1", "A-uuid"))
    _spawn_chain_level(g, "A-uuid", "B", tid="t2", child_link="B-uuid")
    _spawn_chain_level(g, "B-uuid", "C", tid="t3", child_link="C-uuid")
    _spawn_chain_level(g, "C-uuid", "D", tid="t4", child_link="D-uuid")
    _spawn_chain_level(g, "D-uuid", "E", tid="t5", child_link="E-uuid")
    # Sanity: A..E all exist, depths 1..5.
    for name in ("A", "B", "C", "D", "E"):
        assert f"agent:{name}" in g.nodes
    assert MAX_NESTED_DEPTH == 5
    # F would be depth 6 — must be dropped.
    changed = g.update_from_event(
        _nested_agent_use("F", "E-uuid", tid="t6")
    )
    assert changed is False
    assert "agent:F" not in g.nodes


def test_nested_spawn_not_in_breakdown() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_tool_result_with_link("t1", "planner-uuid"))
    g.update_from_event(
        _nested_agent_use("architect", "planner-uuid", tid="t2")
    )
    planner = g.nodes["agent:planner"]
    assert "Agent" not in planner.tool_breakdown
    assert "architect" not in planner.tool_breakdown
    assert "agent:architect" not in planner.tool_breakdown


def test_nested_spawn_respects_max_nodes_cap(monkeypatch) -> None:
    # Shrink the cap so the test doesn't have to create 500 nodes.
    monkeypatch.setattr(gm, "MAX_NODES", 2)
    g = CallGraph()
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_tool_result_with_link("t1", "planner-uuid"))
    # planner is 1 non-root node. Adding one more fills the cap.
    g.update_from_event(
        _nested_agent_use("architect", "planner-uuid", tid="t2")
    )
    assert "agent:architect" in g.nodes
    # Third nested spawn must be blocked by MAX_NODES.
    changed = g.update_from_event(
        _nested_agent_use("critic", "planner-uuid", tid="t3")
    )
    assert changed is False
    assert "agent:critic" not in g.nodes


def test_nested_spawn_adds_to_current_turn() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("planner", tid="t1"))
    g.update_from_event(_tool_result_with_link("t1", "planner-uuid"))
    g.update_from_event(
        _nested_agent_use("architect", "planner-uuid", tid="t2")
    )
    assert g.is_in_current_turn("agent:architect") is True


def test_nested_spawn_unknown_parent_drops() -> None:
    g = CallGraph()
    # Note: no main-session Agent + tool_result to seed the link map.
    changed = g.update_from_event(
        _nested_agent_use("architect", "ghost-uuid", tid="t1")
    )
    assert changed is False
    assert "agent:architect" not in g.nodes
