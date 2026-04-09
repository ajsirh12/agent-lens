"""SessionLocator — picks which JSONL to attach to."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ChosenReason = Literal["slug", "fallback", "none", "override", "picker"]


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

    def find_candidates(self) -> list[Path]:
        """All JSONL sessions in the slugged dir, newest mtime first.

        Empty list if the slugged dir is missing. Caller can use this to
        present a picker UI when len() > 1.
        """
        if not self.projects_root.is_dir():
            return []
        slug = str(self.cwd).replace("/", "-")
        slugged = self.projects_root / slug
        if not slugged.is_dir():
            return []
        try:
            files = [p for p in slugged.iterdir() if p.is_file() and p.suffix == ".jsonl"]
        except (PermissionError, FileNotFoundError):
            return []
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files

    def find_active(self) -> Path | None:
        """Primary: slugged dir newest-mtime JSONL.
        Fallback: globally newest-mtime JSONL under any project dir.
        """
        self.chosen_path = None
        self.chosen_reason = "none"

        if not self.projects_root.is_dir():
            return None

        slug = str(self.cwd).replace("/", "-")
        slugged = self.projects_root / slug
        candidate = self._newest_jsonl_in(slugged)
        if candidate is not None:
            self.chosen_path = candidate
            self.chosen_reason = "slug"
            return candidate

        # Fallback: scan all project dirs.
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
