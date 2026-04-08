# Source of truth: docs/jsonl-schema-observed.md
"""Schema-tolerant JSONL line parser for Claude Code session files.

Any malformed line, unknown type, or structural surprise → HarnessEvent
with type=EventType.unknown. Never raises to callers. See AC10.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .events import EventType, HarnessEvent

log = logging.getLogger(__name__)

# Matches the "agentId: <hash>" preamble embedded in a main-session
# tool_result block when an Agent tool spawn has completed. The hash is a
# 12+ hex-char identifier that also forms the filename of the subagent
# JSONL file (agent-<hash>.jsonl).
_LINKED_AGENT_RE = re.compile(r"agentId:\s*([a-f0-9]{12,})")


def _extract_linked_subagent_uuid(content: Any) -> str | None:
    """Scan tool_result block content for an embedded agentId reference.

    Claude Code inlines a line like ``agentId: a48d2d1088dd1be44 (use
    SendMessage ...)`` in the tool_result payload for an Agent spawn.
    Content may be a plain string or a list of content blocks (each a
    dict with 'text'). Returns the first match or None.
    """
    if isinstance(content, str):
        m = _LINKED_AGENT_RE.search(content)
        return m.group(1) if m else None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    m = _LINKED_AGENT_RE.search(text)
                    if m:
                        return m.group(1)
    return None

SUPPORTED_TOP_TYPES = {
    "assistant",
    "user",
    "file-history-snapshot",
    "attachment",
    "permission-mode",
}
SUPPORTED_CONTENT_TYPES = {"text", "thinking", "tool_use", "tool_result"}


def _parse_ts(raw: Any) -> datetime:
    if isinstance(raw, str):
        try:
            # Claude Code emits ISO-8601 with trailing Z
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _agent_id_from(obj: dict[str, Any]) -> str | None:
    # Sidechain / sub-agent rows get a synthetic id from their parentUuid;
    # main-thread rows use the sessionId.
    if obj.get("isSidechain"):
        pu = obj.get("parentUuid")
        if pu:
            return f"sub:{str(pu)[:8]}"
    sid = obj.get("sessionId")
    if sid:
        return str(sid)
    return None


def _content_blocks(obj: dict[str, Any]) -> list[dict[str, Any]]:
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, list):
        return [c for c in content if isinstance(c, dict)]
    # Some rows have message.content as a string — treat as single text block.
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def parse_line(line: str) -> list[HarnessEvent]:
    """Parse one JSONL line into zero or more HarnessEvents.

    One JSONL row may contain multiple content blocks (text + tool_use etc.),
    so we return a list. Empty strings → empty list. Malformed JSON → single
    unknown event.
    """
    if not line or not line.strip():
        return []
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        log.debug("malformed JSONL line: %s", e)
        return [
            HarnessEvent(
                type=EventType.unknown,
                ts=datetime.now(timezone.utc),
                agent_id=None,
                payload={"error": str(e)},
                raw_line=line,
            )
        ]
    if not isinstance(obj, dict):
        return [
            HarnessEvent(
                type=EventType.unknown,
                ts=datetime.now(timezone.utc),
                agent_id=None,
                payload={"reason": "not an object"},
                raw_line=line,
            )
        ]

    top_type = obj.get("type")
    ts = _parse_ts(obj.get("timestamp"))
    agent_id = _agent_id_from(obj)
    # Top-level agentId field (set in subagent-file rows). We stash it in
    # every emitted event's payload under 'subagent_uuid' so downstream
    # consumers can route internal tool_use events back to the Agent node
    # on the main flowchart without colliding with the legacy agent_id
    # heuristic.
    subagent_uuid_raw = obj.get("agentId")
    subagent_uuid = str(subagent_uuid_raw) if subagent_uuid_raw else None
    # isMeta=True marks system-injected user rows (skill base directory
    # notices, hook messages, etc.) that must NOT be treated as real
    # user turn boundaries by the graph model's sticky-running flush.
    is_meta = bool(obj.get("isMeta"))

    if top_type not in SUPPORTED_TOP_TYPES:
        log.debug("unknown top-level type: %r", top_type)
        return [
            HarnessEvent(
                type=EventType.unknown,
                ts=ts,
                agent_id=agent_id,
                payload={"top_type": top_type},
                raw_line=line,
            )
        ]

    # Terminal top types with no content[] are surfaced directly.
    if top_type == "file-history-snapshot":
        return [
            HarnessEvent(
                type=EventType.file_history_snapshot,
                ts=ts,
                agent_id=agent_id,
                payload={"message_id": obj.get("messageId")},
                raw_line=line,
            )
        ]
    if top_type == "attachment":
        return [
            HarnessEvent(
                type=EventType.attachment,
                ts=ts,
                agent_id=agent_id,
                payload={"attachment": obj.get("attachment")},
                raw_line=line,
            )
        ]
    if top_type == "permission-mode":
        return [
            HarnessEvent(
                type=EventType.permission_mode,
                ts=ts,
                agent_id=agent_id,
                payload={"mode": obj.get("permissionMode")},
                raw_line=line,
            )
        ]

    # assistant / user rows: explode content[] blocks into events.
    blocks = _content_blocks(obj)
    if not blocks:
        # Assistant row with no content (empty message) — surface as message.
        kind = (
            EventType.assistant_message
            if top_type == "assistant"
            else EventType.user_message
        )
        return [
            HarnessEvent(
                type=kind,
                ts=ts,
                agent_id=agent_id,
                payload={"uuid": obj.get("uuid")},
                raw_line=line,
            )
        ]

    out: list[HarnessEvent] = []
    for block in blocks:
        bt = block.get("type")
        if bt not in SUPPORTED_CONTENT_TYPES:
            log.debug("unknown content type: %r", bt)
            out.append(
                HarnessEvent(
                    type=EventType.unknown,
                    ts=ts,
                    agent_id=agent_id,
                    payload={"content_type": bt},
                    raw_line=line,
                )
            )
            continue
        if bt == "tool_use":
            out.append(
                HarnessEvent(
                    type=EventType.tool_use,
                    ts=ts,
                    agent_id=agent_id,
                    payload={
                        "tool_use_id": block.get("id"),
                        "tool_name": block.get("name"),
                        "input": block.get("input"),
                        "parent_tool_use_id": block.get("parent_tool_use_id"),
                        "uuid": obj.get("uuid"),
                    },
                    raw_line=line,
                )
            )
        elif bt == "tool_result":
            linked = _extract_linked_subagent_uuid(block.get("content"))
            payload: dict[str, Any] = {
                "tool_use_id": block.get("tool_use_id"),
                "content": block.get("content"),
                "is_error": block.get("is_error", False),
                "source_assistant_uuid": obj.get("sourceToolAssistantUUID"),
            }
            if linked:
                payload["linked_subagent_uuid"] = linked
            out.append(
                HarnessEvent(
                    type=EventType.tool_result,
                    ts=ts,
                    agent_id=agent_id,
                    payload=payload,
                    raw_line=line,
                )
            )
        elif bt == "text":
            kind = (
                EventType.assistant_message
                if top_type == "assistant"
                else EventType.user_message
            )
            text_payload: dict[str, Any] = {
                "text": block.get("text", "")[:500],
                "is_meta": is_meta,
            }
            out.append(
                HarnessEvent(
                    type=kind,
                    ts=ts,
                    agent_id=agent_id,
                    payload=text_payload,
                    raw_line=line,
                )
            )
        elif bt == "thinking":
            out.append(
                HarnessEvent(
                    type=EventType.thinking,
                    ts=ts,
                    agent_id=agent_id,
                    payload={"thinking": (block.get("thinking") or "")[:500]},
                    raw_line=line,
                )
            )
    # Stamp subagent_uuid on every emitted event from a subagent-file row
    # so the graph model can route internal tool_use events back to the
    # parent Agent node on the main flowchart.
    if subagent_uuid:
        for ev in out:
            ev.payload["subagent_uuid"] = subagent_uuid
    return out


def parse_lines(lines: Iterable[str]) -> list[HarnessEvent]:
    out: list[HarnessEvent] = []
    for ln in lines:
        out.extend(parse_line(ln))
    return out
