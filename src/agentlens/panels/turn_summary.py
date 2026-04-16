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
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

_TOP_N_TOOL = 8
_TOP_N_MCP = 6
_TOP_N_HOOK = 5
_TOP_N_SKILL = 5
_TOP_N_AGENT = 8
_TOP_N_TOKEN = 10  # legacy token_nodes fallback
_TOP_N_TIMELINE = 200  # max rows in the tool timeline DataTable


def _sanitize(s: object) -> str:
    text = str(s)
    text = "".join(c for c in text if (c.isprintable() or c == "\t") and c not in "\x1b\r")
    return text[:500]


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


def _build_lines(s: dict[str, Any]) -> list[str]:
    """Build the ordered list of text lines for the modal body.

    Returns plain markup strings (no Static wrappers) so the function
    can be unit-tested without a running Textual app.
    """
    lines: list[str] = []

    index = int(s.get("index", 0))
    prompt = _sanitize(s.get("prompt", ""))
    duration = float(s.get("duration_s", 0.0))
    agent_count = int(s.get("agent_count", 0))
    skill_count = int(s.get("skill_count", 0))
    error_count = int(s.get("error_count", 0))
    total_agent_dur = float(s.get("total_agent_duration_s", 0.0))
    is_live = s.get("end_ts") is None

    header_parts = [f"Turn {index + 1}"]
    if is_live:
        header_parts.append("(LIVE)")
    header = " ".join(header_parts)

    lines.append(f"[bold]{header}[/bold]")
    lines.append(f"Prompt:   {prompt if prompt else '(empty)'}")
    lines.append(
        f"Duration: {_fmt_dur(duration)}"
        f"   Agents: {agent_count}"
        f"   Skills: {skill_count}"
        f"   Errors: {error_count}"
    )
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

    # --- Agents / Skills ---
    agents = s.get("agents", []) or []
    if not agents:
        lines.append("(no agent or skill invocations)")
    else:
        lines.append("[bold]Agents / Skills[/bold]")
        for i, a in enumerate(agents):
            if i >= 30:
                lines.append(f"  ... +{len(agents) - 30} more")
                break
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

    # --- Tool Usage section ---
    tool_usage = s.get("tool_usage", []) or []
    tool_total = int(s.get("tool_total", 0))
    if tool_usage and tool_total > 0:
        lines.append("")
        lines.append(f"[bold]Tool Usage ({tool_total} calls)[/bold]")
        shown = tool_usage[:_TOP_N_TOOL]
        for item in shown:
            name = _trunc(_sanitize(item.get("name", "")), 14)
            count = int(item.get("count", 0))
            lines.append(f"  {name:<14} \u00d7{count}")
        overflow = len(tool_usage) - len(shown)
        if overflow > 0:
            lines.append(f"  ... +{overflow} more")

    # --- MCP section ---
    mcp_usage = s.get("mcp_usage", []) or []
    mcp_total = int(s.get("mcp_total", 0))
    if mcp_usage and mcp_total > 0:
        lines.append("")
        lines.append(f"[bold]MCP ({mcp_total} calls)[/bold]")
        shown_mcp = mcp_usage[:_TOP_N_MCP]
        for item in shown_mcp:
            server = _sanitize(item.get("server", ""))
            tool_name = _sanitize(item.get("tool", ""))
            count = int(item.get("count", 0))
            display = _mcp_display(server, tool_name)
            lines.append(f"  {display:<38} \u00d7{count}")
        mcp_overflow = len(mcp_usage) - len(shown_mcp)
        if mcp_overflow > 0:
            lines.append(f"  ... +{mcp_overflow} more")

    # --- Hooks section ---
    hook_usage = s.get("hook_usage", []) or []
    hook_runs = int(s.get("hook_runs", s.get("hook_total", 0)))
    hook_errors = int(s.get("hook_errors_total", s.get("hook_error_total", 0)))
    hook_duration_ms = int(s.get("hook_duration_ms", 0))
    hooks_configured = s.get("hooks_configured")
    show_hooks = hook_runs > 0 or hooks_configured is True
    if show_hooks:
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
            shown_hooks = hook_usage[:_TOP_N_HOOK]
            for item in shown_hooks:
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
            hook_overflow = len(hook_usage) - len(shown_hooks)
            if hook_overflow > 0:
                lines.append(f"  ... +{hook_overflow} more")

    # --- Token Usage section ---
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
            shown_nodes = nodes_sorted[:_TOP_N_TOKEN]
            for node in shown_nodes:
                prefix = "[agent]" if node.get("node_type") == "agent" else "[skill]"
                lines.append(_fmt_token_row(f"{prefix} {node.get('label', '')}", node.get("tokens") or {}))
            extra = len(nodes_sorted) - len(shown_nodes)
            if extra > 0:
                lines.append(f"  [dim]... +{extra} more[/dim]")

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
            skills_visible = skills_sorted[:_TOP_N_SKILL]
            if skills_visible:
                lines.append("")
                for sk in skills_visible:
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
            skill_overflow = len(skills_sorted) - len(skills_visible)
            if skill_overflow > 0:
                lines.append(f"  [dim]... +{skill_overflow} more skills[/dim]")

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
            shown_standalone = standalone_sorted[:_TOP_N_AGENT]
            for ag in shown_standalone:
                ag_label = _sanitize(ag.get("label") or ag.get("node_id") or "")
                lines.append(_fmt_token_row(f"[agent] {ag_label}", ag.get("tokens") or {}, indent=2))
            agent_overflow = len(standalone_sorted) - len(shown_standalone)
            if agent_overflow > 0:
                lines.append(f"  [dim]... +{agent_overflow} more agents[/dim]")

    lines.append("")
    lines.append("(Esc / Enter to close)")
    return lines


class TurnSummaryScreen(ModalScreen[None]):
    """Modal showing a summary of a single turn's orchestration."""

    DEFAULT_CSS = """
    #turn-summary-scroll {
        width: 80;
        max-width: 95%;
        max-height: 85%;
        border: thick $accent;
        background: $panel;
        padding: 1 2;
    }
    #turn-tool-timeline {
        max-height: 15;
        margin-top: 1;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close")]

    def __init__(self, summary: dict[str, Any]) -> None:
        super().__init__()
        self.summary = summary

    def compose(self) -> ComposeResult:
        lines = _build_lines(self.summary)
        tool_timeline = self.summary.get("tool_timeline", []) or []

        with ScrollableContainer(id="turn-summary-scroll"):
            with Vertical(id="turn-summary-body"):
                for line in lines:
                    if line in (
                        "(no agent or skill invocations)",
                        "(Esc / Enter to close)",
                    ):
                        yield Static(line, classes="placeholder")
                    else:
                        yield Static(line)

            # --- Tool Timeline DataTable ---
            if tool_timeline:
                yield Static(
                    f"[bold]Tool Calls ({len(tool_timeline)} events)[/bold]"
                )
                table = DataTable(id="turn-tool-timeline")
                table.add_columns("time", "tool", "agent", "sts", "dur")
                table.cursor_type = "row"
                table.zebra_stripes = True
                for evt in tool_timeline[:_TOP_N_TIMELINE]:
                    try:
                        ts = evt.get("ts", 0.0)
                        from datetime import datetime, timezone
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
                overflow = len(tool_timeline) - _TOP_N_TIMELINE
                if overflow > 0:
                    table.add_row("", f"... +{overflow} more", "", "", "")
                yield table

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)
