"""ReplayPickerScreen — modal for selecting a past JSONL session for replay."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static


class ReplayPickerScreen(ModalScreen[Path | None]):
    """Shows recent JSONL files from ~/.claude/projects/, newest first.

    Returns the chosen Path via dismiss(), or None if cancelled.
    The fallback item dismisses None so app.py can push SessionPathInputScreen.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        Binding("enter", "select", "Open"),
        Binding("j", "cursor_down", "Down"),
        Binding("k", "cursor_up", "Up"),
        Binding("down", "cursor_down", "Down"),
        Binding("up", "cursor_up", "Up"),
    ]

    def __init__(self, projects_root: Path | None = None) -> None:
        super().__init__()
        self.projects_root = projects_root or Path.home() / ".claude" / "projects"
        self._files: list[Path] = self._scan_recent_jsonls()

    def _scan_recent_jsonls(self, limit: int = 50) -> list[Path]:
        if not self.projects_root.exists():
            return []
        files: list[Path] = []
        try:
            for slug_dir in self.projects_root.iterdir():
                if slug_dir.is_dir():
                    try:
                        files.extend(slug_dir.glob("*.jsonl"))
                    except OSError:
                        pass
        except OSError:
            return []
        valid: list[Path] = []
        for p in files:
            try:
                p.stat()
                valid.append(p)
            except OSError:
                pass
        return sorted(valid, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]

    def _format_row(self, path: Path) -> str:
        try:
            st = path.stat()
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_kb = st.st_size / 1024
            slug = path.parent.name
            return f"{mtime}  {size_kb:8.1f} KB  {path.name}  [{slug}]"
        except OSError:
            return path.name

    def compose(self) -> ComposeResult:
        items: list[ListItem] = []
        for p in self._files:
            items.append(ListItem(Static(self._format_row(p))))
        if not items:
            items.append(ListItem(Static("(No recent sessions found)")))
        items.append(ListItem(Static("[Open any .jsonl file by path...]")))

        with Vertical(id="replay-picker-dialog"):
            yield Static(
                f"Replay a past session ({len(self._files)} recent files, newest first):",
                id="replay-picker-title",
            )
            yield ListView(*items, id="replay-list")
            yield Static(
                "↑/↓ navigate · Enter open · Esc cancel",
                classes="placeholder",
            )

    def on_mount(self) -> None:
        lv = self.query_one("#replay-list", ListView)
        lv.focus()
        lv.index = 0

    def action_select(self) -> None:
        lv = self.query_one("#replay-list", ListView)
        idx = lv.index or 0
        # Last item is always the fallback "open by path" entry
        fallback_idx = len(self._files) if self._files else 1
        if idx == fallback_idx:
            self.dismiss(None)
        elif 0 <= idx < len(self._files):
            self.dismiss(self._files[idx])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#replay-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#replay-list", ListView).action_cursor_up()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_select()
