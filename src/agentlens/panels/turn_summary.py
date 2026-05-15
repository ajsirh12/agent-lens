"""TurnSummaryScreen — modal showing aggregate stats for a single turn.

Triggered by pressing Enter on a turn marker row in the Timeline.
Lists agent/skill invocations with their duration and status, total
turn duration, error count, and the user prompt preview.
"""

from __future__ import annotations

import os
from typing import Any

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Static

_TOP_N_TOOL = 8
_TOP_N_MCP = 6
_TOP_N_HOOK = 5
_TOP_N_SKILL = 5
_TOP_N_AGENT = 8
_TOP_N_TOKEN = 10  # legacy token_nodes fallback
_TOP_N_TIMELINE = 200  # max rows in the tool timeline DataTable
_PROMPT_MAX_LEN = 10_000  # prompt 전용 표시 상한 (_sanitize()의 500자 캡과 별도)


def _sanitize(s: object) -> str:
    text = str(s)
    text = "".join(c for c in text if (c.isprintable() or c == "\t") and c not in "\x1b\r")
    return text[:500]


def _sanitize_prompt(text: str) -> str:
    """prompt 전용 sanitize: ANSI/제어문자 제거, 10,000자 캡. _sanitize() 미변경."""
    cleaned = "".join(
        c for c in str(text or "")
        if (c.isprintable() or c in "\t\n") and c not in "\x1b\r"
    )
    return cleaned[:_PROMPT_MAX_LEN]


def _fmt_dur(seconds: float) -> str:
    if seconds <= 0:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}m{s:.0f}s"


