"""Tests for subagent aggregation on CallGraph + parser linking."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import MAX_BREAKDOWN_TOOLS, ROOT_ID, CallGraph
from agentlens.parser import parse_line


def _agent_use(subagent: str, *, tid: str = "t1") -> HarnessEvent:
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
            "content": [{"type": "text", "text": f"agentId: {link} (use SendMessage ...)"}],
            "is_error": False,
            "linked_subagent_uuid": link,
        },
    )


def _sub_tool_use(tool: str, link: str, *, tid: str) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": tool,
            "tool_use_id": tid,
            "input": {},
            "subagent_uuid": link,
        },
    )


def test_main_tool_result_populates_subagent_uuid_and_link_map() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    g.update_from_event(_tool_result_with_link("t1", "a48d2d1088dd1be44"))
    node = g.nodes["agent:executor"]
    assert node.subagent_uuid == "a48d2d1088dd1be44"
    assert g._subagent_uuid_to_node["a48d2d1088dd1be44"] == "agent:executor"


def test_subagent_tool_use_increments_breakdown() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    g.update_from_event(_tool_result_with_link("t1", "sub123"))
    for i in range(3):
        assert g.update_from_event(_sub_tool_use("Read", "sub123", tid=f"r{i}")) is True
    g.update_from_event(_sub_tool_use("Edit", "sub123", tid="e1"))
    node = g.nodes["agent:executor"]
    assert node.tool_breakdown == {"Read": 3, "Edit": 1}
    # Subagent tool_use events must NOT create new graph nodes.
    assert "agent:Read" not in g.nodes
    assert "agent:Edit" not in g.nodes


def test_unknown_subagent_uuid_events_are_dropped() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    # No link established yet — drop silently.
    changed = g.update_from_event(_sub_tool_use("Read", "unknown", tid="r1"))
    assert changed is False
    assert g.nodes["agent:executor"].tool_breakdown == {}


def test_breakdown_cap_enforced() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    g.update_from_event(_tool_result_with_link("t1", "sub1"))
    for i in range(MAX_BREAKDOWN_TOOLS + 10):
        g.update_from_event(_sub_tool_use(f"Tool{i}", "sub1", tid=f"t{i}"))
    node = g.nodes["agent:executor"]
    assert len(node.tool_breakdown) == MAX_BREAKDOWN_TOOLS
    # But existing tools can keep incrementing past the cap.
    first_tool = next(iter(node.tool_breakdown))
    before = node.tool_breakdown[first_tool]
    g.update_from_event(_sub_tool_use(first_tool, "sub1", tid="x1"))
    assert node.tool_breakdown[first_tool] == before + 1


def test_get_subagent_tool_counts_returns_copy() -> None:
    g = CallGraph()
    g.update_from_event(_agent_use("executor", tid="t1"))
    g.update_from_event(_tool_result_with_link("t1", "sub1"))
    g.update_from_event(_sub_tool_use("Read", "sub1", tid="r1"))
    copy = g.get_subagent_tool_counts("agent:executor")
    assert copy == {"Read": 1}
    copy["Read"] = 999
    copy["NewTool"] = 42
    assert g.nodes["agent:executor"].tool_breakdown == {"Read": 1}


def test_parser_extracts_linked_subagent_uuid_from_tool_result() -> None:
    row = {
        "type": "user",
        "sessionId": "s",
        "timestamp": "2026-04-08T10:00:01Z",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_x",
                    "content": [
                        {
                            "type": "text",
                            "text": "agentId: a48d2d1088dd1be44 (use SendMessage with to: 'a48d2d1088dd1be44' ...)",
                        }
                    ],
                    "is_error": False,
                }
            ],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 1
    assert events[0].type == EventType.tool_result
    assert events[0].payload.get("linked_subagent_uuid") == "a48d2d1088dd1be44"
    assert events[0].linked_subagent_uuid == "a48d2d1088dd1be44"


def test_parser_stamps_subagent_uuid_on_subagent_file_rows() -> None:
    row = {
        "type": "assistant",
        "sessionId": "s",
        "agentId": "a48d2d1088dd1be44",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "u1",
        "isSidechain": True,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Read",
                    "input": {"file_path": "/tmp/x"},
                }
            ],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 1
    assert events[0].payload.get("subagent_uuid") == "a48d2d1088dd1be44"
    assert events[0].subagent_uuid == "a48d2d1088dd1be44"


def test_real_subagent_files_parse_without_error() -> None:
    """Smoke-test the real subagent JSONL files in the user's Claude
    project directory. If they're absent (CI), skip silently.
    """
    from pathlib import Path

    d = Path(
        "/Users/limdk/.claude/projects/-Users-limdk-Documents-workspace-harness-visual/"
        "b0709256-eb61-4ccb-9b57-49aaca263c33/subagents"
    )
    if not d.is_dir():
        return
    files = [p for p in d.iterdir() if p.name.startswith("agent-") and p.suffix == ".jsonl"]
    assert files, "expected at least one subagent file"
    for f in files[:5]:
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                # Must never raise.
                parse_line(ln)
