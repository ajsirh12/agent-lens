"""ToolDetailScreen — modal showing tool call details (AC7)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
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
        input_summary: str,
        status: str,
        duration_ms: str,
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.input_summary = (input_summary or "")[:200]
        self.status = status
        self.duration_ms = duration_ms

    def compose(self) -> ComposeResult:
        tool_name = _sanitize_cell(self.tool_name)
        input_summary = _sanitize_cell(self.input_summary)
        status = _sanitize_cell(self.status)
        duration_ms = _sanitize_cell(self.duration_ms)
        with Vertical(id="detail-body"):
            yield Static(f"Tool:     {tool_name}")
            yield Static(f"Input:    {input_summary}")
            yield Static(f"Status:   {status}")
            yield Static(f"Duration: {duration_ms} ms")
            yield Static("(Esc / Enter to close)", classes="placeholder")

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)
