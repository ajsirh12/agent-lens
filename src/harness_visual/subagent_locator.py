"""SubagentLocator — finds subagent JSONL files next to a main session.

Claude Code writes each subagent spawn's conversation to a separate
JSONL file living under:

    ~/.claude/projects/{slug}/{sessionId}/subagents/agent-{agentId}.jsonl

where ``{sessionId}`` is the stem of the main session's JSONL file. This
module is pure data — no Textual imports — so it can be unit tested
without mounting an app.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_AGENT_FILENAME_RE = re.compile(r"^agent-([a-f0-9]+)\.jsonl$")


@dataclass
class SubagentLocator:
    main_session_path: Path

    @property
    def subagents_dir(self) -> Path:
        """Directory holding agent-*.jsonl files for this session."""
        return self.main_session_path.with_suffix("") / "subagents"

    def list_files(self) -> list[Path]:
        """All agent-*.jsonl files in subagents dir, newest mtime first.

        Missing directory / PermissionError / any OS error → ``[]``.
        """
        d = self.subagents_dir
        try:
            if not d.is_dir():
                return []
            files = [p for p in d.iterdir() if p.is_file() and _AGENT_FILENAME_RE.match(p.name)]
        except (PermissionError, OSError) as e:
            log.debug("SubagentLocator.list_files error on %s: %s", d, e)
            return []
        try:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            pass
        return files

    @staticmethod
    def agent_id_from_filename(p: Path) -> str | None:
        """agent-a48d2d1088dd1be44.jsonl -> 'a48d2d1088dd1be44'."""
        m = _AGENT_FILENAME_RE.match(p.name)
        return m.group(1) if m else None
