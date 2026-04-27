# Source of truth: docs/jsonl-schema-observed.md
"""Schema-tolerant JSONL line parser for Claude Code session files.

Any malformed line, unknown type, or structural surprise → HarnessEvent
with type=EventType.unknown. Never raises to callers. See AC10.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .events import EventType, HarnessEvent

log = logging.getLogger(__name__)

# Cap raw_line storage: a 50 MB line held by thousands of timeline rows would
# cause multi-GB retention. 8 KiB is sufficient for debugging.
MAX_RAW_LINE = 8192

# Per-event cap on number of hookInfos list elements we retain. Protects
# against adversarial payloads with unbounded hook info arrays.
MAX_HOOK_INFOS = 50

# User prompt only — relaxed text cap so TurnSummaryScreen can show the
# full prompt (XML wrappers, multi-line, ~few KB). All other text blocks
# (assistant, sidechain, isMeta system-injected user rows) keep [:500].
_USER_PROMPT_MAX_LEN = 10_000


def _truncate(s: str) -> str:
    return s[:MAX_RAW_LINE]

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

_TASK_NOTIFICATION_RE = re.compile(
    r"<tool-use-id>([^<]+)</tool-use-id>"
    r".*?<status>([^<]+)</status>"
    r".*?<duration_ms>(\d+)</duration_ms>",
    re.DOTALL,
)

SUPPORTED_TOP_TYPES = {
    "assistant",
    "user",
    "file-history-snapshot",
    "attachment",
    "permission-mode",
    "queue-operation",
    "system",
}
SUPPORTED_CONTENT_TYPES = {"text", "thinking", "tool_use", "tool_result"}


def _parse_hook_infos(value: Any) -> list[dict[str, Any]]:
    """Extract a capped list of ``{command, durationMs}`` dicts.

    Defensive: accepts an already-decoded list, a JSON string, or a
    Python repr string (ast.literal_eval fallback). Returns an empty
    list for anything else. Per-element partial counting: dict
    elements missing a str ``command`` are skipped silently so a
    single bad element never discards the whole event (D-1).
    """
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            try:
                decoded = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                log.debug("hookInfos unparseable string payload")
                return []
        value = decoded
    if not isinstance(value, list):
        log.debug("hookInfos not a list: %r", type(value).__name__)
        return []
    out: list[dict[str, Any]] = []
    for item in value[:MAX_HOOK_INFOS]:
        if not isinstance(item, dict):
            continue
        cmd = item.get("command")
        if not isinstance(cmd, str):
            continue
        dur = item.get("durationMs", 0)
        if isinstance(dur, bool) or not isinstance(dur, (int, float)):
            dur_int = 0
        else:
            dur_int = int(dur)
        out.append({"command": cmd, "durationMs": dur_int})
    return out


def _coerce_hook_errors(value: Any) -> int:
    """Normalize hookErrors (list / int / bool / str) → int count.

    Current observed data always has ``[]`` (empty list). Defensive
    handling covers future schema drift (see schema report §3.5).
    """
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped in ("[]", "''", '""'):
            return 0
        try:
            decoded = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            try:
                decoded = ast.literal_eval(stripped)
            except (ValueError, SyntaxError):
                return 0
        return _coerce_hook_errors(decoded)
    return 0


def _extract_usage(msg: dict) -> dict | None:
    """Extract token usage from message.usage, coercing all values to non-negative int.

    Returns None if usage is absent or not a dict. Per FR-7 / AC10 never-raise:
    bad field types (str, null, negative, float) are coerced to 0 silently.
    """
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None

    def _coerce(v: object) -> int:
        try:
            n = int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
        return max(0, n)

    return {
        "input_tokens": _coerce(u.get("input_tokens")),
        "output_tokens": _coerce(u.get("output_tokens")),
        "cache_creation_input_tokens": _coerce(u.get("cache_creation_input_tokens")),
        "cache_read_input_tokens": _coerce(u.get("cache_read_input_tokens")),
    }


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
                raw_line=_truncate(line),
            )
        ]
    if not isinstance(obj, dict):
        return [
            HarnessEvent(
                type=EventType.unknown,
                ts=datetime.now(timezone.utc),
                agent_id=None,
                payload={"reason": "not an object"},
                raw_line=_truncate(line),
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
    is_sidechain = bool(obj.get("isSidechain"))

    if top_type not in SUPPORTED_TOP_TYPES:
        log.debug("unknown top-level type: %r", top_type)
        return [
            HarnessEvent(
                type=EventType.unknown,
                ts=ts,
                agent_id=agent_id,
                payload={"top_type": top_type},
                raw_line=_truncate(line),
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
                raw_line=_truncate(line),
            )
        ]
    if top_type == "attachment":
        return [
            HarnessEvent(
                type=EventType.attachment,
                ts=ts,
                agent_id=agent_id,
                payload={"attachment": obj.get("attachment")},
                raw_line=_truncate(line),
            )
        ]
    if top_type == "permission-mode":
        return [
            HarnessEvent(
                type=EventType.permission_mode,
                ts=ts,
                agent_id=agent_id,
                payload={"mode": obj.get("permissionMode")},
                raw_line=_truncate(line),
            )
        ]

    if top_type == "system":
        subtype = obj.get("subtype")
        if subtype == "stop_hook_summary":
            hook_infos = _parse_hook_infos(obj.get("hookInfos"))
            hook_errors = _coerce_hook_errors(obj.get("hookErrors", 0))
            hook_count_raw = obj.get("hookCount", 0)
            if isinstance(hook_count_raw, bool) or not isinstance(
                hook_count_raw, (int, float)
            ):
                hook_count = 0
            else:
                hook_count = int(hook_count_raw)
            stop_reason_raw = obj.get("stopReason")
            stop_reason = (
                stop_reason_raw
                if isinstance(stop_reason_raw, str) and stop_reason_raw
                else None
            )
            tool_use_id_raw = obj.get("toolUseID")
            tool_use_id = (
                str(tool_use_id_raw) if tool_use_id_raw else None
            )
            return [
                HarnessEvent(
                    type=EventType.hook_summary,
                    ts=ts,
                    agent_id=agent_id,
                    payload={
                        "hook_count": hook_count,
                        "hook_errors": hook_errors,
                        "hook_infos": hook_infos,
                        "prevented_continuation": bool(
                            obj.get("preventedContinuation", False)
                        ),
                        "stop_reason": stop_reason,
                        "tool_use_id": tool_use_id,
                    },
                    raw_line=_truncate(line),
                )
            ]
        # Other system subtypes (turn_duration, compactMetadata, retry)
        # intentionally drop to unknown in v1.
        return [
            HarnessEvent(
                type=EventType.unknown,
                ts=ts,
                agent_id=agent_id,
                payload={"top_type": "system", "subtype": subtype},
                raw_line=_truncate(line),
            )
        ]

    if top_type == "queue-operation":
        op = obj.get("operation")
        content_str = obj.get("content")
        if op == "enqueue" and isinstance(content_str, str):
            m = _TASK_NOTIFICATION_RE.search(content_str)
            if m:
                return [
                    HarnessEvent(
                        type=EventType.task_notification,
                        ts=ts,
                        agent_id=agent_id,
                        payload={
                            "tool_use_id": m.group(1),
                            "status": m.group(2),
                            "duration_ms": int(m.group(3)),
                        },
                        raw_line=_truncate(line),
                    )
                ]
        return []

    # assistant / user rows: explode content[] blocks into events.

    # Extract row-level usage once, before the block loop (FR-1, FR-2, §4.1).
    # Only assistant rows carry message.usage; for user rows this returns None.
    if top_type == "assistant":
        msg_obj = obj.get("message")
        row_usage = _extract_usage(msg_obj) if isinstance(msg_obj, dict) else None
    else:
        row_usage = None

    blocks = _content_blocks(obj)
    if not blocks:
        # Assistant row with no content (empty message) — surface as message.
        kind = (
            EventType.assistant_message
            if top_type == "assistant"
            else EventType.user_message
        )
        payload_empty: dict[str, Any] = {"uuid": obj.get("uuid")}
        # Attach usage to this sole event if present (FR-2: first/only event).
        if row_usage is not None:
            payload_empty["usage"] = row_usage
        return [
            HarnessEvent(
                type=kind,
                ts=ts,
                agent_id=agent_id,
                payload=payload_empty,
                raw_line=_truncate(line),
            )
        ]

    out: list[HarnessEvent] = []
    first_event = True  # tracks whether we've attached usage yet (FR-2)
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
                    raw_line=_truncate(line),
                )
            )
            continue
        if bt == "tool_use":
            tool_name_raw = block.get("name")
            is_mcp = (
                isinstance(tool_name_raw, str)
                and tool_name_raw.startswith("mcp__")
            )
            block_payload: dict[str, Any] = {
                "tool_use_id": block.get("id"),
                "tool_name": tool_name_raw,
                "input": block.get("input"),
                "parent_tool_use_id": block.get("parent_tool_use_id"),
                "uuid": obj.get("uuid"),
                "is_mcp": is_mcp,
            }
            if first_event and row_usage is not None:
                block_payload["usage"] = row_usage
                first_event = False
            out.append(
                HarnessEvent(
                    type=EventType.tool_use,
                    ts=ts,
                    agent_id=agent_id,
                    payload=block_payload,
                    raw_line=_truncate(line),
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
            if first_event and row_usage is not None:
                payload["usage"] = row_usage
                first_event = False
            out.append(
                HarnessEvent(
                    type=EventType.tool_result,
                    ts=ts,
                    agent_id=agent_id,
                    payload=payload,
                    raw_line=_truncate(line),
                )
            )
        elif bt == "text":
            kind = (
                EventType.assistant_message
                if top_type == "assistant"
                else EventType.user_message
            )
            raw_text = block.get("text", "")
            if not isinstance(raw_text, str):
                raw_text = ""
            # Real user prompts get the relaxed cap so the full prompt
            # (XML wrappers, multi-line) is preserved for TurnSummaryScreen.
            # Assistant text, sidechain rows, and isMeta system-injected
            # user rows keep the legacy 500-char cap.
            if top_type == "user" and not is_meta and not is_sidechain:
                text_value = raw_text[:_USER_PROMPT_MAX_LEN]
            else:
                text_value = raw_text[:500]
            text_payload: dict[str, Any] = {
                "text": text_value,
                "is_meta": is_meta,
            }
            # Attach row-level usage to the first event of any block type (FR-2,
            # §3.3). Covers tool_use-only rows (29/70 observed). user rows never
            # carry row_usage (set to None above).
            if first_event and row_usage is not None:
                text_payload["usage"] = row_usage
                first_event = False
            out.append(
                HarnessEvent(
                    type=kind,
                    ts=ts,
                    agent_id=agent_id,
                    payload=text_payload,
                    raw_line=_truncate(line),
                )
            )
        elif bt == "thinking":
            thinking_payload: dict[str, Any] = {
                "thinking": (block.get("thinking") or "")[:500],
            }
            if first_event and row_usage is not None:
                thinking_payload["usage"] = row_usage
                first_event = False
            out.append(
                HarnessEvent(
                    type=EventType.thinking,
                    ts=ts,
                    agent_id=agent_id,
                    payload=thinking_payload,
                    raw_line=_truncate(line),
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