def _trunc(text: str, width: int) -> str:
    """Truncate to width chars, appending ellipsis if needed."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "\u2026"


def _fmt_ms(ms: int) -> str:
    """Format milliseconds as human-readable duration."""
    if ms <= 0:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    s = ms / 1000.0
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    return f"{m}m{s - m * 60:.0f}s"


def _mcp_display(server: str, tool: str) -> str:
    """Format server\xb7tool, truncating to 38 chars."""
    combined = f"{server}\u00b7{tool}"
    return _trunc(combined, 38)


def _fmt_tokens(n: int) -> str:
    n = max(0, int(n))
    if n == 0:
        return "[dim]-[/dim]"
    if n < 1_000:
        return str(n)
    if n < 10_000:
        return f"{n/1000:.1f}k"
    if n < 1_000_000:
        return f"{n//1000}k"
    if n < 1_000_000_000:
        return f"{n/1_000_000:.1f}M"
    return f"{n/1_000_000_000:.1f}B"


def _has_tokens(tokens: dict) -> bool:
    return sum(int(tokens.get(k, 0) or 0) for k in ("input", "output", "cache_read", "cache_create")) > 0


def _fmt_token_row(label: str, tokens: dict, bold: bool = False, dim: bool = False, indent: int = 2) -> str:
    pad = " " * indent
    label_width = 22 - (indent - 2)
    label_trunc = _trunc(label, label_width)
    label_col = f"{label_trunc:<{label_width}}"
    cols = [
        f"{_fmt_tokens(int(tokens.get('input', 0) or 0)):>8}",
        f"{_fmt_tokens(int(tokens.get('output', 0) or 0)):>8}",
        f"{_fmt_tokens(int(tokens.get('cache_read', 0) or 0)):>8}",
        f"{_fmt_tokens(int(tokens.get('cache_create', 0) or 0)):>8}",
    ]
    row = f"{pad}{label_col}{''.join(cols)}"
    if bold and dim:
        return f"[bold][dim]{row}[/dim][/bold]"
    if bold:
        return f"[bold]{row}[/bold]"
    if dim:
        return f"[dim]{row}[/dim]"
    return row


def _fmt_token_summary(token_total: dict) -> str:
    """Format a 1-line token summary for the fixed header."""
    total_sum = sum(int(token_total.get(k, 0) or 0)
                    for k in ("input", "output", "cache_read", "cache_create"))
    if total_sum == 0:
        return ""
    inp = _fmt_tokens(int(token_total.get("input", 0) or 0))
    out = _fmt_tokens(int(token_total.get("output", 0) or 0))
    cr = _fmt_tokens(int(token_total.get("cache_read", 0) or 0))
    cw = _fmt_tokens(int(token_total.get("cache_create", 0) or 0))
    return f"Tokens: {inp} in / {out} out / {cr} cache-r / {cw} cache-w"


def _build_header_lines(s: dict[str, Any]) -> list[str]:
    """Build the fixed header lines: Turn, Duration, Token summary.

    Note: Prompt is rendered in #turn-section-prompt (scrollable section),
    not in the fixed header — see compose().
    """
    lines: list[str] = []
    index = int(s.get("index", 0))
    duration = float(s.get("duration_s", 0.0))
    agent_count = int(s.get("agent_count", 0))
    skill_count = int(s.get("skill_count", 0))
    error_count = int(s.get("error_count", 0))
    is_live = s.get("end_ts") is None

    header_parts = [f"Turn {index + 1}"]
    if is_live:
        header_parts.append("(LIVE)")
    lines.append(f"[bold]{' '.join(header_parts)}[/bold]")
    lines.append(
        f"Duration: {_fmt_dur(duration)}"
        f"   Agents: {agent_count}"
        f"   Skills: {skill_count}"
        f"   Errors: {error_count}"
    )
    token_summary = _fmt_token_summary(s.get("token_total") or {})
    if token_summary:
        lines.append(token_summary)
    return lines


def _build_agents_lines(s: dict[str, Any]) -> list[str]:
    """Build agents section: agent time + Agents/Skills list."""
    lines: list[str] = []

    duration = float(s.get("duration_s", 0.0))
    total_agent_dur = float(s.get("total_agent_duration_s", 0.0))
    if total_agent_dur > 0:
        lines.append(
            f"Total agent time: {_fmt_dur(total_agent_dur)}"
            + (
                f" (parallelism: {total_agent_dur / duration:.1f}x)"
                if duration > 0
                else ""
            )
        )
    lines.append("")

    agents = s.get("agents", []) or []
    if not agents:
        lines.append("(no agent or skill invocations)")
    else:
        lines.append("[bold]Agents / Skills[/bold]")
        for a in agents:
            label = _sanitize(a.get("label", ""))
            desc = _sanitize(a.get("description", ""))
            node_type = str(a.get("node_type", ""))
            status = str(a.get("status", ""))
            dur = _fmt_dur(float(a.get("duration_s", 0.0)))
            is_bg = bool(a.get("is_background", False))
            prefix = "skill" if node_type == "skill" else "agent"
            bg_tag = " [bg]" if is_bg else ""
            status_tag = ""
            if status == "error":
                status_tag = " [red]✗[/red]"
            elif status == "done":
                status_tag = " [dim]✓[/dim]"
            elif status == "running":
                status_tag = " [green]▶[/green]"
            shown_desc = desc if desc else label
            lines.append(
                f"  [{prefix}] {shown_desc} "
                f"[dim]({label})[/dim] "
                f"[{dur}]{bg_tag}{status_tag}"
            )

    return lines


def _build_tool_usage_lines(s: dict[str, Any]) -> list[str]:
    """Build tool usage section: Tool Usage + MCP + Hooks."""
    lines: list[str] = []

    # --- Tool Usage ---
    tool_usage = s.get("tool_usage", []) or []
    tool_total = int(s.get("tool_total", 0))
    if tool_usage and tool_total > 0:
        lines.append(f"[bold]Tool Usage ({tool_total} calls)[/bold]")
        for item in tool_usage:
            name = _trunc(_sanitize(item.get("name", "")), 14)
            count = int(item.get("count", 0))
            lines.append(f"  {name:<14} \u00d7{count}")

    # --- MCP ---
    mcp_usage = s.get("mcp_usage", []) or []
    mcp_total = int(s.get("mcp_total", 0))
    if mcp_usage and mcp_total > 0:
        lines.append("")
        lines.append(f"[bold]MCP ({mcp_total} calls)[/bold]")
        for item in mcp_usage:
            server = _sanitize(item.get("server", ""))
            tool_name = _sanitize(item.get("tool", ""))
            count = int(item.get("count", 0))
            display = _mcp_display(server, tool_name)
            lines.append(f"  {display:<38} \u00d7{count}")

    # --- Hooks ---
    hook_usage = s.get("hook_usage", []) or []
    hook_runs = int(s.get("hook_runs", s.get("hook_total", 0)))
    hook_errors = int(s.get("hook_errors_total", s.get("hook_error_total", 0)))
    hook_duration_ms = int(s.get("hook_duration_ms", 0))
    hooks_configured = s.get("hooks_configured")
    show_hooks = hook_runs > 0 or hooks_configured is True
    if show_hooks:
        if lines:
            lines.append("")
        if hook_errors > 0:
            err_part = f", [red]{hook_errors} errors[/red]"
        else:
            err_part = f", {hook_errors} errors"
        dur_part = (
            f"  {_fmt_ms(hook_duration_ms)}" if hook_duration_ms > 0 else ""
        )
        lines.append(
            f"[bold]Hooks ({hook_runs} events{err_part}){dur_part}[/bold]"
        )
        if hook_runs == 0:
            lines.append("  [dim](no hook fired this turn)[/dim]")
        else:
            for item in hook_usage:
                event_name = _trunc(_sanitize(item.get("event", "Stop")), 14)
                raw_script = _sanitize(item.get("script", ""))
                script = os.path.basename(raw_script) or "(anonymous)"
                script = _trunc(script, 24)
                count = int(item.get("count", 0))
                err_count = int(item.get("error_count", 0))
                err_tag = (
                    f" [red]\u2717{err_count}[/red]" if err_count > 0 else ""
                )
                lines.append(
                    f"  {event_name:<14} {script:<24} \u00d7{count}{err_tag}"
                )

    return lines


def _build_stats_lines(s: dict[str, Any]) -> list[str]:
    """Backward-compatible wrapper: agents + tool usage."""
    return _build_agents_lines(s) + _build_tool_usage_lines(s)


def _build_token_lines(s: dict[str, Any]) -> list[str]:
    """Build token usage section lines."""
    lines: list[str] = []

    tt = s.get("token_total") or {}
    total_sum = sum(int(tt.get(k, 0) or 0) for k in ("input", "output", "cache_read", "cache_create"))
    if total_sum > 0:
        lines.append("")
        lines.append("[bold]Token Usage[/bold]")
        lines.append("  Node                    input    output  cache-r  cache-w")
        lines.append("  [dim]" + "\u2500" * 58 + "[/dim]")
        lines.append(_fmt_token_row("Total", tt, bold=True))
        lines.append("  [dim]" + "\u2500" * 58 + "[/dim]")
        tm = s.get("token_main") or {}
        if _has_tokens(tm):
            lines.append(_fmt_token_row("main", tm))

        # --- Skill tree (hierarchical) or legacy token_nodes fallback ---
        skill_tree = s.get("token_skill_tree") or []
        standalone_raw_check = s.get("token_agents_standalone") or []
        use_legacy = not skill_tree and not standalone_raw_check
        if use_legacy:
            # Legacy flat display: token_nodes list (backward compat)
            nodes = s.get("token_nodes") or []
            nodes_sorted = sorted(
                nodes,
                key=lambda n: (
                    -(int((n.get("tokens") or {}).get("input", 0) or 0) + int((n.get("tokens") or {}).get("output", 0) or 0)),
                    str(n.get("label", "")),
                ),
            )
            for node in nodes_sorted:
                prefix = "[agent]" if node.get("node_type") == "agent" else "[skill]"
                lines.append(_fmt_token_row(f"{prefix} {node.get('label', '')}", node.get("tokens") or {}))

        if skill_tree:
            skills_filtered = [
                sk for sk in skill_tree
                if _has_tokens((sk.get("total") or {}).get("tokens") or {})
            ]
            skills_sorted = sorted(
                skills_filtered,
                key=lambda sk: -(
                    int(((sk.get("total") or {}).get("tokens") or {}).get("input", 0) or 0)
                    + int(((sk.get("total") or {}).get("tokens") or {}).get("output", 0) or 0)
                ),
            )
            if skills_sorted:
                lines.append("")
                for sk in skills_sorted:
                    skill_label = _sanitize(sk.get("label") or sk.get("skill_node_id") or "")
                    skill_tokens = (sk.get("total") or {}).get("tokens") or {}
                    lines.append(_fmt_token_row(f"[skill] {skill_label}", skill_tokens, indent=2))
                    agents_raw = sk.get("agents") or []
                    agents_filtered = [a for a in agents_raw if _has_tokens(a.get("tokens") or {})]
                    agents_sorted = sorted(
                        agents_filtered,
                        key=lambda a: -(
                            int((a.get("tokens") or {}).get("input", 0) or 0)
                            + int((a.get("tokens") or {}).get("output", 0) or 0)
                        ),
                    )
                    for ag in agents_sorted:
                        ag_label = _sanitize(ag.get("label") or ag.get("node_id") or "")
                        lines.append(_fmt_token_row(f"[agent] {ag_label}", ag.get("tokens") or {}, indent=4))

        # --- Standalone agents ---
        standalone_raw = standalone_raw_check
        standalone_filtered = [a for a in standalone_raw if _has_tokens(a.get("tokens") or {})]
        standalone_sorted = sorted(
            standalone_filtered,
            key=lambda a: -(
                int((a.get("tokens") or {}).get("input", 0) or 0)
                + int((a.get("tokens") or {}).get("output", 0) or 0)
            ),
        )
        if standalone_sorted:
            lines.append("")
            if len(standalone_sorted) >= 2:
                # Subtotal across ALL standalone agents (including overflow)
                subtotal: dict[str, int] = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
                for a in standalone_sorted:
                    t = a.get("tokens") or {}
                    for k in subtotal:
                        subtotal[k] += int(t.get(k, 0) or 0)
                lines.append(_fmt_token_row(f"agents ({len(standalone_sorted)})", subtotal, dim=True, indent=2))
            for ag in standalone_sorted:
                ag_label = _sanitize(ag.get("label") or ag.get("node_id") or "")
                lines.append(_fmt_token_row(f"[agent] {ag_label}", ag.get("tokens") or {}, indent=2))

    return lines


def _build_body_lines(s: dict[str, Any]) -> list[str]:
    """Backward-compatible wrapper: stats + tokens + footer."""
    lines = _build_stats_lines(s) + _build_token_lines(s)
    lines.append("")
    lines.append("(Esc / Enter to close)")
    return lines


def _build_lines(s: dict[str, Any]) -> list[str]:
    """Build the ordered list of text lines for the modal body.

    Retained as a wrapper for backward compatibility with tests.
    Returns plain markup strings (no Static wrappers) so the function
    can be unit-tested without a running Textual app.
    """
    return _build_header_lines(s) + _build_body_lines(s)


class TurnSummaryScreen(Screen[None]):
    """Full-screen summary of a single turn's orchestration."""

    _tool_timeline_rows: list[dict]

    DEFAULT_CSS = """
    TurnSummaryScreen {
        background: $panel;
    }
    #turn-summary-header {
        dock: top;
        width: 100%;
        height: auto;
        max-height: 4;
        padding: 1 2 0 2;
        background: $panel;
        border-bottom: solid $accent;
    }
    #turn-sections {
        width: 100%;
        height: 1fr;
    }
    #turn-section-prompt {
        height: 1fr;
        min-height: 3;
        padding: 0 2;
        border-bottom: solid $accent-darken-2;
        overflow-x: hidden;
        overflow-y: auto;
    }
    #turn-section-prompt Static {
        width: 100%;
    }
    #turn-section-tokens {
        height: 1fr;
        min-height: 3;
        padding: 0 2;
        border-bottom: solid $accent-darken-2;
    }
    #turn-section-agents {
        height: 1fr;
        min-height: 3;
        padding: 0 2;
        border-bottom: solid $accent-darken-2;
    }
    #turn-section-stats {
        height: 1fr;
        min-height: 3;
        padding: 0 2;
        border-bottom: solid $accent-darken-2;
    }
    #turn-section-timeline {
        height: 1fr;
        min-height: 3;
        padding: 0 2;
    }
    #turn-tool-timeline {
        height: 1fr;
        margin-top: 0;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close")]

    def __init__(self, summary: dict[str, Any]) -> None:
        super().__init__()
        self.summary = summary
        self._tool_timeline_rows = list(summary.get("tool_timeline", []) or [])

    def compose(self) -> ComposeResult:
        from datetime import datetime, timezone

        header_lines = _build_header_lines(self.summary)
        token_lines = _build_token_lines(self.summary)
        agents_lines = _build_agents_lines(self.summary)
        tool_usage_lines = _build_tool_usage_lines(self.summary)
        tool_timeline = self.summary.get("tool_timeline", []) or []

        yield Static("\n".join(header_lines), id="turn-summary-header")

        with Vertical(id="turn-sections"):
            # Section 0: Prompt (전문 스크롤 표시)
            with ScrollableContainer(id="turn-section-prompt"):
                prompt_text = _sanitize_prompt(self.summary.get("prompt", ""))
                yield Static("[bold]Prompt[/bold]")
                if prompt_text:
                    yield Static(prompt_text, markup=False)
                else:
                    yield Static("[dim](empty)[/dim]", classes="placeholder")

            # Section A: Token Usage
            with ScrollableContainer(id="turn-section-tokens"):
                if token_lines:
                    for line in token_lines:
                        yield Static(line)
                else:
                    yield Static("[dim](no token data)[/dim]", classes="placeholder")

            # Section B: Agents / Skills
            with ScrollableContainer(id="turn-section-agents"):
                for line in agents_lines:
                    if line == "(no agent or skill invocations)":
                        yield Static(line, classes="placeholder")
                    else:
                        yield Static(line)

            # Section C: Tool Usage + MCP + Hooks
            with ScrollableContainer(id="turn-section-stats"):
                if tool_usage_lines:
                    for line in tool_usage_lines:
                        yield Static(line)
                else:
                    yield Static("[dim](no tool usage)[/dim]", classes="placeholder")

            # Section C: Tool Timeline (conditional)
            if tool_timeline:
                with Vertical(id="turn-section-timeline"):
                    yield Static(
                        f"[bold]Tool Calls ({len(tool_timeline)} events) — Enter: view details[/bold]"
                    )
                    table = DataTable(id="turn-tool-timeline")
                    table.add_columns("time", "tool", "agent", "sts", "dur")
                    table.cursor_type = "row"
                    table.zebra_stripes = True
                    for evt in tool_timeline:
                        try:
                            ts = evt.get("ts", 0.0)
                            time_str = datetime.fromtimestamp(
                                ts, tz=timezone.utc
                            ).strftime("%H:%M:%S")
                        except Exception:
                            time_str = "-"
                        name = _trunc(str(evt.get("name", "")), 14)
                        agent = _trunc(str(evt.get("agent_id", "") or "-"), 16)
                        status = evt.get("status", "")
                        if status == "done":
                            sts = "ok"
                        elif status == "error":
                            sts = "err"
                        elif status == "running":
                            sts = "run"
                        else:
                            sts = str(status)[:3]
                        dur_ms = evt.get("duration_ms")
                        dur = _fmt_ms(dur_ms) if dur_ms is not None else "-"
                        table.add_row(time_str, name, agent, sts, dur)
                    yield table

            yield Static("(Esc / Enter to close)", classes="placeholder")

    def on_mount(self) -> None:
        if self._tool_timeline_rows:
            self.call_after_refresh(
                lambda: self.query_one("#turn-tool-timeline", DataTable).focus()
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        if 0 <= row_idx < len(self._tool_timeline_rows):
            entry = self._tool_timeline_rows[row_idx]
            from agentlens.panels.detail_modal import ToolDetailScreen
            self.app.push_screen(ToolDetailScreen(
                tool_name=entry.get("name", ""),
                agent_id=entry.get("agent_id", ""),
                ts=entry.get("ts", 0.0),
                status=entry.get("status", ""),
                duration_ms=entry.get("duration_ms"),
                input_summary=entry.get("input_summary", ""),
                input_raw=entry.get("input_raw"),
                output_preview=entry.get("output_preview"),
                is_error=entry.get("is_error", False),
                tool_use_id=entry.get("tool_use_id"),
            ))

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)
