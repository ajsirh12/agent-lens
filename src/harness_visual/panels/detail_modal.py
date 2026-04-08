"""ToolDetailScreen — modal showing tool call details (AC7)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


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
        with Vertical(id="detail-body"):
            yield Static(f"Tool:     {self.tool_name}")
            yield Static(f"Input:    {self.input_summary}")
            yield Static(f"Status:   {self.status}")
            yield Static(f"Duration: {self.duration_ms} ms")
            yield Static("(Esc / Enter to close)", classes="placeholder")

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)
