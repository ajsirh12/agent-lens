"""Unit tests for CallGraph (graph_model.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from harness_visual.events import EventType, HarnessEvent
from harness_visual.graph_model import MAX_NODES, ROOT_ID, CallGraph


def _task_use(subagent: str, *, parent: str | None = None, tid: str = "t1") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=parent,
        payload={
            "tool_name": "Task",
            "tool_use_id": tid,
            "input": {"subagent_type": subagent},
        },
    )


def _agent_use(subagent: str, *, parent: str | None = None, tid: str = "t1") -> HarnessEvent:
    # The current Claude Code harness emits tool_name="Agent" for subagent
    # spawns; historical sessions used tool_name="Task". Both carry a
    # `subagent_type` field in the input.
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=parent,
        payload={
            "tool_name": "Agent",
            "tool_use_id": tid,
            "input": {"subagent_type": subagent, "description": "x", "prompt": "y"},
        },
    )


def _skill_use(skill: str, *, parent: str | None = None, tid: str = "t1") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=parent,
        payload={
            "tool_name": "Skill",
            "tool_use_id": tid,
            "input": {"skill": skill},
        },
    )


def _result(tid: str, *, error: bool = False) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_use_id": tid, "is_error": error},
    )


def test_empty_graph_has_only_root() -> None:
    g = CallGraph()
    assert list(g.nodes.keys()) == [ROOT_ID]
    assert g.nodes[ROOT_ID].node_type == "root"
    assert g.edges == {}


def test_task_event_adds_child_node_and_edge() -> None:
    g = CallGraph()
    changed = g.update_from_event(_task_use("planner", tid="t1"))
    assert changed is True
    assert "agent:planner" in g.nodes
    assert g.nodes["agent:planner"].node_type == "agent"
    assert g.nodes["agent:planner"].label == "planner"
    assert (ROOT_ID, "agent:planner") in g.edges


def test_skill_event_adds_skill_prefixed_node() -> None:
    g = CallGraph()
    g.update_from_event(_skill_use("plan", tid="t1"))
    assert "skill:plan" in g.nodes
    assert g.nodes["skill:plan"].node_type == "skill"
    assert (ROOT_ID, "skill:plan") in g.edges


def test_agent_tool_name_is_accepted_same_as_task() -> None:
    """Current Claude Code harness emits tool_name='Agent' for subagent
    spawns. CallGraph must treat it identically to the historical 'Task'
    tool (same subagent_type field, same agent node shape).
    """
    g = CallGraph()
    changed = g.update_from_event(_agent_use("code-reviewer", tid="t1"))
    assert changed is True
    assert "agent:code-reviewer" in g.nodes
    assert g.nodes["agent:code-reviewer"].node_type == "agent"
    assert g.nodes["agent:code-reviewer"].label == "code-reviewer"
    assert (ROOT_ID, "agent:code-reviewer") in g.edges


def test_agent_and_task_collapse_to_same_node() -> None:
    """A subagent_type 'planner' invoked via Agent OR Task should become
    the SAME node (both map to 'agent:planner').
    """
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    g.update_from_event(_agent_use("planner", tid="t2"))
    assert len([n for n in g.nodes if n.startswith("agent:")]) == 1
    assert g.nodes["agent:planner"].call_count == 2


def test_duplicate_call_increments_call_count() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    g.update_from_event(_task_use("planner", tid="t2"))
    g.update_from_event(_task_use("planner", tid="t3"))
    assert len(g.nodes) == 2  # root + planner
    assert g.nodes["agent:planner"].call_count == 3
    assert g.edges[(ROOT_ID, "agent:planner")].count == 3


def test_tool_result_flips_status_to_done() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    assert g.nodes["agent:planner"].status == "running"
    g.update_from_event(_result("t1"))
    assert g.nodes["agent:planner"].status == "done"


def test_tool_result_with_error_sets_error_status() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    g.update_from_event(_result("t1", error=True))
    assert g.nodes["agent:planner"].status == "error"


def test_unknown_tool_name_is_ignored() -> None:
    g = CallGraph()
    ev = HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_name": "Bash", "tool_use_id": "t1", "input": {"command": "ls"}},
    )
    changed = g.update_from_event(ev)
    assert changed is False
    assert list(g.nodes.keys()) == [ROOT_ID]


def test_max_depth_root_only_is_zero() -> None:
    g = CallGraph()
    assert g.max_depth() == 0


def test_get_node_at_depth() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    g.update_from_event(_task_use("executor", tid="t2"))
    d0 = g.get_node_at_depth(0)
    d1 = g.get_node_at_depth(1)
    assert [n.id for n in d0] == [ROOT_ID]
    assert {n.id for n in d1} == {"agent:planner", "agent:executor"}


def test_parent_falls_back_to_root_when_agent_id_unknown() -> None:
    """A Task event with an agent_id that is not a known node should
    attach its child directly to root rather than synthesizing a ghost
    parent from the unknown UUID.
    """
    g = CallGraph()
    ev = _task_use("worker", parent="some-uuid-that-doesnt-exist", tid="t1")
    g.update_from_event(ev)
    # No ghost UUID node created.
    assert "some-uuid-that-doesnt-exist" not in g.nodes
    # Child is attached to root.
    assert (ROOT_ID, "agent:worker") in g.edges
    assert "agent:worker" in g.nodes


def test_four_parallel_tasks_all_branch_from_root() -> None:
    """The demo scenario: 4 Task events with unknown session UUIDs
    should all land as direct children of root.
    """
    g = CallGraph()
    for i, name in enumerate(["a", "b", "c", "d"]):
        g.update_from_event(_task_use(name, parent=f"sub:uuid-{i}", tid=f"t{i}"))
    # Root + 4 children, no ghost UUID nodes.
    assert len(g.nodes) == 5
    for name in ["a", "b", "c", "d"]:
        assert (ROOT_ID, f"agent:{name}") in g.edges


def test_agent_and_skill_with_same_name_are_distinct() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("debug", tid="t1"))
    g.update_from_event(_skill_use("debug", tid="t2"))
    assert "agent:debug" in g.nodes
    assert "skill:debug" in g.nodes
    assert g.nodes["agent:debug"].node_type == "agent"
    assert g.nodes["skill:debug"].node_type == "skill"


def test_node_cap_at_500_drops_overflow() -> None:
    g = CallGraph()
    for i in range(600):
        g.update_from_event(_task_use(f"agent{i}", tid=f"t{i}"))
    # Root + up to MAX_NODES.
    assert len(g.nodes) <= MAX_NODES + 1
    assert len(g.nodes) == MAX_NODES + 1


def test_label_sanitization() -> None:
    g = CallGraph()
    evil = "\x1bbad\nname" + "x" * 200
    g.update_from_event(_task_use(evil, tid="t1"))
    # Find the one non-root agent node.
    agent_nodes = [n for nid, n in g.nodes.items() if nid != ROOT_ID]
    assert len(agent_nodes) == 1
    label = agent_nodes[0].label
    assert len(label) <= 64
    # No control chars / escape sequences.
    assert "\x1b" not in label
    assert "\n" not in label
    assert "\r" not in label
    assert "\t" not in label
    # All printable.
    assert all(c.isprintable() for c in label)


def test_update_returns_false_when_nothing_changed() -> None:
    """A duplicate tool_result for an already-done node is a no-op."""
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    assert g.update_from_event(_result("t1")) is True  # running -> done
    assert g.update_from_event(_result("t1")) is False  # already done


def _user_message() -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": "next prompt"},
    )


def test_current_turn_marks_nodes_as_running_until_flushed() -> None:
    """A node that finishes (tool_result -> done) during the current turn
    should still be reported as in-current-turn, so the UI can keep
    showing it as 'running' until a user_message flushes the turn.
    """
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    assert g.is_in_current_turn("agent:planner") is True
    # Fast agent finishes immediately.
    g.update_from_event(_result("t1"))
    assert g.nodes["agent:planner"].status == "done"
    # Still in current turn until next user_message.
    assert g.is_in_current_turn("agent:planner") is True


def test_user_message_flushes_current_turn() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    g.update_from_event(_result("t1"))
    assert g.is_in_current_turn("agent:planner") is True
    changed = g.update_from_event(_user_message())
    assert changed is True
    assert g.is_in_current_turn("agent:planner") is False
    # Real status is preserved.
    assert g.nodes["agent:planner"].status == "done"


def test_user_message_noop_when_turn_empty() -> None:
    g = CallGraph()
    # No nodes in current turn — new user_message is a no-op.
    assert g.update_from_event(_user_message()) is False


def test_new_turn_after_flush_adds_new_nodes() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("a", tid="t1"))
    g.update_from_event(_result("t1"))
    g.update_from_event(_user_message())  # flush
    g.update_from_event(_task_use("b", tid="t2"))
    # Only the new node is in the current turn.
    assert g.is_in_current_turn("agent:a") is False
    assert g.is_in_current_turn("agent:b") is True


def _system_user_message(text: str, is_meta: bool = False) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": text, "is_meta": is_meta},
    )


def test_meta_user_message_does_not_flush_turn() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    assert g.is_in_current_turn("agent:planner") is True
    # Meta system message arrives — must NOT flush.
    g.update_from_event(_system_user_message("Base directory for this skill: /foo", is_meta=True))
    assert g.is_in_current_turn("agent:planner") is True


def test_task_notification_does_not_flush_turn() -> None:
    """Background task completion notifications come in as user_message
    events but are system-generated, not real user prompts.
    """
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    assert g.is_in_current_turn("agent:planner") is True
    g.update_from_event(_system_user_message("<task-notification>\n<task-id>abc</task-id>"))
    assert g.is_in_current_turn("agent:planner") is True


def test_system_reminder_does_not_flush_turn() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    g.update_from_event(_system_user_message("<system-reminder>The task tools haven't been used recently"))
    assert g.is_in_current_turn("agent:planner") is True


def test_real_user_prompt_still_flushes() -> None:
    g = CallGraph()
    g.update_from_event(_task_use("planner", tid="t1"))
    g.update_from_event(_system_user_message("Hello, please help me with this"))
    assert g.is_in_current_turn("agent:planner") is False


def test_subagent_user_message_does_not_flush_turn() -> None:
    """The initial prompt the main agent sent to a subagent appears as a
    user_message event in the subagent JSONL file. It carries a
    subagent_uuid payload and must NOT flush the main graph's turn —
    only actual user-typed prompts on the main session should.
    """
    g = CallGraph()
    g.update_from_event(_task_use("explore", tid="t1"))
    assert g.is_in_current_turn("agent:explore") is True
    # Simulate the subagent's own user row (its initial prompt from
    # the main agent). Has subagent_uuid set, text looks like a normal
    # prompt, is_meta=False.
    subagent_um = HarnessEvent(
        type=EventType.user_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "text": "List the directories under /some/path",
            "is_meta": False,
            "subagent_uuid": "a48d2d1088dd1be44",
        },
    )
    g.update_from_event(subagent_um)
    # Turn must be intact.
    assert g.is_in_current_turn("agent:explore") is True
