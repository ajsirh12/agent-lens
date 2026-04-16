"""Unit tests for CallGraph (graph_model.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import MAX_NODES, MAX_TOOL_EVENTS, ROOT_ID, CallGraph


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


# --- Token accumulation tests ---


def _assistant_usage(usage: dict, subagent_uuid: str | None = None) -> HarnessEvent:
    payload: dict = {"usage": usage}
    if subagent_uuid is not None:
        payload["subagent_uuid"] = subagent_uuid
    return HarnessEvent(
        type=EventType.assistant_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload=payload,
    )


def test__token_main_accumulate() -> None:
    """Two main assistant events in one turn accumulate into token_main and totals."""
    g = CallGraph()
    g.update_from_event(_user_message())  # creates turn index 0

    usage1 = {"input_tokens": 10, "output_tokens": 5,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    usage2 = {"input_tokens": 20, "output_tokens": 8,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    g.update_from_event(_assistant_usage(usage1))
    g.update_from_event(_assistant_usage(usage2))

    s = g.get_turn_summary(0)
    tt = s["token_total"]
    assert tt["input"] == 30
    assert tt["output"] == 13
    tm = s["token_main"]
    assert tm["input"] == 30
    assert tm["output"] == 13


def test__token_preturn_drop() -> None:
    """AC-12: assistant usage before any user_message (turn index < 0) is dropped."""
    g = CallGraph()
    # No user_message → _current_turn_index == -1
    usage = {"input_tokens": 50, "output_tokens": 10,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    changed = g.update_from_event(_assistant_usage(usage))
    # Event is silently dropped, no turns exist.
    assert changed is False
    assert g.get_turn_summary(0)["token_total"]["input"] == 0


def test__token_skill_span() -> None:
    """Assistant usage inside a skill tool_use/result span → skill node bucket."""
    g = CallGraph()
    g.update_from_event(_user_message())  # creates turn 0

    # Spawn a skill.
    skill_ev = HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_name": "Skill", "tool_use_id": "sk1", "input": {"skill": "myskill"}},
    )
    g.update_from_event(skill_ev)

    # Assistant usage that occurs within the skill span.
    usage = {"input_tokens": 100, "output_tokens": 40,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    g.update_from_event(_assistant_usage(usage))

    # Close the skill span.
    result_ev = HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_use_id": "sk1", "is_error": False},
    )
    g.update_from_event(result_ev)

    s = g.get_turn_summary(0)
    # Total tokens must include the skill usage.
    assert s["token_total"]["input"] == 100
    # token_nodes should contain the skill entry.
    node_labels = [n["label"] for n in s["token_nodes"]]
    assert "myskill" in node_labels


def test__unknown_subagent_drop() -> None:
    """assistant_message with an unknown subagent_uuid → silently dropped, no exception."""
    g = CallGraph()
    g.update_from_event(_user_message())
    usage = {"input_tokens": 5, "output_tokens": 2,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    # This subagent UUID is not registered in the graph.
    changed = g.update_from_event(_assistant_usage(usage, subagent_uuid="unknown-uuid-xyz"))
    assert changed is False
    # Main turn totals must remain zero — nothing was accumulated.
    s = g.get_turn_summary(0)
    assert s["token_total"]["input"] == 0


# --- Subagent token breakdown (skill-tree / standalone) ---


def _tool_result_linked(tid: str, linked_uuid: str) -> HarnessEvent:
    """tool_result that links an Agent node to its subagent JSONL UUID."""
    return HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_use_id": tid, "is_error": False,
                 "linked_subagent_uuid": linked_uuid},
    )


def _subagent_usage(subagent_uuid: str, usage: dict) -> HarnessEvent:
    """Simulates an assistant_message row from a subagent JSONL file."""
    return HarnessEvent(
        type=EventType.assistant_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"usage": usage, "subagent_uuid": subagent_uuid},
    )


def _make_usage(inp: int = 100, out: int = 40) -> dict:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def test_t_a1_agent_in_skill_span_records_agent_to_skill() -> None:
    """T-A1: Agent spawned while a skill span is open → _agent_to_skill mapped."""
    g = CallGraph()
    g.update_from_event(_user_message())  # turn 0

    # Open a skill span.
    skill_ev = HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_name": "Skill", "tool_use_id": "sk1", "input": {"skill": "ralplan"}},
    )
    g.update_from_event(skill_ev)

    # Spawn an agent while the skill span is open.
    g.update_from_event(_task_use("executor", tid="t1"))

    # _agent_to_skill must link the agent to the skill node.
    assert "agent:executor" in g._agent_to_skill
    assert g._agent_to_skill["agent:executor"] == "skill:ralplan"


def test_t_a2_agent_outside_skill_span_not_mapped() -> None:
    """T-A2: Agent spawned outside any skill span → not in _agent_to_skill."""
    g = CallGraph()
    g.update_from_event(_user_message())  # turn 0

    # Spawn agent — no skill span open.
    g.update_from_event(_task_use("planner", tid="t1"))

    assert "agent:planner" not in g._agent_to_skill


def test_t_a3_subagent_usage_in_skill_span_goes_to_skill_tree() -> None:
    """T-A3: Subagent that was spawned inside a skill span → skill_tree bucket."""
    g = CallGraph()
    g.update_from_event(_user_message())  # turn 0

    # Open skill span.
    g.update_from_event(HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_name": "Skill", "tool_use_id": "sk1", "input": {"skill": "myskill"}},
    ))

    # Spawn agent inside skill span.
    g.update_from_event(_task_use("worker", tid="t1"))

    # Link agent node to a subagent JSONL UUID via tool_result.
    g.update_from_event(_tool_result_linked("t1", linked_uuid="uuid-worker-1"))

    # Close skill span.
    g.update_from_event(_result("sk1"))

    # Subagent usage arrives asynchronously.
    g.update_from_event(_subagent_usage("uuid-worker-1", _make_usage(inp=200, out=80)))

    s = g.get_turn_summary(0)
    skill_tree = s["token_skill_tree"]
    assert len(skill_tree) == 1
    entry = skill_tree[0]
    assert entry["label"] == "myskill"
    agents_in_skill = entry["agents"]
    assert len(agents_in_skill) == 1
    assert agents_in_skill[0]["label"] == "worker"
    assert agents_in_skill[0]["tokens"]["input"] == 200
    assert agents_in_skill[0]["tokens"]["output"] == 80


def test_t_a4_subagent_usage_outside_skill_span_goes_to_standalone() -> None:
    """T-A4: Subagent spawned outside any skill span → standalone bucket."""
    g = CallGraph()
    g.update_from_event(_user_message())  # turn 0

    # Spawn agent outside any skill span.
    g.update_from_event(_task_use("lone-worker", tid="t1"))
    g.update_from_event(_tool_result_linked("t1", linked_uuid="uuid-lone-1"))

    # Subagent usage arrives.
    g.update_from_event(_subagent_usage("uuid-lone-1", _make_usage(inp=150, out=50)))

    s = g.get_turn_summary(0)
    standalone = s["token_agents_standalone"]
    assert len(standalone) == 1
    assert standalone[0]["label"] == "lone-worker"
    assert standalone[0]["tokens"]["input"] == 150
    assert standalone[0]["tokens"]["output"] == 50

    # skill_tree must be empty.
    assert s["token_skill_tree"] == []


def test_t_a5_skill_tree_total_not_in_token_total() -> None:
    """T-A5: skill_tree aggregate is display-only — not double-counted in token_total."""
    g = CallGraph()
    g.update_from_event(_user_message())  # turn 0

    # Open skill span.
    g.update_from_event(HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_name": "Skill", "tool_use_id": "sk1", "input": {"skill": "myskill"}},
    ))

    # Spawn agent inside skill span.
    g.update_from_event(_task_use("worker", tid="t1"))
    g.update_from_event(_tool_result_linked("t1", linked_uuid="uuid-w1"))

    # Close skill span.
    g.update_from_event(_result("sk1"))

    # Subagent sends 300 input tokens.
    g.update_from_event(_subagent_usage("uuid-w1", _make_usage(inp=300, out=100)))

    s = g.get_turn_summary(0)
    # token_total should be exactly the subagent's usage — no double count.
    assert s["token_total"]["input"] == 300
    assert s["token_total"]["output"] == 100

    # skill_tree's total bucket for the skill shows the same amount.
    skill_tree = s["token_skill_tree"]
    assert len(skill_tree) == 1
    tree_total = skill_tree[0]["total"]["tokens"]
    assert tree_total["input"] == 300  # display-only aggregate, equal but not additive


def test_t_a6_agent_to_skill_survives_turn_boundary() -> None:
    """T-A6: _agent_to_skill is NOT cleared on turn boundary (session-lifetime map)."""
    g = CallGraph()
    g.update_from_event(_user_message())  # turn 0

    # Open skill span, spawn agent, link.
    g.update_from_event(HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_name": "Skill", "tool_use_id": "sk1", "input": {"skill": "mypipeline"}},
    ))
    g.update_from_event(_task_use("subworker", tid="t1"))
    g.update_from_event(_result("t1"))
    g.update_from_event(_result("sk1"))

    # Flush to turn 1.
    g.update_from_event(_user_message())

    # _agent_to_skill mapping must still be present after turn flush.
    assert "agent:subworker" in g._agent_to_skill
    assert g._agent_to_skill["agent:subworker"] == "skill:mypipeline"


def test_t_a7_get_turn_summary_contains_new_keys() -> None:
    """T-A7: get_turn_summary() always includes token_skill_tree and token_agents_standalone keys."""
    g = CallGraph()
    g.update_from_event(_user_message())  # turn 0
    s = g.get_turn_summary(0)
    assert "token_skill_tree" in s
    assert "token_agents_standalone" in s
    # Empty turn → both are empty lists.
    assert s["token_skill_tree"] == []
    assert s["token_agents_standalone"] == []


# ---------------------------------------------------------------------------
# OMC team agent name-based linking (subagent_meta_link / _lazy_resolve)
# ---------------------------------------------------------------------------


def _omc_agent_use(
    name: str,
    *,
    tid: str = "t1",
    description: str = "",
) -> HarnessEvent:
    """Agent tool_use with OMC-style `name` field (no subagent_type)."""
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Agent",
            "tool_use_id": tid,
            "input": {
                "name": name,
                "description": description or f"{name}: test task",
                "prompt": "do work",
                "team_name": "test-team",
            },
        },
    )


def _meta_link(hex_id: str, agent_type: str) -> HarnessEvent:
    """subagent_meta_link event emitted by SubagentWatcherManager."""
    return HarnessEvent(
        type=EventType.subagent_meta_link,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"hex_id": hex_id, "agent_type": agent_type},
    )


def test_t_c1_omc_agent_uses_name_field_for_node_id() -> None:
    """T-C1: Agent tool_use with input.name creates node agent:<name>, not agent:general-purpose."""
    g = CallGraph()
    g.update_from_event(_user_message())
    g.update_from_event(_omc_agent_use("token-worker-a", tid="t1"))

    assert "agent:token-worker-a" in g.nodes
    assert "agent:general-purpose" not in g.nodes
    assert g.nodes["agent:token-worker-a"].label == "token-worker-a"


def test_t_c2_omc_agent_populates_name_to_node() -> None:
    """T-C2: Agent tool_use with input.name populates _name_to_node."""
    g = CallGraph()
    g.update_from_event(_user_message())
    g.update_from_event(_omc_agent_use("verifier-a", tid="t1"))

    assert g._name_to_node.get("verifier-a") == "agent:verifier-a"


def test_t_c3_meta_link_resolves_uuid_to_node_eagerly() -> None:
    """T-C3: subagent_meta_link resolves hex→node immediately when _name_to_node is already set."""
    g = CallGraph()
    g.update_from_event(_user_message())
    g.update_from_event(_omc_agent_use("verifier-b", tid="t1"))

    # meta_link arrives after the tool_use → eager resolution.
    g.update_from_event(_meta_link("bbbb1111cccc2222", "verifier-b"))

    assert g._subagent_uuid_to_node.get("bbbb1111cccc2222") == "agent:verifier-b"


def test_t_c4_meta_link_lazy_resolve_when_name_to_node_arrives_later() -> None:
    """T-C4: meta_link arrives before tool_use → lazy resolve on first assistant event."""
    g = CallGraph()
    g.update_from_event(_user_message())

    # meta_link arrives FIRST (file discovered before main JSONL processed).
    g.update_from_event(_meta_link("aaaa2222bbbb3333", "late-worker"))

    # Not yet resolved — _name_to_node is empty.
    assert g._subagent_uuid_to_node.get("aaaa2222bbbb3333") is None

    # Now the Agent tool_use is processed.
    g.update_from_event(_omc_agent_use("late-worker", tid="t1"))

    # First assistant_message triggers lazy resolve and caches the result.
    usage = _make_usage(inp=50, out=20)
    g.update_from_event(_subagent_usage("aaaa2222bbbb3333", usage))

    assert g._subagent_uuid_to_node.get("aaaa2222bbbb3333") == "agent:late-worker"
    turn = g.get_turns()[0]
    assert turn.input_tokens == 50


def test_t_c5_omc_agent_tokens_go_to_standalone_bucket() -> None:
    """T-C5: OMC team agent tokens appear in token_agents_standalone (no skill span)."""
    g = CallGraph()
    g.update_from_event(_user_message())
    g.update_from_event(_omc_agent_use("exec-agent", tid="t1"))
    g.update_from_event(_meta_link("exec1111aaaa2222", "exec-agent"))

    usage = _make_usage(inp=120, out=60)
    g.update_from_event(_subagent_usage("exec1111aaaa2222", usage))

    turn = g.get_turns()[0]
    assert "agent:exec-agent" in turn.token_agents_standalone
    assert turn.token_agents_standalone["agent:exec-agent"]["tokens"]["input"] == 120
    assert turn.input_tokens == 120  # Total includes agent


def test_t_c6_two_omc_agents_accumulate_independently() -> None:
    """T-C6: Two OMC team agents each get their own standalone bucket entry."""
    g = CallGraph()
    g.update_from_event(_user_message())
    g.update_from_event(_omc_agent_use("agent-alpha", tid="t1"))
    g.update_from_event(_omc_agent_use("agent-beta", tid="t2"))
    g.update_from_event(_meta_link("alpha111", "agent-alpha"))
    g.update_from_event(_meta_link("beta2222", "agent-beta"))

    g.update_from_event(_subagent_usage("alpha111", _make_usage(inp=100, out=40)))
    g.update_from_event(_subagent_usage("beta2222", _make_usage(inp=200, out=80)))

    turn = g.get_turns()[0]
    assert turn.token_agents_standalone["agent:agent-alpha"]["tokens"]["input"] == 100
    assert turn.token_agents_standalone["agent:agent-beta"]["tokens"]["input"] == 200
    assert turn.input_tokens == 300  # 100 + 200, no double-count


def test_t_c7_meta_link_missing_fields_is_noop() -> None:
    """T-C7: subagent_meta_link with missing hex_id or agent_type is silently ignored."""
    g = CallGraph()
    g.update_from_event(_user_message())

    # Missing agent_type.
    changed = g.update_from_event(HarnessEvent(
        type=EventType.subagent_meta_link,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"hex_id": "deadbeef1234"},
    ))
    assert changed is False

    # Missing hex_id.
    changed = g.update_from_event(HarnessEvent(
        type=EventType.subagent_meta_link,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"agent_type": "some-agent"},
    ))
    assert changed is False

    # Graph state should be clean.
    assert g._pending_hex_to_type == {}


def test_t_c8_subagent_type_still_works_for_native_agents() -> None:
    """T-C8: Native Claude Code agents (subagent_type field, no name) are unaffected."""
    g = CallGraph()
    g.update_from_event(_user_message())
    g.update_from_event(_agent_use("general-purpose", tid="t1"))

    assert "agent:general-purpose" in g.nodes
    # _name_to_node should not be populated (no input.name field).
    assert g._name_to_node == {}


# ---------------------------------------------------------------------------
# Tool events timeline (turn-summary-timeline feature)
# ---------------------------------------------------------------------------

def _tool_use_event(
    tool_name: str = "Bash",
    tid: str = "tid1",
    agent_id: str | None = None,
    ts: datetime | None = None,
) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=ts or datetime.now(timezone.utc),
        agent_id=agent_id,
        payload={"tool_name": tool_name, "tool_use_id": tid, "input": {"command": "ls"}},
    )


def _tool_result_event(
    tid: str = "tid1",
    error: bool = False,
    ts: datetime | None = None,
) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_result,
        ts=ts or datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_use_id": tid, "is_error": error},
    )


def test_tool_events_populated_on_tool_use() -> None:
    """tool_use event for a regular tool adds an entry to TurnRecord.tool_events."""
    g = CallGraph()
    g.update_from_event(_user_message())  # turn 0
    g.update_from_event(_tool_use_event("Read", tid="r1"))

    turns = g.get_turns()
    assert len(turns) > 0
    turn = turns[0]
    assert len(turn.tool_events) == 1
    evt = turn.tool_events[0]
    assert evt["name"] == "Read"
    assert evt["tool_use_id"] == "r1"
    assert evt["status"] == "running"
    assert evt["duration_ms"] is None
    assert evt["ts"] > 0


def test_tool_events_updated_on_tool_result() -> None:
    """Matching tool_result updates status to 'done' and computes duration_ms."""
    from datetime import timedelta

    g = CallGraph()
    g.update_from_event(_user_message())

    t_start = datetime.now(timezone.utc)
    t_end = t_start + timedelta(milliseconds=500)

    g.update_from_event(_tool_use_event("Bash", tid="b1", ts=t_start))
    g.update_from_event(_tool_result_event("b1", ts=t_end))

    turn = g.get_turns()[0]
    evt = turn.tool_events[0]
    assert evt["status"] == "done"
    assert evt["duration_ms"] is not None
    assert 400 <= evt["duration_ms"] <= 600  # ~500ms with float precision


def test_tool_events_error_status() -> None:
    """tool_result with is_error=True sets status to 'error'."""
    g = CallGraph()
    g.update_from_event(_user_message())
    g.update_from_event(_tool_use_event("Bash", tid="e1"))
    g.update_from_event(_tool_result_event("e1", error=True))

    turn = g.get_turns()[0]
    assert turn.tool_events[0]["status"] == "error"


def test_tool_events_cap_enforced() -> None:
    """201st tool_use event is rejected; tool_events length stays at MAX_TOOL_EVENTS."""
    g = CallGraph()
    g.update_from_event(_user_message())

    for i in range(MAX_TOOL_EVENTS + 1):
        g.update_from_event(_tool_use_event("Bash", tid=f"cap-{i}"))

    turn = g.get_turns()[0]
    assert len(turn.tool_events) == MAX_TOOL_EVENTS


def test_tool_events_agent_task_skill_excluded() -> None:
    """Agent, Task, and Skill tool_use events are NOT added to tool_events."""
    g = CallGraph()
    g.update_from_event(_user_message())

    g.update_from_event(_agent_use("planner", tid="a1"))
    g.update_from_event(_task_use("executor", tid="t1"))
    g.update_from_event(_skill_use("ralplan", tid="s1"))

    turn = g.get_turns()[0]
    assert len(turn.tool_events) == 0


def test_tool_events_mcp_included() -> None:
    """MCP tool_use events ARE included in tool_events."""
    g = CallGraph()
    g.update_from_event(_user_message())

    mcp_ev = HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "mcp__context7__get-docs",
            "tool_use_id": "mcp1",
            "is_mcp": True,
            "input": {},
        },
    )
    g.update_from_event(mcp_ev)

    turn = g.get_turns()[0]
    assert len(turn.tool_events) == 1
    assert turn.tool_events[0]["name"] == "mcp__context7__get-docs"


def test_tool_timeline_in_get_turn_summary() -> None:
    """get_turn_summary() includes tool_timeline key, sorted by timestamp."""
    from datetime import timedelta

    g = CallGraph()
    g.update_from_event(_user_message())

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=1)
    t2 = t0 + timedelta(seconds=2)

    # Insert in non-chronological order to verify sorting
    g.update_from_event(_tool_use_event("Edit", tid="e1", ts=t2))
    g.update_from_event(_tool_use_event("Read", tid="r1", ts=t0))
    g.update_from_event(_tool_use_event("Bash", tid="b1", ts=t1))

    s = g.get_turn_summary(0)
    assert "tool_timeline" in s
    tl = s["tool_timeline"]
    assert len(tl) == 3
    # Verify chronological ordering
    assert tl[0]["name"] == "Read"
    assert tl[1]["name"] == "Bash"
    assert tl[2]["name"] == "Edit"
    assert tl[0]["ts"] <= tl[1]["ts"] <= tl[2]["ts"]


def test_tool_timeline_fallback() -> None:
    """get_turn_summary() for nonexistent turn returns tool_timeline: []."""
    g = CallGraph()
    s = g.get_turn_summary(999)
    assert "tool_timeline" in s
    assert s["tool_timeline"] == []
