"""HarnessEvent dataclass + EventType enum.

The parser emits HarnessEvent instances. UI panels consume them.
See docs/jsonl-schema-observed.md for the source schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EventType(str, Enum):
    tool_use = "tool_use"
    tool_result = "tool_result"
    assistant_message = "assistant_message"
    user_message = "user_message"
    thinking = "thinking"
    agent_spawn = "agent_spawn"
    agent_status = "agent_status"
    file_history_snapshot = "file_history_snapshot"
    attachment = "attachment"
    task_notification = "task_notification"
    permission_mode = "permission_mode"
    hook_summary = "hook_summary"
    # Emitted by SubagentWatcherManager when a new agent file is discovered
    # alongside a .meta.json — maps the hex agentId to the OMC agentType so
    # the graph model can link OMC team agents that never embed agentId in
    # their tool_result content.
    subagent_meta_link = "subagent_meta_link"
    unknown = "unknown"


@dataclass(frozen=True)
class HarnessEvent:
    """A single parsed JSONL event or OMC-state event."""

    type: EventType
    ts: datetime
    agent_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    raw_line: str = ""

    # convenient accessors used by panels
    @property
    def tool_name(self) -> str:
        return str(self.payload.get("tool_name") or "")

    @property
    def tool_use_id(self) -> str | None:
        v = self.payload.get("tool_use_id")
        return str(v) if v else None

    @property
    def is_error(self) -> bool:
        return bool(self.payload.get("is_error"))

    @property
    def message_id(self) -> str | None:
        v = self.payload.get("message_id")
        return str(v) if v else None

    @property
    def subagent_uuid(self) -> str | None:
        v = self.payload.get("subagent_uuid")
        return str(v) if v else None

    @property
    def linked_subagent_uuid(self) -> str | None:
        v = self.payload.get("linked_subagent_uuid")
        return str(v) if v else None
