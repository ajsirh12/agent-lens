"""SessionPathInputScreen — modal for pasting a session JSONL path.

Used as an escape hatch when the normal session picker cannot find
the intended session (e.g. slug-path mismatch on Windows git-bash).
Pressing ``Shift+S`` in the app pushes this modal; the user pastes
a path, hits Enter, and the app attaches to that file if it exists.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class SessionPathInputScreen(ModalScreen[Path | None]):
    """Ask the user for a JSONL path via an Input widget.

    Returns the resolved Path on submit (``dismiss(path)``) or None
    on cancel (``dismiss(None)``). Validates existence, file-type,
    and .jsonl suffix in place and surfaces errors inline.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="path-input-body"):
            yield Static(
                "Paste a session JSONL path and press Enter:",
                id="path-input-title",
            )
            yield Input(
                placeholder="e.g. ~/.claude/projects/<slug>/<session-id>.jsonl",
                id="session-path-field",
            )
            yield Static("", id="path-input-error", classes="placeholder")
            yield Static(
                "Enter submit · Esc cancel",
                classes="placeholder",
            )

    def on_mount(self) -> None:
        self.query_one("#session-path-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        resolved = self._validate(event.value)
        if resolved is None:
            return
        self.dismiss(resolved)

    def _validate(self, raw: str) -> Path | None:
        """Return a valid Path or None (with the error surfaced)."""
        text = raw.strip().strip("'\"")
        if not text:
            self._set_error("Path is empty")
            return None
        try:
            candidate = Path(text).expanduser()
        except (OSError, ValueError) as exc:
            self._set_error(f"Invalid path: {exc}")
            return None
        if not candidate.exists():
            self._set_error(f"File not found: {candidate}")
            return None
        if not candidate.is_file():
            self._set_error(f"Not a file: {candidate}")
            return None
        if candidate.suffix != ".jsonl":
            self._set_error(
                f"Not a .jsonl file: {candidate.name} "
                f"(suffix: {candidate.suffix or '(none)'})"
            )
            return None
        self._set_error("")
        return candidate

    def _set_error(self, msg: str) -> None:
        try:
            err = self.query_one("#path-input-error", Static)
        except Exception:
            return
        if msg:
            err.update(f"[red]{msg}[/red]")
        else:
            err.update("")

    def action_cancel(self) -> None:
        self.dismiss(None)
