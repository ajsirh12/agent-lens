"""Tests for turn summary modal."""

from __future__ import annotations

from datetime import datetime, timezone

from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import CallGraph
from agentlens.panels.turn_summary import (
    _build_lines,
    _fmt_ms,
    _mcp_display,
    _trunc,
)


def _user(text: str = "hi") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": text},
    )


def _agent(subagent: str, tid: str, desc: str = "") -> HarnessEvent:
    inp: dict = {"subagent_type": subagent}
    if desc:
        inp["description"] = desc
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_name": "Agent", "tool_use_id": tid, "input": inp},
    )


def _result(tid: str, error: bool = False) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_use_id": tid, "is_error": error},
    )


def test_get_turn_summary_empty() -> None:
    g = CallGraph()
    s = g.get_turn_summary(0)
    assert s["index"] == 0
    assert s["agent_count"] == 0
    assert s["agents"] == []


def test_get_turn_summary_with_agents() -> None:
    g = CallGraph()
    g.update_from_event(_user("turn 0"))
    g.update_from_event(_agent("planner", "t1", desc="Plan the work"))
    g.update_from_event(_result("t1"))
    g.update_from_event(_agent("executor", "t2"))
    g.update_from_event(_result("t2", error=True))

    s = g.get_turn_summary(0)
    assert s["index"] == 0
    assert s["agent_count"] == 2
    assert s["skill_count"] == 0
    assert s["error_count"] == 1
    assert len(s["agents"]) == 2
    # First agent has description, second doesn't
    assert s["agents"][0]["description"] == "Plan the work"
    assert s["agents"][1]["description"] == ""
    assert s["agents"][1]["status"] == "error"


def test_get_turn_summary_skill_count() -> None:
    g = CallGraph()
    g.update_from_event(_user("turn 0"))
    # Spawn a skill
    skill_ev = HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Skill",
            "tool_use_id": "s1",
            "input": {"skill": "ralplan"},
        },
    )
    g.update_from_event(skill_ev)
    g.update_from_event(_result("s1"))

    s = g.get_turn_summary(0)
    assert s["skill_count"] == 1
    assert s["agent_count"] == 0


def test_get_turn_summary_live_turn_has_none_end_ts() -> None:
    g = CallGraph()
    g.update_from_event(_user("turn 0"))
    g.update_from_event(_agent("planner", "t1"))
    g.update_from_event(_result("t1"))

    s = g.get_turn_summary(0)
    # Current turn not yet closed → end_ts is None
    assert s["end_ts"] is None
    # Duration can still be computed from latest agent completion
    assert s["duration_s"] >= 0.0


def test_get_turn_summary_nonexistent_turn() -> None:
    g = CallGraph()
    s = g.get_turn_summary(99)
    # Never raises, returns empty summary
    assert s["agent_count"] == 0
    assert s["agents"] == []


# --- Helper function unit tests ---


def test_trunc_short_string_unchanged() -> None:
    assert _trunc("Read", 14) == "Read"


def test_trunc_exact_width_unchanged() -> None:
    assert _trunc("a" * 14, 14) == "a" * 14


def test_trunc_long_string_truncated() -> None:
    result = _trunc("a" * 20, 14)
    assert len(result) == 14
    assert result.endswith("\u2026")


def test_fmt_ms_zero() -> None:
    assert _fmt_ms(0) == "-"


def test_fmt_ms_negative() -> None:
    assert _fmt_ms(-100) == "-"


def test_fmt_ms_milliseconds() -> None:
    assert _fmt_ms(500) == "500ms"


def test_fmt_ms_seconds() -> None:
    assert _fmt_ms(2500) == "2.5s"


def test_fmt_ms_minutes() -> None:
    result = _fmt_ms(90000)
    assert result.startswith("1m")


def test_mcp_display_short() -> None:
    result = _mcp_display("context7", "get-library-docs")
    assert result == "context7\u00b7get-library-docs"


def test_mcp_display_truncation() -> None:
    long_server = "a" * 30
    long_tool = "b" * 20
    result = _mcp_display(long_server, long_tool)
    assert len(result) == 38
    assert result.endswith("\u2026")


