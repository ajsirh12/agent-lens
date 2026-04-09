"""SessionLocator — picks which JSONL to attach to."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ChosenReason = Literal[
    "slug",
    "fallback",
    "cwd-match",
    "none",
    "override",
    "picker",
    "switched",
    "path-input",
]


def _norm(p: str) -> str:
    """Normalize a path string for cross-platform comparison.

    Converts backslashes to forward slashes, strips any trailing
    separator, and lowercases on Windows (case-insensitive FS).
    """
    s = p.replace("\\", "/").rstrip("/")
    # POSIX FS is case-sensitive; Windows FS is not. We detect Windows
    # via the presence of a drive letter at the start (``C:/...``).
    if len(s) >= 2 and s[1] == ":":
        s = s.lower()
    return s


@dataclass
class SessionLocator:
    cwd: Path
    projects_root: Path  # usually ~/.claude/projects
    chosen_path: Path | None = None
    chosen_reason: ChosenReason = "none"

    @classmethod
    def default(cls, cwd: Path | None = None) -> "SessionLocator":
        return cls(
            cwd=cwd or Path.cwd(),
            projects_root=Path.home() / ".claude" / "projects",
        )

    def _slug(self) -> str:
        # Normalize path separators first so Windows (backslashes) and
        # POSIX (forward slashes) both produce dash-separated slugs.
        posix = str(self.cwd).replace("\\", "/")
        return posix.replace("/", "-")

    def _cwd_matches(self, jsonl_path: Path) -> bool:
        """Peek at a JSONL file's first row and return True if its
        ``cwd`` field matches ``self.cwd``.

        Used as a slug-independent fallback for environments where
        Claude Code's slug convention does not match this locator's
        (e.g. git bash on Windows where ``Path.cwd()`` returns a
        backslash path but Claude Code writes a forward-slash slug).
        Safe: ignores malformed / permission-denied files.
        """
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                first = f.readline()
        except (OSError, UnicodeDecodeError):
            return False
        if not first.strip():
            return False
        try:
            obj = json.loads(first)
        except json.JSONDecodeError:
            return False
        if not isinstance(obj, dict):
            return False
        recorded = obj.get("cwd")
        if not isinstance(recorded, str):
            return False
        return _norm(recorded) == _norm(str(self.cwd))

    def _scan_matching_jsonls(self) -> list[Path]:
        """Scan every project dir for JSONLs whose first row's ``cwd``
        matches ``self.cwd``. Newest mtime first. Empty list on miss.
        """
        matches: list[Path] = []
        try:
            entries = list(self.projects_root.iterdir())
        except (PermissionError, FileNotFoundError):
            return []
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                files = [p for p in entry.iterdir() if p.is_file() and p.suffix == ".jsonl"]
            except (PermissionError, FileNotFoundError):
                continue
            for jsonl in files:
                if self._cwd_matches(jsonl):
                    matches.append(jsonl)
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return matches

    def find_candidates(self) -> list[Path]:
        """All JSONL sessions for this cwd, newest mtime first.

        Fast path: the slugged dir. Fallback: scan project dirs and
        match on the ``cwd`` field of each JSONL's first row — this
        is what makes the locator work on Windows / git bash where
        Claude Code's slug convention differs from this tool's.
        """
        if not self.projects_root.is_dir():
            return []
        slugged = self.projects_root / self._slug()
        if slugged.is_dir():
            try:
                files = [
                    p
                    for p in slugged.iterdir()
                    if p.is_file() and p.suffix == ".jsonl"
                ]
            except (PermissionError, FileNotFoundError):
                files = []
            if files:
                files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return files
        return self._scan_matching_jsonls()

    def find_active(self) -> Path | None:
        """Primary: slugged dir newest-mtime JSONL.
        Secondary: cwd-field match across all project dirs.
        Tertiary: globally newest-mtime JSONL under any project dir.
        """
        self.chosen_path = None
        self.chosen_reason = "none"

        if not self.projects_root.is_dir():
            return None

        slugged = self.projects_root / self._slug()
        candidate = self._newest_jsonl_in(slugged)
        if candidate is not None:
            self.chosen_path = candidate
            self.chosen_reason = "slug"
            return candidate

        # cwd-match fallback (slug-independent, works on Windows).
        cwd_matches = self._scan_matching_jsonls()
        if cwd_matches:
            self.chosen_path = cwd_matches[0]
            self.chosen_reason = "cwd-match"
            return cwd_matches[0]

        # Last resort: globally newest JSONL across all project dirs.
        best: Path | None = None
        best_mtime = -1.0
        try:
            for entry in self.projects_root.iterdir():
                if not entry.is_dir():
                    continue
                cand = self._newest_jsonl_in(entry)
                if cand is None:
                    continue
                m = cand.stat().st_mtime
                if m > best_mtime:
                    best_mtime = m
                    best = cand
        except (PermissionError, FileNotFoundError):
            return None
        if best is not None:
            self.chosen_path = best
            self.chosen_reason = "fallback"
        return best

    @staticmethod
    def _newest_jsonl_in(directory: Path) -> Path | None:
        if not directory.is_dir():
            return None
        try:
            files = [p for p in directory.iterdir() if p.is_file() and p.suffix == ".jsonl"]
        except (PermissionError, FileNotFoundError):
            return None
        if not files:
            return None
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0]
