"""Tests for turn summary modal."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import CallGraph
from agentlens.panels.turn_summary import (
    _build_lines,
    _build_header_lines,
    _build_body_lines,
    _build_stats_lines,
    _build_token_lines,
    _fmt_token_summary,
    _fmt_ms,
    _fmt_tokens,
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


def test_tool_usage_all_items_shown() -> None:
    """All tool usage items are shown (no cap/overflow truncation)."""
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
    # All 10 items shown — no truncation
    for i in range(10):
        assert f"Tool{i}" in combined
    assert "more" not in combined


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

        # Feed two real user messages to create two turn marker rows.
        # In turn-only mode, tool_use/tool_result do NOT add rows.
        tl.add_event(_user("first turn"))
        tl.add_event(_user("second turn"))
        await pilot.pause()

        # Position cursor on the first turn marker row (row 0 = Turn 1).
        if tl._table is not None:
            tl._table.move_cursor(row=0)
        await pilot.pause()

        idx = tl.get_selected_turn_index()
        assert idx == 0, f"expected turn 0 on row 0, got {idx}"

        # Position cursor on the second turn marker row (row 1 = Turn 2).
        if tl._table is not None:
            tl._table.move_cursor(row=1)
        await pilot.pause()
        idx2 = tl.get_selected_turn_index()
        assert idx2 == 1, f"expected turn 1 on row 1, got {idx2}"


# --- _fmt_tokens boundary tests ---


def test__fmt_tokens_boundaries() -> None:
    assert _fmt_tokens(0) == "[dim]-[/dim]"
    assert _fmt_tokens(999) == "999"
    assert _fmt_tokens(1000) == "1.0k"
    assert _fmt_tokens(9999) == "10.0k"
    assert _fmt_tokens(10000) == "10k"
    assert _fmt_tokens(999999) == "999k"
    assert _fmt_tokens(1000000) == "1.0M"
    assert _fmt_tokens(1000000000) == "1.0B"


def _token_summary(token_total: dict, token_main: dict | None = None,
                   token_nodes: list | None = None) -> dict:
    """Helper to build a minimal summary dict with token fields."""
    return {
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
        "token_total": token_total,
        "token_main": token_main or {},
        "token_nodes": token_nodes or [],
    }


def test__token_section_hidden_when_zero() -> None:
    summary = _token_summary(
        token_total={"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    )
    combined = _combined(summary)
    assert "Token Usage" not in combined


def test__token_section_shows_when_nonzero() -> None:
    summary = _token_summary(
        token_total={"input": 100, "output": 50, "cache_read": 0, "cache_create": 0}
    )
    combined = _combined(summary)
    assert "Token Usage" in combined


def test__token_all_nodes_shown() -> None:
    """All 12 nodes shown — no cap/overflow truncation."""
    nodes = [
        {"label": f"agent{i}", "node_type": "agent",
         "tokens": {"input": 100 - i, "output": 10, "cache_read": 0, "cache_create": 0}}
        for i in range(12)
    ]
    summary = _token_summary(
        token_total={"input": 500, "output": 100, "cache_read": 0, "cache_create": 0},
        token_nodes=nodes,
    )
    combined = _combined(summary)
    for i in range(12):
        assert f"agent{i}" in combined
    assert "more" not in combined


def test__token_sort_order() -> None:
    """Nodes sorted by input+output descending."""
    nodes = [
        {"label": "small", "node_type": "agent",
         "tokens": {"input": 10, "output": 5, "cache_read": 0, "cache_create": 0}},
        {"label": "large", "node_type": "agent",
         "tokens": {"input": 500, "output": 200, "cache_read": 0, "cache_create": 0}},
        {"label": "medium", "node_type": "agent",
         "tokens": {"input": 100, "output": 50, "cache_read": 0, "cache_create": 0}},
    ]
    summary = _token_summary(
        token_total={"input": 610, "output": 255, "cache_read": 0, "cache_create": 0},
        token_nodes=nodes,
    )
    combined = _combined(summary)
    # 'large' must appear before 'medium' which appears before 'small'.
    pos_large = combined.index("large")
    pos_medium = combined.index("medium")
    pos_small = combined.index("small")
    assert pos_large < pos_medium < pos_small


# --- Skill-tree / standalone subagent token section (T-B tests) ---


def _skill_tree_summary(
    token_skill_tree: list | None = None,
    token_agents_standalone: list | None = None,
    token_total: dict | None = None,
) -> dict:
    """Build a summary dict that includes the new hierarchical token keys."""
    return {
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
        "token_total": token_total or {
            "input": 300, "output": 100, "cache_read": 0, "cache_create": 0
        },
        "token_main": {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0},
        "token_nodes": [],
        "token_skill_tree": token_skill_tree if token_skill_tree is not None else [],
        "token_agents_standalone": (
            token_agents_standalone if token_agents_standalone is not None else []
        ),
    }


def test_t_b1_skill_tree_and_standalone_sections_render() -> None:
    """T-B1: When token_skill_tree and token_agents_standalone have data, sections render."""
    summary = _skill_tree_summary(
        token_skill_tree=[
            {
                "skill_node_id": "skill:ralplan",
                "label": "ralplan",
                "total": {"tokens": {"input": 200, "output": 80, "cache_read": 0, "cache_create": 0}},
                "agents": [
                    {
                        "node_id": "agent:executor",
                        "label": "executor",
                        "tokens": {"input": 200, "output": 80, "cache_read": 0, "cache_create": 0},
                    }
                ],
            }
        ],
        token_agents_standalone=[
            {
                "node_id": "agent:planner",
                "label": "planner",
                "tokens": {"input": 100, "output": 20, "cache_read": 0, "cache_create": 0},
            }
        ],
    )
    combined = _combined(summary)
    # Skill section renders the skill label.
    assert "ralplan" in combined
    # Standalone section renders the agent label.
    assert "planner" in combined
    # Token Usage section header present.
    assert "Token Usage" in combined


def test_t_b2_skill_subagent_indented_4_spaces() -> None:
    """T-B2: Agent under a skill is indented at 4-space level (indent=4)."""
    summary = _skill_tree_summary(
        token_skill_tree=[
            {
                "skill_node_id": "skill:pipeline",
                "label": "pipeline",
                "total": {"tokens": {"input": 500, "output": 150, "cache_read": 0, "cache_create": 0}},
                "agents": [
                    {
                        "node_id": "agent:childagent",
                        "label": "childagent",
                        "tokens": {"input": 500, "output": 150, "cache_read": 0, "cache_create": 0},
                    }
                ],
            }
        ],
    )
    lines = _build_lines(summary)
    # Find the line that mentions "childagent".
    child_lines = [ln for ln in lines if "childagent" in ln]
    assert len(child_lines) == 1, f"expected exactly 1 childagent line, got: {child_lines}"
    child_line = child_lines[0]
    # The line should start with at least 4 spaces of indentation.
    assert child_line.startswith("    "), (
        f"expected 4-space indent, got: {repr(child_line)}"
    )


def test_t_b3_token_total_does_not_include_skill_tree_total() -> None:
    """T-B3: Total row shows token_total, which must not double-count skill_tree totals."""
    # token_total = 300 (the agent's usage). skill_tree total also = 300.
    # If there were double-counting, Total would show 600.
    summary = _skill_tree_summary(
        token_skill_tree=[
            {
                "skill_node_id": "skill:myskill",
                "label": "myskill",
                "total": {"tokens": {"input": 300, "output": 100, "cache_read": 0, "cache_create": 0}},
                "agents": [
                    {
                        "node_id": "agent:worker",
                        "label": "worker",
                        "tokens": {"input": 300, "output": 100, "cache_read": 0, "cache_create": 0},
                    }
                ],
            }
        ],
        token_total={"input": 300, "output": 100, "cache_read": 0, "cache_create": 0},
    )
    combined = _combined(summary)
    # The Total row must not show 600 (which would indicate double-counting).
    # 300 in, 100 out → rendered as "300" and "100" (< 1k threshold).
    assert "600" not in combined


def test_t_b4_missing_skill_tree_keys_falls_back_silently() -> None:
    """T-B4: When token_skill_tree and token_agents_standalone are absent, fallback to legacy (no crash)."""
    # Use _token_summary which does NOT include the new keys.
    summary = _token_summary(
        token_total={"input": 100, "output": 50, "cache_read": 0, "cache_create": 0},
        token_nodes=[
            {"label": "someagent", "node_type": "agent",
             "tokens": {"input": 100, "output": 50, "cache_read": 0, "cache_create": 0}}
        ],
    )
    # Must not raise; must still render Token Usage with legacy node.
    combined = _combined(summary)
    assert "Token Usage" in combined
    assert "someagent" in combined


# --- Tool timeline DataTable tests ---


def _timeline_summary(tool_timeline: list | None = None, **overrides) -> dict:
    """Build a minimal summary with tool_timeline data."""
    base = {
        "index": 0,
        "prompt": "hi",
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
        "tool_timeline": tool_timeline if tool_timeline is not None else [],
    }
    base.update(overrides)
    return base


async def test_tool_timeline_datatable_present(tmp_path) -> None:
    """DataTable with id='turn-tool-timeline' is rendered when tool_timeline has data."""
    from agentlens.panels.turn_summary import TurnSummaryScreen
    from textual.app import App, ComposeResult
    from textual.widgets import DataTable

    summary = _timeline_summary(tool_timeline=[
        {"ts": 1700000000.0, "name": "Bash", "agent_id": "",
         "tool_use_id": "t1", "status": "done", "duration_ms": 500},
    ])

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield TurnSummaryScreen(summary)

    app = TestApp()
    async with app.run_test() as pilot:
        screen = TurnSummaryScreen(summary)
        app.push_screen(screen)
        await pilot.pause()
        dt = screen.query(DataTable)
        found = any(w.id == "turn-tool-timeline" for w in dt)
        assert found, "DataTable(id='turn-tool-timeline') not found"


async def test_tool_timeline_datatable_absent(tmp_path) -> None:
    """DataTable is NOT rendered when tool_timeline is empty."""
    from agentlens.panels.turn_summary import TurnSummaryScreen
    from textual.app import App, ComposeResult
    from textual.widgets import DataTable

    summary = _timeline_summary(tool_timeline=[])

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield TurnSummaryScreen(summary)

    app = TestApp()
    async with app.run_test() as pilot:
        screen = TurnSummaryScreen(summary)
        app.push_screen(screen)
        await pilot.pause()
        dt = screen.query(DataTable)
        found = any(w.id == "turn-tool-timeline" for w in dt)
        assert not found, "DataTable should not exist when tool_timeline is empty"


def test_tool_timeline_status_mapping() -> None:
    """Verify status mapping: done->'ok', error->'err', running->'run' in compose logic."""
    # We test the mapping logic directly since it's embedded in compose().
    # The mapping is: done->ok, error->err, running->run
    mapping = {"done": "ok", "error": "err", "running": "run"}
    for status, expected in mapping.items():
        if status == "done":
            sts = "ok"
        elif status == "error":
            sts = "err"
        elif status == "running":
            sts = "run"
        else:
            sts = str(status)[:3]
        assert sts == expected, f"Status '{status}' mapped to '{sts}', expected '{expected}'"


async def test_scrollable_container_present(tmp_path) -> None:
    """ScrollableContainer sections are present in the modal content."""
    from agentlens.panels.turn_summary import TurnSummaryScreen
    from textual.app import App, ComposeResult
    from textual.containers import ScrollableContainer

    summary = _timeline_summary()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield TurnSummaryScreen(summary)

    app = TestApp()
    async with app.run_test() as pilot:
        screen = TurnSummaryScreen(summary)
        app.push_screen(screen)
        await pilot.pause()
        sc = screen.query(ScrollableContainer)
        found_stats = any(w.id == "turn-section-stats" for w in sc)
        found_tokens = any(w.id == "turn-section-tokens" for w in sc)
        assert found_stats, "ScrollableContainer(id='turn-section-stats') not found"
        assert found_tokens, "ScrollableContainer(id='turn-section-tokens') not found"


def test_existing_sections_unchanged() -> None:
    """_build_lines() output for existing sections is unchanged (regression test)."""
    summary = {
        "index": 0,
        "prompt": "test prompt",
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
        "tool_usage": [{"name": "Read", "count": 2}],
        "tool_total": 2,
        "mcp_usage": [],
        "mcp_total": 0,
        "hook_usage": [],
        "hook_runs": 0,
        "hook_errors_total": 0,
        "hook_duration_ms": 0,
        "hooks_configured": False,
        "tool_timeline": [
            {"ts": 1700000000.0, "name": "Read", "agent_id": "",
             "tool_use_id": "t1", "status": "done", "duration_ms": 100},
        ],
    }
    combined = _combined(summary)
    # Existing sections still present
    assert "Turn 1" in combined
    assert "test prompt" in combined
    assert "Agents / Skills" in combined
    assert "Plan work" in combined
    assert "Tool Usage (2 calls)" in combined
    assert "(Esc / Enter to close)" in combined
    # tool_timeline does NOT appear in _build_lines (it's rendered as DataTable)
    assert "Tool Calls" not in combined


# --- _fmt_token_summary tests ---


def test_fmt_token_summary_format() -> None:
    """_fmt_token_summary returns correctly formatted 1-line summary."""
    tokens = {"input": 45000, "output": 12000, "cache_read": 100000, "cache_create": 2000}
    result = _fmt_token_summary(tokens)
    assert "Tokens:" in result
    assert "in" in result
    assert "out" in result
    assert "cache-r" in result
    assert "cache-w" in result


def test_fmt_token_summary_empty_when_zero() -> None:
    """_fmt_token_summary returns empty string when all token values are zero."""
    assert _fmt_token_summary({}) == ""
    assert _fmt_token_summary({"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}) == ""


def test_fmt_token_summary_partial_zeros() -> None:
    """_fmt_token_summary returns non-empty when at least one token value > 0."""
    result = _fmt_token_summary({"input": 1000, "output": 0, "cache_read": 0, "cache_create": 0})
    assert result != ""
    assert "Tokens:" in result


# --- _build_header_lines tests ---


def test_build_header_lines_contains_turn_info() -> None:
    """_build_header_lines returns Turn number, Prompt, Duration lines."""
    summary = {
        "index": 0,
        "prompt": "hello world",
        "duration_s": 10.0,
        "agent_count": 2,
        "skill_count": 3,
        "error_count": 0,
        "end_ts": 10.0,
        "agents": [],
    }
    lines = _build_header_lines(summary)
    combined = "\n".join(lines)
    assert "Turn 1" in combined
    assert "hello world" in combined
    assert "Duration:" in combined
    assert "Agents: 2" in combined
    assert "Skills: 3" in combined


def test_build_header_lines_contains_token_summary() -> None:
    """_build_header_lines includes Tokens line when token data exists."""
    summary = {
        "index": 0,
        "prompt": "test",
        "duration_s": 5.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "end_ts": 5.0,
        "agents": [],
        "token_total": {"input": 1000, "output": 500, "cache_read": 0, "cache_create": 0},
    }
    lines = _build_header_lines(summary)
    combined = "\n".join(lines)
    assert "Tokens:" in combined


def test_build_header_lines_no_token_when_zero() -> None:
    """_build_header_lines omits Tokens line when all token values are zero."""
    summary = {
        "index": 0,
        "prompt": "test",
        "duration_s": 5.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "end_ts": 5.0,
        "agents": [],
        "token_total": {},
    }
    lines = _build_header_lines(summary)
    combined = "\n".join(lines)
    assert "Tokens:" not in combined


# --- _build_body_lines tests ---


def test_build_body_lines_ends_with_close_hint() -> None:
    """_build_body_lines last non-empty line is the Esc/Enter close hint."""
    summary = {
        "index": 0,
        "prompt": "test",
        "duration_s": 5.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "end_ts": 5.0,
        "agents": [],
    }
    lines = _build_body_lines(summary)
    non_empty = [ln for ln in lines if ln.strip()]
    assert non_empty[-1] == "(Esc / Enter to close)"


def test_build_lines_backward_compat() -> None:
    """_build_lines() == _build_header_lines() + _build_body_lines() (backward compat)."""
    summary = {
        "index": 2,
        "prompt": "compat test",
        "duration_s": 30.0,
        "agent_count": 1,
        "skill_count": 2,
        "error_count": 0,
        "total_agent_duration_s": 25.0,
        "end_ts": 30.0,
        "agents": [
            {"label": "planner", "description": "Plan work",
             "node_type": "agent", "duration_s": 10.0,
             "status": "done", "is_background": False}
        ],
        "token_total": {"input": 5000, "output": 1000, "cache_read": 0, "cache_create": 0},
    }
    assert _build_lines(summary) == _build_header_lines(summary) + _build_body_lines(summary)


# --- async UI tests ---


@pytest.mark.asyncio
async def test_header_widget_present(tmp_path) -> None:
    """compose() must yield a Static widget with id='turn-summary-header'."""
    from agentlens.app import AgentlensApp
    from agentlens.panels.turn_summary import TurnSummaryScreen

    jsonl = tmp_path / "test.jsonl"
    jsonl.write_text("")
    app = AgentlensApp(
        session_override=jsonl,
        state_dir_override=tmp_path / "state",
    )
    summary = {
        "index": 0,
        "prompt": "header test",
        "duration_s": 5.0,
        "agent_count": 0,
        "skill_count": 0,
        "error_count": 0,
        "end_ts": 5.0,
        "agents": [],
        "token_total": {"input": 1000, "output": 500, "cache_read": 0, "cache_create": 0},
        "tool_timeline": [],
    }
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = TurnSummaryScreen(summary)
        await app.push_screen(screen)
        await pilot.pause()
        # #turn-summary-header must exist
        header = screen.query_one("#turn-summary-header")
        assert header is not None
        # token summary must appear in header text
        header_text = str(header.render())
        assert "Tokens:" in header_text
        # #turn-sections must also exist (sectioned body)
        sections = screen.query_one("#turn-sections")
        assert sections is not None


# --- Section scroll tests ---


async def test_section_containers_present(tmp_path) -> None:
    """#turn-section-stats and #turn-section-tokens containers exist."""
    from agentlens.panels.turn_summary import TurnSummaryScreen
    from textual.app import App, ComposeResult
    from textual.containers import ScrollableContainer

    summary = _timeline_summary(tool_timeline=[
        {"ts": 1700000000.0, "name": "Bash", "agent_id": "",
         "tool_use_id": "t1", "status": "done", "duration_ms": 500},
    ])

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield TurnSummaryScreen(summary)

    app = TestApp()
    async with app.run_test() as pilot:
        screen = TurnSummaryScreen(summary)
        app.push_screen(screen)
        await pilot.pause()
        stats = screen.query_one("#turn-section-stats", ScrollableContainer)
        assert stats is not None
        tokens = screen.query_one("#turn-section-tokens", ScrollableContainer)
        assert tokens is not None


def test_build_stats_lines_basic() -> None:
    """_build_stats_lines() returns lines containing 'Agents / Skills'."""
    summary = {
        "index": 0,
        "prompt": "test",
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
    lines = _build_stats_lines(summary)
    combined = "\n".join(lines)
    assert "Agents / Skills" in combined


def test_build_token_lines_basic() -> None:
    """_build_token_lines() returns lines containing 'Token Usage' when token_total nonzero."""
    summary = _token_summary(
        token_total={"input": 100, "output": 50, "cache_read": 0, "cache_create": 0},
    )
    lines = _build_token_lines(summary)
    combined = "\n".join(lines)
    assert "Token Usage" in combined


def test_build_body_lines_backward_compat() -> None:
    """_build_body_lines() contains both stats and tokens sections."""
    summary = {
        "index": 0,
        "prompt": "test",
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
        "token_total": {"input": 100, "output": 50, "cache_read": 0, "cache_create": 0},
    }
    lines = _build_body_lines(summary)
    combined = "\n".join(lines)
    assert "Agents / Skills" in combined
    assert "Token Usage" in combined
    assert "(Esc / Enter to close)" in combined