# --- TurnSummaryScreen compose tests ---


def _combined(summary: dict) -> str:
    """Return all panel lines joined for easy assertion."""
    return "\n".join(_build_lines(summary))


def test_tool_usage_section_present_when_data_exists() -> None:
    summary = {
        "index": 0,
        "prompt": "hi",
        "duration_s": 1.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": 1.0,
        "agents": [],
        "tool_usage": [
            {"name": "Read", "count": 5},
            {"name": "Edit", "count": 3},
        ],
        "tool_total": 8,
        "mcp_usage": [],
        "mcp_total": 0,
        "hook_usage": [],
        "hook_runs": 0,
        "hook_errors_total": 0,
        "hook_duration_ms": 0,
        "hooks_configured": False,
    }
    combined = _combined(summary)
    assert "Tool Usage (8 calls)" in combined
    assert "Read" in combined
    assert "Edit" in combined


def test_tool_usage_section_absent_when_empty() -> None:
    summary = {
        "index": 0,
        "prompt": "",
        "duration_s": 0.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": None,
        "agents": [],
        "tool_usage": [],
        "tool_total": 0,
        "mcp_usage": [],
        "mcp_total": 0,
        "hook_usage": [],
        "hook_runs": 0,
        "hook_errors_total": 0,
        "hook_duration_ms": 0,
        "hooks_configured": False,
    }
    combined = _combined(summary)
    assert "Tool Usage" not in combined


def test_tool_usage_overflow_row() -> None:
    items = [{"name": f"Tool{i}", "count": 10 - i} for i in range(10)]
    summary = {
        "index": 0,
        "prompt": "",
        "duration_s": 0.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": 1.0,
        "agents": [],
        "tool_usage": items,
        "tool_total": 55,
        "mcp_usage": [],
        "mcp_total": 0,
        "hook_usage": [],
        "hook_runs": 0,
        "hook_errors_total": 0,
        "hook_duration_ms": 0,
        "hooks_configured": False,
    }
    combined = _combined(summary)
    assert "+2 more" in combined


def test_mcp_section_present_when_data_exists() -> None:
    summary = {
        "index": 0,
        "prompt": "",
        "duration_s": 1.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": 1.0,
        "agents": [],
        "tool_usage": [],
        "tool_total": 0,
        "mcp_usage": [
            {"server": "context7", "tool": "get-library-docs",
             "full_name": "mcp__context7__get-library-docs", "count": 2},
        ],
        "mcp_total": 2,
        "hook_usage": [],
        "hook_runs": 0,
        "hook_errors_total": 0,
        "hook_duration_ms": 0,
        "hooks_configured": False,
    }
    combined = _combined(summary)
    assert "MCP (2 calls)" in combined
    assert "context7\u00b7get-library-docs" in combined


def test_mcp_section_absent_when_empty() -> None:
    summary = {
        "index": 0,
        "prompt": "",
        "duration_s": 0.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": None,
        "agents": [],
        "tool_usage": [],
        "tool_total": 0,
        "mcp_usage": [],
        "mcp_total": 0,
        "hook_usage": [],
        "hook_runs": 0,
        "hook_errors_total": 0,
        "hook_duration_ms": 0,
        "hooks_configured": False,
    }
    combined = _combined(summary)
    assert "MCP" not in combined


def test_hooks_section_present_when_runs_exist() -> None:
    summary = {
        "index": 0,
        "prompt": "",
        "duration_s": 1.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": 1.0,
        "agents": [],
        "tool_usage": [],
        "tool_total": 0,
        "mcp_usage": [],
        "mcp_total": 0,
        "hook_usage": [
            {"event": "PostToolUse", "script": "check.sh",
             "count": 2, "error_count": 0, "total_ms": 100},
        ],
        "hook_runs": 2,
        "hook_errors_total": 0,
        "hook_duration_ms": 100,
        "hooks_configured": True,
    }
    combined = _combined(summary)
    assert "Hooks (2 events" in combined
    assert "PostToolUse" in combined
    assert "check.sh" in combined


