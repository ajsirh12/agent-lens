"""SessionPickerScreen — modal for selecting one of N JSONL sessions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static


class SessionPickerScreen(ModalScreen[Path | None]):
    """Pushed when SessionLocator returns ≥2 candidates.

    Returns the chosen Path via dismiss(), or None if cancelled.
    Items are presented newest-mtime first.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        Binding("enter", "select", "Select"),
    ]

    def __init__(
        self,
        candidates: list[Path],
        *,
        current_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.candidates = candidates
        self.current_path = current_path

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-body"):
            yield Static(
                f"Multiple Claude Code sessions found ({len(self.candidates)}). "
                "Pick one:",
                id="picker-title",
            )
            items: list[ListItem] = []
            for p in self.candidates:
                items.append(ListItem(Static(self._format_row(p))))
            yield ListView(*items, id="picker-list")
            yield Static(
                "↑/↓ navigate · Enter select · Esc cancel",
                classes="placeholder",
            )

    def _format_row(self, path: Path | None = None) -> str:
        # Back-compat: the original implementation was a staticmethod, so
        # some callers invoke it as ``SessionPickerScreen._format_row(p)``
        # which binds ``path`` to ``self``. Detect that by checking whether
        # the first argument looks like a Path.
        if isinstance(self, Path):
            path = self  # type: ignore[assignment]
            current: Path | None = None
        else:
            current = self.current_path
        assert path is not None
        try:
            st = path.stat()
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_kb = st.st_size / 1024
            row = f"{mtime}  {size_kb:8.1f} KB  {path.name}"
        except OSError:
            row = path.name
        if current is not None and path == current:
            row = f"{row} ✓ (current)"
        return row

    def on_mount(self) -> None:
        lv = self.query_one("#picker-list", ListView)
        lv.focus()
        lv.index = 0

    def action_select(self) -> None:
        lv = self.query_one("#picker-list", ListView)
        idx = lv.index or 0
        if 0 <= idx < len(self.candidates):
            self.dismiss(self.candidates[idx])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # Click / Enter on a row.
        idx = self.query_one("#picker-list", ListView).index or 0
        if 0 <= idx < len(self.candidates):
            self.dismiss(self.candidates[idx])
