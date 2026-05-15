"""ToolDetailScreen — modal showing tool call details (AC7)."""

from __future__ import annotations

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


def _sanitize_cell(s: object) -> str:
    """Strip non-printable / ANSI-escape characters and cap length."""
    text = str(s)
    text = "".join(c for c in text if (c.isprintable() or c == "\t") and c not in "\x1b\r")
    return text[:500]


class ToolDetailScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close")]

    def __init__(
        self,
        tool_name: str,
        agent_id: str,
        ts: float,
        status: str,
        duration_ms: int | None,
        input_summary: str,
        input_raw: str | None,
        output_preview: str | None,
        is_error: bool,
        tool_use_id: str | None = None,
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.agent_id = agent_id
        self.ts = ts
        self.status = status
        self.duration_ms = duration_ms
        self.input_summary = (input_summary or "")[:200]
        self.input_raw = input_raw
        self.output_preview = output_preview
        self.is_error = is_error
        self.tool_use_id = tool_use_id

    def compose(self) -> ComposeResult:
        tool_name = _sanitize_cell(self.tool_name)
        agent_id = _sanitize_cell(self.agent_id)
        status = _sanitize_cell(self.status)
        duration_str = f"{self.duration_ms} ms" if self.duration_ms is not None else "-"
        try:
            started_str = datetime.fromtimestamp(self.ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        except Exception:
            started_str = "-"
        tool_use_id = _sanitize_cell(self.tool_use_id) if self.tool_use_id else "-"
        input_text = self.input_raw if self.input_raw else "(empty)"
        output_text = self.output_preview if self.output_preview else "(empty)"
        output_header = (
            "[red]─ Output (error) ─[/red]" if self.is_error else "─ Output ────"
        )

        with Vertical(id="detail-body"):
            yield Static(f"Tool:         {tool_name}")
            yield Static(f"Agent:        {agent_id}")
            yield Static(f"Status:       {status}")
            yield Static(f"Duration:     {duration_str}")
            yield Static(f"Started:      {started_str}")
            yield Static(f"Tool Use ID:  {tool_use_id}")
            yield Static("─ Input ─────")
            with ScrollableContainer():
                yield Static(input_text, markup=False)
            yield Static(output_header)
            with ScrollableContainer():
                yield Static(output_text, markup=False)

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)