def test_hooks_section_dim_when_configured_but_no_runs() -> None:
    summary = {
        "index": 0,
        "prompt": "",
        "duration_s": 1.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": 1.0,
        "agents": [],
        "tool_usage": [],
        "tool_total": 0,
        "mcp_usage": [],
        "mcp_total": 0,
        "hook_usage": [],
        "hook_runs": 0,
        "hook_errors_total": 0,
        "hook_duration_ms": 0,
        "hooks_configured": True,
    }
    combined = _combined(summary)
    assert "Hooks" in combined
    assert "no hook fired this turn" in combined


def test_hooks_section_absent_when_not_configured() -> None:
    summary = {
        "index": 0,
        "prompt": "",
        "duration_s": 1.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": 1.0,
        "agents": [],
        "tool_usage": [],
        "tool_total": 0,
        "mcp_usage": [],
        "mcp_total": 0,
        "hook_usage": [],
        "hook_runs": 0,
        "hook_errors_total": 0,
        "hook_duration_ms": 0,
        "hooks_configured": False,
    }
    combined = _combined(summary)
    assert "Hooks" not in combined


def test_hooks_error_badge_shown() -> None:
    summary = {
        "index": 0,
        "prompt": "",
        "duration_s": 1.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": 1.0,
        "agents": [],
        "tool_usage": [],
        "tool_total": 0,
        "mcp_usage": [],
        "mcp_total": 0,
        "hook_usage": [
            {"event": "Stop", "script": "cleanup.sh",
             "count": 3, "error_count": 1, "total_ms": 200},
        ],
        "hook_runs": 3,
        "hook_errors_total": 1,
        "hook_duration_ms": 200,
        "hooks_configured": True,
    }
    combined = _combined(summary)
    assert "[red]" in combined
    assert "1 errors" in combined


def test_hooks_section_absent_when_hooks_configured_missing() -> None:
    """If hooks_configured key absent, section is suppressed (conservative)."""
    summary = {
        "index": 0,
        "prompt": "",
        "duration_s": 0.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 0.0,
        "end_ts": None,
        "agents": [],
        # no hook keys at all — old data
    }
    combined = _combined(summary)
    assert "Hooks" not in combined


def test_summary_missing_new_keys_no_crash() -> None:
    """Panel must not crash on old summary dicts (never-raise)."""
    summary = {
        "index": 0,
        "prompt": "old turn",
        "duration_s": 5.0,
        "agent_count": 1,
        "skill_count": 0,
        "error_count": 0,
        "total_agent_duration_s": 3.0,
        "end_ts": 5.0,
        "agents": [
            {"label": "planner", "description": "Plan work",
             "node_type": "agent", "duration_s": 3.0,
             "status": "done", "is_background": False}
        ],
    }
    # Should not raise
    combined = _combined(summary)
    assert "Turn 1" in combined


async def test_get_selected_turn_index_detects_turn_marker(tmp_path) -> None:
    """Pressing Enter on a turn marker row must return its turn index."""
    from agentlens.app import AgentlensApp

    jsonl = tmp_path / "test.jsonl"
    jsonl.write_text("")
    app = AgentlensApp(
        session_override=jsonl,
        state_dir_override=tmp_path / "state",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        tl = app._timeline
        fc = app._flowchart
        assert tl is not None and fc is not None

        # Feed a real user message to create a turn marker row.
        tl.add_event(_user("first turn"))
        tl.add_event(_agent("planner", "t1"))
        tl.add_event(_result("t1"))
        await pilot.pause()

        # Position cursor on the turn marker row (the first row).
        if tl._table is not None:
            tl._table.move_cursor(row=0)
        await pilot.pause()

        idx = tl.get_selected_turn_index()
        assert idx == 0, f"expected turn 0 on turn marker, got {idx}"

        # Cursor on a non-turn row returns None.
        if tl._table is not None:
            tl._table.move_cursor(row=1)
        await pilot.pause()
        idx2 = tl.get_selected_turn_index()
        assert idx2 is None, f"expected None on non-turn row, got {idx2}"
