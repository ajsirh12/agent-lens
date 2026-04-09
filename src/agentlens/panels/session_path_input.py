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
        """Return a valid Path or None (with the error surfaced).

        Accepts three forms:

        1. A full path to a ``.jsonl`` file.
        2. A path with ``~`` or quotes around it.
        3. A bare session id or prefix (no slashes) that matches
           ``~/.claude/projects/*/<id>*.jsonl``. If exactly one file
           matches, it is used; zero or multiple matches surface as
           inline errors.
        """
        text = raw.strip().strip("'\"")
        if not text:
            self._set_error("Path is empty")
            return None

        # Form 3: bare id / prefix lookup. Triggered when the input has
        # no path separators and no extension suffix — i.e. it doesn't
        # look like a path at all.
        if "/" not in text and "\\" not in text and not text.endswith(".jsonl"):
            resolved = self._resolve_session_id(text)
            if resolved is not None:
                self._set_error("")
                return resolved
            # _resolve_session_id already surfaced an error; stop.
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

    def _resolve_session_id(self, sid: str) -> Path | None:
        """Look up ``~/.claude/projects/*/<sid>*.jsonl``.

        - Zero matches → error, returns None.
        - One match → that Path.
        - Multiple matches → error listing counts, returns None.
        """
        projects = Path.home() / ".claude" / "projects"
        if not projects.is_dir():
            self._set_error(f"Projects directory not found: {projects}")
            return None
        # Collect matches across every project subdir.
        matches: list[Path] = []
        try:
            entries = list(projects.iterdir())
        except (PermissionError, FileNotFoundError) as exc:
            self._set_error(f"Cannot read projects dir: {exc}")
            return None
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                for f in entry.glob(f"{sid}*.jsonl"):
                    if f.is_file():
                        matches.append(f)
            except (PermissionError, OSError):
                continue
        if not matches:
            self._set_error(
                f"No session found for id/prefix '{sid}'"
            )
            return None
        if len(matches) > 1:
            # Deduplicate by filename in case the same id appears in
            # multiple project dirs (shouldn't normally happen).
            self._set_error(
                f"{len(matches)} sessions matched '{sid}' — paste the full path instead"
            )
            return None
        return matches[0]

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
