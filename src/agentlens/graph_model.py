"""Pure data model for the live agent + skill call graph.

No Textual imports — this module is pure data + logic so it can be unit
tested without spinning up a UI.

Filtering policy (locked decision):
- Only `Task(subagent_type=...)` and `Skill(skill=...)` tool_use events
  become nodes.
- Regular tools (Read/Edit/Bash/etc.) are ignored.
- Same agent/skill type called multiple times from the same parent
  collapses into one node with a `call_count` counter.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from .events import EventType, HarnessEvent

NodeType = Literal["root", "agent", "skill"]
NodeStatus = Literal["running", "done", "error"]


@dataclass
class TurnRecord:
    """A user-prompt turn boundary."""
    index: int          # 0-based turn number
    start_ts: float     # timestamp of the user_message that started this turn
    end_ts: float | None = None  # timestamp of next turn's start (None = current/live)
    prompt_preview: str = ""     # first 40 chars of user prompt
    prompt_full: str = ""        # full user prompt (capped upstream by parser._USER_PROMPT_MAX_LEN)
    # Per-turn aggregates (all capped at MAX_BREAKDOWN_TOOLS for dict
    # fields; raw hookInfos snapshot capped at MAX_HOOK_INFOS). Agent /
    # Task / Skill and MCP tools are NOT counted in tool_calls.
    tool_calls: dict[str, int] = field(default_factory=dict)
    mcp_calls: dict[str, int] = field(default_factory=dict)
    hook_events: int = 0
    hook_errors: int = 0
    # command → {"count": int, "total_ms": int, "error_count": int,
    #            "event_type": str}
    hook_by_command: dict[str, dict] = field(default_factory=dict)
    hook_infos_raw: list[dict] = field(default_factory=list)
    # Counters for unique-name drops once the cap is hit. Used by
    # get_turn_summary() to populate the ``*_overflow`` fields so the UI
    # can render "+M more" suffixes. Track unique names, not total calls.
    tool_overflow_names: set[str] = field(default_factory=set)
    mcp_overflow_names: set[str] = field(default_factory=set)
    hook_overflow_names: set[str] = field(default_factory=set)
    # Token tracking (v0.8.0+). Per-turn totals across all assistant rows
    # attributed to this turn (main-session + linked subagent sessions).
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # Bare "main" bucket = main-session assistant usage NOT inside any
    # skill tool_use/tool_result span.
    token_main: dict = field(default_factory=lambda: {
        "input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
    })
    # Per-node breakdown. key = FlowRecord.node_id,
    # value = {"label": str, "node_type": "agent"|"skill",
    #          "tokens": {"input","output","cache_read","cache_create"}}
    token_nodes: dict = field(default_factory=dict)
    # Hierarchical skill → agent token tree (C안 / rev2).
    # key = skill_node_id,
    # value = {
    #   "label": str,
    #   "total": {"tokens": {input,output,cache_read,cache_create}},
    #   "agents": {
    #     agent_node_id: {"label": str, "tokens": {...}},
    #   },
    # }
    # The "total" bucket is a display-only sum (main skill body + all
    # child agents); it is NOT added to token_total to avoid double
    # counting (FR-5 / AC-4). Skill-body main usage continues to flow to
    # token_main per §5.4 of the design spec.
    token_skill_tree: dict = field(default_factory=dict)
    # Flat bucket for agents spawned outside any skill span.
    # key = agent_node_id, value = {"label": str, "tokens": {...}}
    token_agents_standalone: dict = field(default_factory=dict)
    # Per-turn individual tool events for timeline display (capped at
    # MAX_TOOL_EVENTS). Each entry:
    #   {"ts": float, "name": str, "agent_id": str, "tool_use_id": str,
    #    "status": "running"|"done"|"error", "duration_ms": int|None,
    #    # Detail capture (additive; never None — empty string when absent):
    #    "input_summary": str,    # max MAX_INPUT_SUMMARY chars
    #    "input_raw": str,        # max MAX_INPUT_RAW chars
    #    "output_preview": str,   # max MAX_OUTPUT_PREVIEW chars
    #    "is_error": bool}
    # Populated by _handle_tool_use / _handle_tool_result.
    tool_events: list[dict] = field(default_factory=list)
    # Lookup index: tool_use_id -> index in tool_events list.
    # Enables O(1) tool_result -> tool_use matching for duration update.
    _tool_event_index: dict[str, int] = field(default_factory=dict)

ROOT_ID = "main"

# Hard cap on total node count — protects against untrusted JSONL payloads
# blowing up memory. Root node does not count against the cap.
MAX_NODES = 500

# Maximum nested spawn depth from root. Root is level 0, direct children of
# main are level 1. Spawns that would exceed this depth are dropped.
MAX_NESTED_DEPTH = 5

# Max length of a user-supplied label (subagent_type / skill name) after
# sanitization.
MAX_LABEL_LEN = 64


def _sanitize_label(s: str) -> str:
    """Truncate to 64 chars and strip non-printable / control chars.

    Applied to any untrusted string before it becomes a node label or id.
    """
    if not isinstance(s, str):
        s = str(s)
    truncated = s[:MAX_LABEL_LEN]
    return "".join(
        c for c in truncated if c.isprintable() and c not in "\x1b\r\n\t"
    )


# Common prefixes that eat up display width without adding information.
# Stripped from node *labels* (not ids) so boxes show the meaningful name.
_LABEL_PREFIX_STRIPS = (
    "oh-my-claudecode:",
    "omc:",
)


def _display_label(name: str) -> str:
    """Shorten a fully-qualified agent/skill name for display in a node
    box. The full name is preserved in the node id so uniqueness is
    untouched; only the visible label changes.
    """
    for prefix in _LABEL_PREFIX_STRIPS:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


# Priority key order for input_summary extraction (FR-02). The first
# matching key in this tuple wins; this lets us surface the most-likely
# meaningful identifier (a path, a command, a query) without forcing
# the caller to pre-sort keys.
_INPUT_SUMMARY_PRIORITY_KEYS = (
    "file_path", "path", "command", "pattern",
    "prompt", "description", "query", "url",
)


def _strip_controls(s: str) -> str:
    """Remove ANSI escapes / control chars from a string (FR-05).

    Mirrors the defense applied in ``_sanitize_label`` / ``_sanitize_cell``
    but keeps the result multi-line capable (so input_raw / output_preview
    can preserve linebreaks for the modal viewer).
    """
    return _CONTROL_CHAR_RE.sub("", s)


def _build_input_summary(inp: object) -> str:
    """Single-line summary of ``tool_use.input`` for DataTable preview.

    Tries priority keys (FR-02) first; falls back to ``repr(input)`` for
    arbitrary dicts so something always renders. Capped at
    MAX_INPUT_SUMMARY chars and stripped of control chars. Never raises.
    """
    try:
        if inp is None:
            return ""
        if isinstance(inp, dict):
            for key in _INPUT_SUMMARY_PRIORITY_KEYS:
                if key in inp:
                    try:
                        val = inp[key]
                    except Exception:
                        continue
                    try:
                        text = str(val)
                    except Exception:
                        text = ""
                    text = _strip_controls(text).replace("\n", " ").replace("\t", " ")
                    return text[:MAX_INPUT_SUMMARY]
            try:
                text = repr(inp)
            except Exception:
                text = ""
            text = _strip_controls(text).replace("\n", " ").replace("\t", " ")
            return text[:MAX_INPUT_SUMMARY]
        try:
            text = str(inp)
        except Exception:
            try:
                text = repr(inp)
            except Exception:
                return ""
        text = _strip_controls(text).replace("\n", " ").replace("\t", " ")
        return text[:MAX_INPUT_SUMMARY]
    except Exception:
        return ""


def _build_input_raw(inp: object) -> str:
    """Full ``tool_use.input`` serialization for the modal Input section.

    Dicts are pretty-printed via ``json.dumps`` (FR-03) with ``default=str``
    so unserializable objects (datetime, custom types) don't blow up.
    Falls back to ``str(inp)`` then ``repr(inp)``. Capped at
    MAX_INPUT_RAW chars, control chars stripped. Never raises.
    """
    try:
        if inp is None:
            return ""
        if isinstance(inp, dict):
            try:
                raw = json.dumps(
                    inp, ensure_ascii=False, default=str, indent=2,
                )
            except Exception:
                try:
                    raw = repr(inp)
                except Exception:
                    return ""
        else:
            try:
                raw = str(inp)
            except Exception:
                try:
                    raw = repr(inp)
                except Exception:
                    return ""
        raw = _strip_controls(raw)
        return raw[:MAX_INPUT_RAW]
    except Exception:
        return ""


def _build_output_preview(content: object) -> str:
    """Normalize ``tool_result.content`` into a single string for the
    modal Output section (FR-04).

    Handles three observed shapes (per 01_schema_report.md §5):
      - str: passed through (sanitize + cap)
      - list[dict]: each block is reduced to its most informative known
        field. Priority: ``text`` > ``content`` > ``tool_name`` (for
        tool_reference blocks) > ``type``. Unknown blocks fall back to
        ``str(block)``.
      - other: ``str(content)`` fallback.

    Capped at MAX_OUTPUT_PREVIEW chars, control chars stripped.
    Never raises — bad input drops to empty string.
    """
    try:
        if content is None:
            return ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                try:
                    if isinstance(block, dict):
                        if "text" in block:
                            parts.append(str(block.get("text", "")))
                        elif "content" in block:
                            parts.append(str(block.get("content", "")))
                        elif "tool_name" in block:
                            btype = str(block.get("type", "block"))
                            parts.append(
                                f"[{btype}: {block.get('tool_name', '')}]"
                            )
                        elif "type" in block:
                            parts.append(f"[{block.get('type', '')}]")
                        else:
                            parts.append(str(block))
                    else:
                        parts.append(str(block))
                except Exception:
                    continue
            text = "\n".join(parts)
        else:
            try:
                text = str(content)
            except Exception:
                return ""
        text = _strip_controls(text)
        return text[:MAX_OUTPUT_PREVIEW]
    except Exception:
        return ""


# Maximum number of distinct tool types tracked per subagent breakdown.
# Caps memory growth when fed untrusted / extremely diverse payloads.
MAX_BREAKDOWN_TOOLS = 20

# Per-turn cap on raw hookInfos snapshot retention. Mirrors parser.MAX_HOOK_INFOS
# so a single turn can never retain an unbounded number of hook entries.
MAX_HOOK_INFOS = 50

# Per-turn cap on individual tool event entries for timeline display.
# Bounds memory growth from high-frequency tool turns.
MAX_TOOL_EVENTS = 200

# Per-event caps for tool_use/tool_result detail capture (FR-01..FR-04).
# Each cap bounds the *per-event* string length, not the total number of
# events (that is still bounded by MAX_TOOL_EVENTS). Together with
# MAX_TOOL_EVENTS=200 the worst-case per-turn memory is well under a few
# MB even for adversarial payloads.
MAX_INPUT_SUMMARY = 200
MAX_INPUT_RAW = 2048
MAX_OUTPUT_PREVIEW = 4096

# Single regex compiled once for control-char stripping in input/output
# capture (FR-05). Strips C0/C1 control chars; preserves printable text,
# tabs/newlines are dropped so DataTable cells stay single-line-safe for
# input_summary and so the raw/output blobs don't carry terminal escapes.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


# Text patterns Claude Code injects into user rows that LOOK like user
# prompts but are actually system-generated and must NOT be treated as
# turn boundaries by sticky-running flush. Match against the start of
# the message text after stripping leading whitespace.
_SYSTEM_USER_PREFIXES = (
    "<task-notification",      # background task completion notice
    "<system-reminder",        # tool/skill hint reminders
    "Base directory for this skill",  # skill expansion preamble
    "Caveat:",                 # internal caveats
    "<command-name>",          # slash command bookkeeping (without command-message)
    "<local-command-stdout>",  # bare local command output
    "<teammate-message",       # teammate callback responses (team pipeline)
)


def _is_real_user_prompt(ev: HarnessEvent) -> bool:
    """Heuristic for whether a user_message event is an actual user
    prompt (vs a system-injected message that happens to land on a user
    row). Only real prompts should flush the sticky-running turn.
    """
    if ev.payload.get("is_meta"):
        return False
    text = str(ev.payload.get("text") or "").lstrip()
    if not text:
        return False
    for prefix in _SYSTEM_USER_PREFIXES:
        if text.startswith(prefix):
            return False
    return True


@dataclass
class FlowRecord:
    """Permanent record of a single agent invocation for flow mode.

    Unlike Instance (which is turn-scoped and cleared on flush),
    FlowRecord lives for the entire session and is only cleared on
    session switch (clear()). This lets flow mode show the full
    execution history.
    """

    node_id: str  # base node id (e.g. "agent:executor")
    tool_use_id: str
    label: str  # display label (from _display_label)
    description: str  # Agent call description
    node_type: NodeType
    started_ts: float
    ended_ts: float | None = None
    status: NodeStatus = "running"
    turn_index: int = 0
    is_background: bool = False
    parent_node_id: str = ROOT_ID  # nested spawn의 부모 node_id; top-level은 기본값 ROOT_ID


@dataclass
class Instance:
    """A single live spawn of an agent node.

    Phase 1 of mode-dependent instance view: tracks per-spawn lifecycle so
    running-mode rendering can show one box per parallel invocation while
    all-mode keeps the aggregated view.
    """

    tool_use_id: str
    status: NodeStatus = "running"
    started_ts: float = 0.0
    ended_ts: float | None = None
    description: str = ""
    subagent_uuid: str | None = None
    turn_index: int = 0
    # Per-instance tool-call counts (Phase 2a). Mirrors the node-level
    # ``tool_breakdown`` aggregate but scoped to a single spawn so the
    # running-mode instance view can show distinct badges per parallel
    # invocation. Capped independently from the node aggregate so a
    # runaway instance can't poison the parent's badge.
    tool_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass
class Node:
    id: str
    label: str
    node_type: NodeType
    status: NodeStatus = "running"
    call_count: int = 1
    last_ts: float = 0.0
    # UUID-like id of the subagent JSONL file this Agent node was linked
    # to (via the 'agentId: ...' marker emitted in the main-session
    # tool_result). Only meaningful for agent nodes.
    subagent_uuid: str | None = None
    # Aggregated tool-call counts observed inside the linked subagent
    # conversation. Keyed by sanitized tool name.
    tool_breakdown: dict[str, int] = field(default_factory=dict)
    # Per-spawn instances keyed by tool_use_id. Only populated for top
    # level agent spawns (``_handle_tool_use``); nested spawns remain
    # aggregated in Phase 1. Insertion order = spawn order.
    _instances: dict[str, "Instance"] = field(default_factory=dict)


@dataclass
class Edge:
    parent_id: str
    child_id: str
    count: int = 1


@dataclass
class CallGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[tuple[str, str], Edge] = field(default_factory=dict)
    # tool_use_id -> node_id, for matching tool_result back to a node.
    _tool_use_to_node: dict[str, str] = field(default_factory=dict)
    # Subagent JSONL agentId -> node_id, so we can route internal
    # tool_use events from subagent files back to the existing Agent
    # node rather than spawning ghost nodes on the graph.
    _subagent_uuid_to_node: dict[str, str] = field(default_factory=dict)
    # subagent JSONL agentId -> (node_id, tool_use_id) of the Instance it
    # was linked to. Lets per-instance tool_breakdown (Phase 2a) attribute
    # subagent tool_use events to the specific spawn that produced them.
    _subagent_uuid_to_instance: dict[str, tuple[str, str]] = field(
        default_factory=dict
    )
    # Node ids that were touched (spawned/re-invoked) during the current
    # user turn. These remain displayed as "running" until the next
    # user_message event clears the set, so the user has time to observe
    # fast agents before they flip to done on screen.
    _current_turn: set[str] = field(default_factory=set)
    # Session-persistent flow history. NOT cleared on user_message flush;
    # only cleared on clear() (session switch). Lets flow mode show the
    # full execution history across all turns.
    _flow_history: list["FlowRecord"] = field(default_factory=list)
    _flow_tid_to_index: dict[str, int] = field(default_factory=dict)
    # Pending task-notification payloads for background agents whose
    # queue-operation row arrived before the assistant tool_use row.
    _pending_task_notifications: dict[str, dict] = field(default_factory=dict)
    # Session-lifetime snapshot: agent_node_id → skill_node_id, captured
    # at subagent spawn time when an active skill span is open. Enables
    # correct hierarchical attribution of asynchronously-arriving
    # subagent token usage to the originating skill even after the
    # skill span has closed (FR-3 / AC-5). NOT cleared on turn
    # boundaries — only on session switch (clear_turns currently keeps
    # this map; full session reset is external).
    _agent_to_skill: dict[str, str] = field(default_factory=dict)
    # OMC team agent linking maps (FR-OMC).
    # _pending_hex_to_type: hex agentId → OMC agentType, populated when
    #   SubagentWatcherManager discovers a file with a companion .meta.json.
    # _name_to_node: sanitized OMC agentType → node_id, populated when an
    #   Agent tool_use carries an `input.name` field (OMC team spawn).
    # Together they enable lazy resolution in _handle_assistant_usage when
    # a subagent uuid is not yet in _subagent_uuid_to_node.
    _pending_hex_to_type: dict[str, str] = field(default_factory=dict)
    _name_to_node: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._turns: list[TurnRecord] = []
        self._current_turn_index: int = -1  # -1 = no turns yet
        if ROOT_ID not in self.nodes:
            self.nodes[ROOT_ID] = Node(
                id=ROOT_ID,
                label="main",
                node_type="root",
                status="running",
            )

    def is_in_current_turn(self, node_id: str) -> bool:
        """Whether this node was touched during the current user turn.

        Used by rendering to keep recently-finished nodes visually
        'running' until the next user_message flushes the turn.
        """
        return node_id in self._current_turn

    def get_turns(self) -> list[TurnRecord]:
        return list(self._turns)

    def get_current_turn_index(self) -> int:
        return self._current_turn_index

    def clear_turns(self) -> None:
        """Reset turn tracking. Called on session switch."""
        self._turns.clear()
        self._current_turn_index = -1

    # ------------------------------------------------------------------
    def update_from_event(self, ev: HarnessEvent) -> bool:
        """Apply a single HarnessEvent to the graph.

        Returns True if the graph changed (a node added, edge added,
        counter incremented, or status updated). Never raises.
        """
        try:
            return self._update_inner(ev)
        except Exception:
            return False

    def _update_inner(self, ev: HarnessEvent) -> bool:
        if ev.type == EventType.tool_use:
            # Events originating from a subagent JSONL file carry a
            # 'subagent_uuid' payload field. Those must NOT create new
            # graph nodes; they only update the linked Agent node's
            # tool_breakdown.
            # tool_use-only assistant rows carry the row's usage on the
            # tool_use event (no text block precedes). Attribute first so
            # the skill-span lookup doesn't match the row's own FlowRecord
            # (not yet created at this point).
            if isinstance(ev.payload, dict) and ev.payload.get("usage") is not None:
                self._handle_assistant_usage(ev)
            if ev.payload.get("subagent_uuid"):
                return self._handle_subagent_tool_use(ev)
            return self._handle_tool_use(ev)
        if ev.type == EventType.tool_result:
            return self._handle_tool_result(ev)
        if ev.type == EventType.subagent_meta_link:
            return self._handle_subagent_meta_link(ev)
        if ev.type == EventType.task_notification:
            return self._handle_task_notification(ev)
        if ev.type == EventType.hook_summary:
            return self._handle_hook_summary(ev)
        if ev.type in (EventType.assistant_message, EventType.thinking):
            return self._handle_assistant_usage(ev)
        if ev.type == EventType.user_message:
            # Events originating from a subagent JSONL file carry a
            # subagent_uuid — these are the initial prompt the main
            # agent sent to the subagent, NOT real user input. They
            # must never flush the main graph's turn.
            if ev.payload.get("subagent_uuid"):
                return False
            if not _is_real_user_prompt(ev):
                # System-injected text (skill base directory notices,
                # background-task notifications, hook reminders, etc.)
                # arrives as user_message events but is NOT a real user
                # turn boundary. Skip the flush so sticky-running nodes
                # stay visible until the actual next user prompt.
                return False
            # Close the previous turn and open a new one.
            ts_epoch = ev.ts.timestamp() if ev.ts is not None else 0.0
            raw_text = str(ev.payload.get("text", ""))
            prompt_text = raw_text[:40]
            if self._turns:
                self._turns[-1].end_ts = ts_epoch
            self._turns.append(TurnRecord(
                index=len(self._turns),
                start_ts=ts_epoch,
                prompt_preview=prompt_text,
                prompt_full=raw_text,
            ))
            self._current_turn_index = len(self._turns) - 1
            # New user turn — flush the sticky-running set so nodes from
            # the previous turn transition to their real done/error state
            # on the next render. Also clear per-node _instances so the
            # running-mode instance view only shows spawns from the
            # *current* turn, not accumulated session history.
            changed = False
            if self._current_turn:
                self._current_turn.clear()
                changed = True
            for node in self.nodes.values():
                if node._instances:
                    node._instances.clear()
                    changed = True
            # Phase 2a: drop the instance index too so stale subagent_uuid
            # entries don't leak into the next turn. Node-level
            # tool_breakdown is intentionally preserved (all-mode session
            # persistence semantics).
            if self._subagent_uuid_to_instance:
                self._subagent_uuid_to_instance.clear()
                changed = True
            return changed
        return False

    def _handle_tool_use(self, ev: HarnessEvent) -> bool:
        tool = ev.tool_name
        # === Per-turn attribution MUST run before Agent/Task/Skill filter.
        # MCP and regular tool_use events would otherwise be dropped here
        # and never counted. subagent_uuid events are routed to
        # _handle_subagent_tool_use by the dispatcher, so this branch only
        # ever sees main-thread tool_use calls (D-2 preserved). Pre-first-
        # turn events (turn_index == -1) are silently skipped (D-3).
        is_mcp = bool(ev.payload.get("is_mcp", False))
        if self._current_turn_index >= 0 and isinstance(tool, str) and tool:
            try:
                turn = self._turns[self._current_turn_index]
            except IndexError:
                turn = None
            if turn is not None:
                if is_mcp:
                    self._bump_capped(
                        turn.mcp_calls, tool, turn.mcp_overflow_names
                    )
                elif tool not in ("Agent", "Task", "Skill"):
                    self._bump_capped(
                        turn.tool_calls, tool, turn.tool_overflow_names
                    )
                # --- tool_events timeline tracking ---
                if tool not in ("Agent", "Task", "Skill"):
                    tid_evt = ev.tool_use_id
                    if tid_evt and len(turn.tool_events) < MAX_TOOL_EVENTS:
                        ts_epoch_evt = (
                            ev.ts.timestamp() if ev.ts is not None else 0.0
                        )
                        # Detail capture (FR-01..FR-03). All three helpers
                        # are never-raise; wrap the append itself in
                        # try/except as a belt-and-suspenders against any
                        # future regression.
                        try:
                            raw_input = ev.payload.get("input")
                        except Exception:
                            raw_input = None
                        try:
                            input_summary = _build_input_summary(raw_input)
                        except Exception:
                            input_summary = ""
                        try:
                            input_raw = _build_input_raw(raw_input)
                        except Exception:
                            input_raw = ""
                        try:
                            turn.tool_events.append({
                                "ts": ts_epoch_evt,
                                "name": tool,
                                "agent_id": str(ev.agent_id or ""),
                                "tool_use_id": tid_evt,
                                "status": "running",
                                "duration_ms": None,
                                # New detail fields (additive).
                                "input_summary": input_summary,
                                "input_raw": input_raw,
                                "output_preview": "",
                                "is_error": False,
                            })
                            turn._tool_event_index[tid_evt] = (
                                len(turn.tool_events) - 1
                            )
                        except Exception:
                            pass  # never-raise
        # === end per-turn attribution ===

        # Claude Code historically used "Task" for subagent spawns; the
        # current harness uses "Agent". Both share the same input shape
        # (a `subagent_type` field), so we accept either.
        if tool not in ("Agent", "Task", "Skill"):
            return False
        inp = ev.payload.get("input")
        if not isinstance(inp, dict):
            return False
        if tool in ("Agent", "Task"):
            # OMC team agents carry an `input.name` field (e.g. "token-worker-a")
            # that is more specific than `subagent_type` ("general-purpose").
            # Prefer name > subagent_type > "general-purpose" so each OMC
            # team member gets its own flowchart node.
            raw_name = inp.get("name") or inp.get("subagent_type") or "general-purpose"
            child_name = _sanitize_label(str(raw_name))
            if not child_name:
                return False
            child_id = f"agent:{child_name}"
            label = _display_label(child_name)
            ntype: NodeType = "agent"
        else:  # Skill
            raw_name = inp.get("skill")
            if not raw_name:
                return False
            child_name = _sanitize_label(str(raw_name))
            if not child_name:
                return False
            child_id = f"skill:{child_name}"
            label = _display_label(child_name)
            ntype = "skill"

        # Parent must be a node that already exists. Unknown agent_ids
        # (session/subagent UUIDs that never became nodes) fall back to
        # the root — this prevents ghost UUID parents from appearing.
        raw_parent = ev.agent_id or ROOT_ID
        parent_id = raw_parent if raw_parent in self.nodes else ROOT_ID

        ts_epoch = ev.ts.timestamp() if ev.ts is not None else 0.0
        changed = False

        if child_id in self.nodes:
            node = self.nodes[child_id]
            node.call_count += 1
            node.last_ts = ts_epoch
            # Re-running counts as running again.
            node.status = "running"
            changed = True
        else:
            # Enforce node cap against untrusted payloads.
            if len(self.nodes) >= MAX_NODES + 1:  # +1 for root
                # Still record the tool_use_id mapping? No — the node
                # never existed, so tool_result has nothing to flip.
                return False
            self.nodes[child_id] = Node(
                id=child_id,
                label=label,
                node_type=ntype,
                status="running",
                call_count=1,
                last_ts=ts_epoch,
            )
            changed = True

        # Register OMC agent name → node_id for deferred subagent linking.
        # OMC team tool_results embed "agent_id: name@team" (not agentId: hex),
        # so the standard linked_subagent_uuid path never fires. We store the
        # name here so _handle_subagent_meta_link can resolve it later.
        if ntype == "agent":
            try:
                omc_name = inp.get("name")
                if omc_name and isinstance(omc_name, str):
                    self._name_to_node[_sanitize_label(omc_name)] = child_id
            except Exception:
                pass  # never-raise

        # Mark this node as belonging to the current user turn. It will
        # be kept visually "running" until the next user_message clears
        # the turn set.
        self._current_turn.add(child_id)

        # Snapshot skill→agent link at spawn time (FR-3 / AC-5).
        # Only agent spawns need this; a skill child is never attributed
        # under itself. Captured here regardless of parent-is-skill so
        # that any open skill span on the main session inherits.
        if ntype == "agent":
            try:
                if self._current_turn_index >= 0:
                    _turn = self._turns[self._current_turn_index]
                    active_skill = self._find_active_skill_span(
                        _turn, ts_epoch
                    )
                    if active_skill is not None:
                        self._agent_to_skill[child_id] = active_skill.node_id
            except Exception:
                pass  # never-raise

        edge_key = (parent_id, child_id)
        if edge_key in self.edges:
            self.edges[edge_key].count += 1
            changed = True
        else:
            self.edges[edge_key] = Edge(parent_id=parent_id, child_id=child_id, count=1)
            changed = True

        tid = ev.tool_use_id
        if tid:
            self._tool_use_to_node[tid] = child_id
            # Record a per-spawn Instance so running-mode can show one
            # box per live invocation. Only top-level agent spawns get
            # instances in Phase 1; nested spawns stay aggregated.
            desc = inp.get("description", "")
            if not isinstance(desc, str):
                desc = ""
            desc = desc[:MAX_LABEL_LEN]
            if ntype == "agent":
                self.nodes[child_id]._instances[tid] = Instance(
                    tool_use_id=tid,
                    status="running",
                    started_ts=ts_epoch,
                    description=desc,
                    turn_index=self._current_turn_index,
                )
            # Append a session-persistent FlowRecord for flow mode.
            # Cap at MAX_NODES to bound memory.
            if len(self._flow_history) <= MAX_NODES:
                rec = FlowRecord(
                    node_id=child_id,
                    tool_use_id=tid,
                    label=label,
                    description=desc,
                    node_type=ntype,
                    started_ts=ts_epoch,
                    turn_index=self._current_turn_index,
                    is_background=bool(inp.get("run_in_background")),
                )
                self._flow_history.append(rec)
                self._flow_tid_to_index[tid] = len(self._flow_history) - 1
                # Apply any pending task-notification that arrived before
                # this tool_use (race condition in JSONL write order).
                pending = self._pending_task_notifications.pop(tid, None)
                if pending is not None:
                    self._apply_task_notification(tid, pending)

        return changed

    def _handle_tool_result(self, ev: HarnessEvent) -> bool:
        tid = ev.tool_use_id
        if not tid:
            return False

        # --- tool_events timeline: update status/duration ---
        if self._current_turn_index >= 0:
            try:
                turn = self._turns[self._current_turn_index]
            except IndexError:
                turn = None
            if turn is not None:
                evt_idx = turn._tool_event_index.get(tid)
                if evt_idx is not None and evt_idx < len(turn.tool_events):
                    evt = turn.tool_events[evt_idx]
                    if evt.get("status") == "running":
                        is_error = bool(ev.payload.get("is_error", False))
                        evt["status"] = "error" if is_error else "done"
                        ts_epoch_res = (
                            ev.ts.timestamp() if ev.ts is not None else 0.0
                        )
                        start_ts = evt.get("ts", 0.0)
                        if ts_epoch_res > start_ts > 0:
                            evt["duration_ms"] = int(
                                (ts_epoch_res - start_ts) * 1000
                            )
                        # Detail capture (FR-04 / FR-10). Always wrap
                        # never-raise to defend against runaway content.
                        try:
                            evt["output_preview"] = _build_output_preview(
                                ev.payload.get("content")
                            )
                        except Exception:
                            evt["output_preview"] = ""
                        evt["is_error"] = is_error
        # --- end tool_events timeline ---

        node_id = self._tool_use_to_node.get(tid)
        if not node_id:
            return False
        node = self.nodes.get(node_id)
        if node is None:
            return False
        changed = False
        # Link the Agent node to its subagent JSONL file if the
        # tool_result embedded an agentId marker. Main-session-only: the
        # linked_subagent_uuid key is set by the parser when it sees the
        # "agentId: <hash>" line in tool_result content.
        linked = ev.payload.get("linked_subagent_uuid")
        if linked and node.node_type == "agent":
            if node.subagent_uuid != linked:
                node.subagent_uuid = linked
                changed = True
            if self._subagent_uuid_to_node.get(linked) != node.id:
                self._subagent_uuid_to_node[linked] = node.id
                changed = True
            # Phase 2a: also index the linked subagent_uuid back to the
            # specific Instance so subagent tool_use events can update
            # per-spawn breakdown. Only meaningful when the Instance
            # actually exists (top-level agent spawns).
            instance = node._instances.get(tid)
            if instance is not None:
                self._subagent_uuid_to_instance[linked] = (node.id, tid)
        new_status: NodeStatus = "error" if ev.is_error else "done"
        if node.status != new_status:
            node.status = new_status
            changed = True
        # Update the matching per-spawn instance (Phase 1: only top-level
        # agent spawns have instances; nested spawns skip this branch).
        # Idempotent: only touch the instance while it is still running
        # so a duplicate tool_result stays a no-op.
        instance = node._instances.get(tid)
        if instance is not None and instance.ended_ts is None:
            ts_epoch = ev.ts.timestamp() if ev.ts is not None else 0.0
            if instance.status != new_status:
                instance.status = new_status
                changed = True
            instance.ended_ts = ts_epoch
            changed = True
            if linked and instance.subagent_uuid != linked:
                instance.subagent_uuid = linked
                changed = True
        # Update the matching session-persistent FlowRecord.
        flow_idx = self._flow_tid_to_index.get(tid)
        if flow_idx is not None and flow_idx < len(self._flow_history):
            rec = self._flow_history[flow_idx]
            if rec.ended_ts is None:
                ts_epoch_flow = ev.ts.timestamp() if ev.ts is not None else 0.0
                rec.ended_ts = ts_epoch_flow
                rec.status = new_status
                changed = True
        return changed

    def _handle_task_notification(self, ev: HarnessEvent) -> bool:
        """Update FlowRecord from a background agent's task-notification.

        Claude Code writes a queue-operation (enqueue) JSONL row when a
        background agent completes. The parser extracts tool_use_id,
        status, and duration_ms from the embedded XML. We use these to
        overwrite the instant-ack ended_ts with the real completion time
        so fork/join detection works correctly for parallel agents.

        Race condition: the queue-operation row may appear in the JSONL
        *before* the assistant row containing the tool_use (different
        writers). When the FlowRecord doesn't exist yet, we stash the
        payload in ``_pending_task_notifications`` and apply it later
        when ``_handle_tool_use`` creates the record.
        """
        tid = ev.tool_use_id
        if not tid:
            return False
        flow_idx = self._flow_tid_to_index.get(tid)
        if flow_idx is None or flow_idx >= len(self._flow_history):
            # FlowRecord not created yet — stash for later.
            self._pending_task_notifications[tid] = dict(ev.payload)
            return False
        return self._apply_task_notification(tid, ev.payload)

    def _apply_task_notification(
        self, tid: str, payload: dict,
    ) -> bool:
        """Apply task-notification data to an existing FlowRecord."""
        flow_idx = self._flow_tid_to_index.get(tid)
        if flow_idx is None or flow_idx >= len(self._flow_history):
            return False
        rec = self._flow_history[flow_idx]
        duration_ms = payload.get("duration_ms")
        if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
            return False
        # Compute real ended_ts from started_ts + actual duration.
        real_ended = rec.started_ts + (duration_ms / 1000.0)
        rec.ended_ts = real_ended
        status_str = payload.get("status", "")
        if status_str == "completed":
            rec.status = "done"
        elif status_str in ("error", "failed"):
            rec.status = "error"
        # Also update the Instance if it still exists (live session).
        node = self.nodes.get(rec.node_id)
        if node is not None:
            inst = node._instances.get(tid)
            if inst is not None:
                inst.ended_ts = real_ended
                if status_str == "completed":
                    inst.status = "done"
                elif status_str in ("error", "failed"):
                    inst.status = "error"
        return True

    def _handle_subagent_meta_link(self, ev: HarnessEvent) -> bool:
        """Handle a subagent_meta_link event emitted by SubagentWatcherManager.

        Stores hex_id → agent_type in _pending_hex_to_type and eagerly
        resolves into _subagent_uuid_to_node when _name_to_node already has
        a matching entry. This handles OMC team agents whose tool_results
        carry "agent_id: name@team" instead of "agentId: <hex-hash>".
        """
        try:
            payload = ev.payload if isinstance(ev.payload, dict) else {}
            hex_id = payload.get("hex_id")
            agent_type = payload.get("agent_type")
            if not hex_id or not agent_type:
                return False
            hex_id = str(hex_id)
            agent_type = _sanitize_label(str(agent_type))
            if not agent_type:
                return False
            self._pending_hex_to_type[hex_id] = agent_type
            # Eagerly resolve if _name_to_node already has the entry.
            node_id = self._name_to_node.get(agent_type)
            if node_id and hex_id not in self._subagent_uuid_to_node:
                self._subagent_uuid_to_node[hex_id] = node_id
                return True
        except Exception:
            pass  # never-raise
        return False

    def _lazy_resolve_subagent_uuid(self, hex_id: str) -> str | None:
        """Deferred name-based link for OMC team agents.

        Called when a subagent_uuid is not yet in _subagent_uuid_to_node.
        Returns the node_id if both _pending_hex_to_type and _name_to_node
        have matching entries; caches the result in _subagent_uuid_to_node.
        Returns None if the link cannot be resolved yet.
        """
        try:
            agent_type = self._pending_hex_to_type.get(hex_id)
            if not agent_type:
                return None
            node_id = self._name_to_node.get(agent_type)
            if node_id:
                self._subagent_uuid_to_node[hex_id] = node_id  # cache
                return node_id
        except Exception:
            pass  # never-raise
        return None

    def _handle_subagent_tool_use(self, ev: HarnessEvent) -> bool:
        """Route an internal tool_use event from a subagent file.

        - Agent/Task/Skill tool_use events become new child nodes under
          the parent subagent's node (nested spawn). These are handled
          by ``_handle_nested_spawn`` and contribute to the flowchart
          tree, not to the parent's tool_breakdown badge.
        - All other tool_use events (Read/Edit/Bash/etc.) aggregate into
          the parent node's tool_breakdown.
        - Events whose subagent_uuid doesn't map to a known node are
          silently dropped — this can happen if the subagent file is
          seen before the main-session tool_result that links it.
        """
        # Nested spawn branch: Agent/Task/Skill inside a subagent file
        # become real child nodes rather than breakdown entries.
        if ev.tool_name in ("Agent", "Task", "Skill"):
            return self._handle_nested_spawn(ev)
        sub_uuid = ev.payload.get("subagent_uuid")
        if not sub_uuid:
            return False
        sub_uuid_str = str(sub_uuid)
        node_id = self._subagent_uuid_to_node.get(sub_uuid_str)
        if not node_id:
            node_id = self._lazy_resolve_subagent_uuid(sub_uuid_str)
        if not node_id:
            return False
        node = self.nodes.get(node_id)
        if node is None:
            return False
        raw_name = ev.tool_name
        if not raw_name:
            return False
        name = _sanitize_label(raw_name)
        if not name:
            return False
        breakdown = node.tool_breakdown
        node_changed = False
        if name in breakdown:
            breakdown[name] += 1
            node_changed = True
        elif len(breakdown) < MAX_BREAKDOWN_TOOLS:
            # Enforce per-node breakdown cap against untrusted payloads.
            breakdown[name] = 1
            node_changed = True
        # Phase 2a: also update the per-instance breakdown when the
        # subagent_uuid is indexed to a specific Instance. Cap is enforced
        # independently here so a runaway instance can't poison the node
        # aggregate or vice versa. Orphan instances (no link) skip this
        # branch silently and only contribute to the node-level total.
        inst_ref = self._subagent_uuid_to_instance.get(str(sub_uuid))
        if inst_ref is not None:
            n_id, t_id = inst_ref
            n = self.nodes.get(n_id)
            if n is not None:
                inst = n._instances.get(t_id)
                if inst is not None:
                    inst_breakdown = inst.tool_breakdown
                    if name in inst_breakdown:
                        inst_breakdown[name] += 1
                    elif len(inst_breakdown) < MAX_BREAKDOWN_TOOLS:
                        inst_breakdown[name] = 1
                    # if cap exceeded, silently drop — no error
        return node_changed

    def _handle_nested_spawn(self, ev: HarnessEvent) -> bool:
        """Create a child node for an Agent/Task/Skill spawn emitted
        inside a subagent JSONL file. The child is parented to the
        subagent's own node (not main), giving the flowchart a true
        nested tree. Drops events whose parent is unknown, exceeds
        MAX_NESTED_DEPTH, or would overflow MAX_NODES.
        """
        sub_uuid = ev.payload.get("subagent_uuid")
        if not sub_uuid:
            return False
        sub_uuid_str = str(sub_uuid)
        parent_id = self._subagent_uuid_to_node.get(sub_uuid_str)
        if not parent_id:
            parent_id = self._lazy_resolve_subagent_uuid(sub_uuid_str)
        if not parent_id:
            return False
        if parent_id not in self.nodes:
            return False

        tool = ev.tool_name
        inp = ev.payload.get("input")
        if not isinstance(inp, dict):
            return False
        if tool in ("Agent", "Task"):
            raw_name = inp.get("subagent_type") or "general-purpose"
            child_name = _sanitize_label(str(raw_name))
            if not child_name:
                return False
            child_id = f"agent:{child_name}"
            label = _display_label(child_name)
            ntype: NodeType = "agent"
        else:  # Skill
            raw_name = inp.get("skill")
            if not raw_name:
                return False
            child_name = _sanitize_label(str(raw_name))
            if not child_name:
                return False
            child_id = f"skill:{child_name}"
            label = _display_label(child_name)
            ntype = "skill"

        # Depth cap: parent's depth + 1 must not exceed MAX_NESTED_DEPTH.
        depths = self._compute_depths()
        parent_depth = depths.get(parent_id)
        if parent_depth is None:
            return False
        if parent_depth + 1 > MAX_NESTED_DEPTH:
            return False

        ts_epoch = ev.ts.timestamp() if ev.ts is not None else 0.0
        changed = False

        if child_id in self.nodes:
            node = self.nodes[child_id]
            node.call_count += 1
            node.last_ts = ts_epoch
            node.status = "running"
            changed = True
        else:
            if len(self.nodes) >= MAX_NODES + 1:  # +1 for root
                return False
            self.nodes[child_id] = Node(
                id=child_id,
                label=label,
                node_type=ntype,
                status="running",
                call_count=1,
                last_ts=ts_epoch,
            )
            changed = True

        self._current_turn.add(child_id)

        # Snapshot skill→agent link at nested spawn time (FR-3 / AC-5).
        # Mirrors the top-level spawn path: only agent children inherit
        # an enclosing skill span; skill children never attribute under
        # themselves.
        if ntype == "agent":
            try:
                if self._current_turn_index >= 0:
                    _turn = self._turns[self._current_turn_index]
                    active_skill = self._find_active_skill_span(
                        _turn, ts_epoch
                    )
                    if active_skill is not None:
                        self._agent_to_skill[child_id] = active_skill.node_id
            except Exception:
                pass  # never-raise

        edge_key = (parent_id, child_id)
        if edge_key in self.edges:
            self.edges[edge_key].count += 1
            changed = True
        else:
            self.edges[edge_key] = Edge(
                parent_id=parent_id, child_id=child_id, count=1
            )
            changed = True

        # Register tool_use_id → child node so the subsequent tool_result
        # (which carries linked_subagent_uuid when the nested agent has
        # its own subagent file) can attach the link to this node. The
        # global _tool_use_to_node map is flat, so _handle_tool_result
        # picks this up regardless of which file the result came from.
        tid = ev.tool_use_id
        if tid:
            self._tool_use_to_node[tid] = child_id

        # Create FlowRecord for nested spawn so flow mode can render the
        # child under its structural parent. Mirrors the _handle_tool_use
        # pattern; parent_node_id carries the real parent (not ROOT_ID).
        if tid and len(self._flow_history) < MAX_NODES:
            desc = ""
            raw_desc = inp.get("description")
            if isinstance(raw_desc, str):
                desc = raw_desc
            flow_rec = FlowRecord(
                node_id=child_id,
                tool_use_id=tid,
                label=label,
                description=desc,
                node_type=ntype,
                started_ts=ts_epoch,
                ended_ts=None,
                status="running",
                turn_index=(
                    self._current_turn_index
                    if self._current_turn_index >= 0 else 0
                ),
                is_background=False,
                parent_node_id=parent_id,
            )
            self._flow_history.append(flow_rec)
            self._flow_tid_to_index[tid] = len(self._flow_history) - 1
            changed = True

        return changed

    def get_subagent_tool_counts(self, node_id: str) -> dict[str, int]:
        """Return a copy of the node's tool_breakdown (mutation-safe)."""
        node = self.nodes.get(node_id)
        if node is None:
            return {}
        return dict(node.tool_breakdown)

    # ------------------------------------------------------------------
    def get_node_at_depth(self, depth: int) -> list[Node]:
        """Return nodes at a given BFS depth from root, in insertion order."""
        depths = self._compute_depths()
        return [
            self.nodes[nid]
            for nid in self.nodes.keys()
            if depths.get(nid) == depth
        ]

    def max_depth(self) -> int:
        depths = self._compute_depths()
        if not depths:
            return 0
        return max(depths.values())

    def compute_depths(self) -> dict[str, int]:
        """BFS depths from ROOT_ID. With parent-must-exist enforcement in
        _handle_tool_use, every node is reachable from root, so no
        fallback pinning is needed.
        """
        depths: dict[str, int] = {ROOT_ID: 0}
        # Build adjacency from edges in insertion order.
        adj: dict[str, list[str]] = {}
        for (p, c) in self.edges.keys():
            adj.setdefault(p, []).append(c)

        # BFS — use deque for O(1) popleft.
        queue: deque[str] = deque([ROOT_ID])
        visited: set[str] = {ROOT_ID}
        while queue:
            cur = queue.popleft()
            for child in adj.get(cur, []):
                if child in visited:
                    continue
                visited.add(child)
                depths[child] = depths[cur] + 1
                queue.append(child)

        return depths

    def get_flow_records_for_turn(self, turn_index: int) -> list[FlowRecord]:
        """Return FlowRecords belonging to a specific turn. Never raises."""
        try:
            return [r for r in self._flow_history if r.turn_index == turn_index]
        except Exception:
            return []

    def get_nodes_for_turn(self, turn_index: int) -> set[str]:
        """Return node_ids that have FlowRecords in the given turn. Never raises."""
        try:
            return {r.node_id for r in self._flow_history if r.turn_index == turn_index}
        except Exception:
            return set()

    def get_turn_summary(self, turn_index: int) -> dict[str, Any]:
        """Return a summary dict for a single turn.

        Keys:
          - index: 0-based turn index
          - prompt: truncated user prompt
          - start_ts / end_ts: floats (end_ts is None if this is the live turn)
          - duration_s: seconds (0 if end_ts unavailable)
          - agent_count, skill_count, error_count
          - agents: list of (label, description, duration_s, status, is_background)
          - total_agent_duration_s: sum of all child agent/skill durations
        Never raises.
        """
        try:
            turn = None
            for t in self._turns:
                if t.index == turn_index:
                    turn = t
                    break
            recs = self.get_flow_records_for_turn(turn_index)

            duration = 0.0
            if turn is not None:
                if turn.end_ts is not None:
                    duration = max(0.0, turn.end_ts - turn.start_ts)
                elif recs:
                    latest_end = max(
                        (r.ended_ts for r in recs if r.ended_ts is not None),
                        default=turn.start_ts,
                    )
                    duration = max(0.0, latest_end - turn.start_ts)

            agents: list[dict[str, Any]] = []
            agent_count = 0
            skill_count = 0
            error_count = 0
            total_agent_duration = 0.0
            for r in recs:
                if r.node_type == "agent":
                    agent_count += 1
                elif r.node_type == "skill":
                    skill_count += 1
                if r.status == "error":
                    error_count += 1
                dur = 0.0
                if r.ended_ts is not None:
                    dur = max(0.0, r.ended_ts - r.started_ts)
                total_agent_duration += dur
                agents.append({
                    "label": r.label,
                    "description": r.description,
                    "node_type": r.node_type,
                    "duration_s": dur,
                    "status": r.status,
                    "is_background": r.is_background,
                })

            # Per-turn tool / mcp / hook aggregates (additive only —
            # callers relying on the legacy keys still see identical
            # shape; new keys default to empty/zero when the turn has
            # no aggregates).
            tool_usage: list[dict[str, Any]] = []
            mcp_usage: list[dict[str, Any]] = []
            hook_usage: list[dict[str, Any]] = []
            tool_total = 0
            mcp_total = 0
            hook_runs_total = 0
            hook_error_total = 0
            hook_duration_ms = 0
            tool_overflow = (
                len(turn.tool_overflow_names) if turn is not None else 0
            )
            mcp_overflow = (
                len(turn.mcp_overflow_names) if turn is not None else 0
            )
            hook_overflow = (
                len(turn.hook_overflow_names) if turn is not None else 0
            )
            hook_total_events = 0
            if turn is not None:
                tool_items = sorted(
                    turn.tool_calls.items(),
                    key=lambda kv: (-kv[1], kv[0]),
                )
                tool_total = sum(turn.tool_calls.values())
                tool_usage = [
                    {"name": name, "count": count}
                    for name, count in tool_items
                ]

                mcp_items = sorted(
                    turn.mcp_calls.items(),
                    key=lambda kv: (-kv[1], kv[0]),
                )
                mcp_total = sum(turn.mcp_calls.values())
                for full_name, count in mcp_items:
                    server = ""
                    short = full_name
                    if full_name.startswith("mcp__"):
                        rest = full_name[len("mcp__"):]
                        idx = rest.find("__")
                        if idx >= 0:
                            server = rest[:idx]
                            short = rest[idx + 2:]
                        else:
                            server = rest
                            short = ""
                    mcp_usage.append({
                        "server": server,
                        "tool": short,
                        "full_name": full_name,
                        "count": count,
                    })

                hook_total_events = turn.hook_events
                hook_error_total = turn.hook_errors
                hook_items = sorted(
                    turn.hook_by_command.items(),
                    key=lambda kv: (
                        -int(kv[1].get("count", 0) or 0),
                        -int(kv[1].get("total_ms", 0) or 0),
                        str(kv[1].get("event_type", "")),
                        kv[0],
                    ),
                )
                for cmd, data in hook_items:
                    count = int(data.get("count", 0) or 0)
                    total_ms = int(data.get("total_ms", 0) or 0)
                    err_count = int(data.get("error_count", 0) or 0)
                    event_type = str(data.get("event_type", "Stop") or "Stop")
                    hook_runs_total += count
                    hook_duration_ms += total_ms
                    hook_usage.append({
                        "event": event_type,
                        "script": self._script_basename(cmd),
                        "command": cmd,
                        "count": count,
                        "error_count": err_count,
                        "total_ms": total_ms,
                    })

            return {
                "index": turn_index,
                "prompt": (
                    (turn.prompt_full or turn.prompt_preview)
                    if turn is not None else ""
                ),
                "start_ts": turn.start_ts if turn is not None else 0.0,
                "end_ts": turn.end_ts if turn is not None else None,
                "duration_s": duration,
                "agent_count": agent_count,
                "skill_count": skill_count,
                "error_count": error_count,
                "agents": agents,
                "total_agent_duration_s": total_agent_duration,
                # NEW (additive) — see design_spec §2.4 / §6
                "tool_usage": tool_usage,
                "mcp_usage": mcp_usage,
                "hook_usage": hook_usage,
                "hooks_configured": self._get_hooks_configured(),
                "tool_total": tool_total,
                "mcp_total": mcp_total,
                "hook_total": hook_total_events,
                "hook_runs": hook_runs_total,
                "hook_runs_total": hook_runs_total,
                "hook_errors_total": hook_error_total,
                "hook_error_total": hook_error_total,
                "hook_duration_ms": hook_duration_ms,
                "tool_overflow": tool_overflow,
                "mcp_overflow": mcp_overflow,
                "hook_overflow": hook_overflow,
                # Token usage (v0.8.0+)
                "token_total": {
                    "input": turn.input_tokens if turn is not None else 0,
                    "output": turn.output_tokens if turn is not None else 0,
                    "cache_read": (
                        turn.cache_read_input_tokens
                        if turn is not None else 0
                    ),
                    "cache_create": (
                        turn.cache_creation_input_tokens
                        if turn is not None else 0
                    ),
                },
                "token_main": (
                    dict(turn.token_main) if turn is not None else {
                        "input": 0, "output": 0,
                        "cache_read": 0, "cache_create": 0,
                    }
                ),
                "token_nodes": (
                    [
                        {
                            "label": v.get("label", ""),
                            "node_type": v.get("node_type", ""),
                            "tokens": dict(v.get("tokens", {})),
                        }
                        for v in turn.token_nodes.values()
                    ]
                    if turn is not None else []
                ),
                # Hierarchical token views (rev2 / D-15). Additive —
                # existing consumers keep reading the legacy keys.
                "token_skill_tree": (
                    [
                        {
                            "skill_node_id": skill_id,
                            "label": entry.get("label", ""),
                            "total": {
                                "tokens": dict(
                                    entry.get("total", {}).get("tokens", {})
                                ),
                            },
                            "agents": [
                                {
                                    "node_id": a_id,
                                    "label": a_entry.get("label", ""),
                                    "tokens": dict(
                                        a_entry.get("tokens", {})
                                    ),
                                }
                                for a_id, a_entry in (
                                    entry.get("agents", {}) or {}
                                ).items()
                            ],
                        }
                        for skill_id, entry in turn.token_skill_tree.items()
                    ]
                    if turn is not None else []
                ),
                "token_agents_standalone": (
                    [
                        {
                            "node_id": nid,
                            "label": entry.get("label", ""),
                            "tokens": dict(entry.get("tokens", {})),
                        }
                        for nid, entry in turn.token_agents_standalone.items()
                    ]
                    if turn is not None else []
                ),
                # Tool event timeline (per-event, chronological)
                "tool_timeline": (
                    sorted(
                        [dict(e) for e in turn.tool_events],
                        key=lambda e: e.get("ts", 0.0),
                    )
                    if turn is not None else []
                ),
            }
        except Exception:
            return {
                "index": turn_index,
                "prompt": "",
                "start_ts": 0.0,
                "end_ts": None,
                "duration_s": 0.0,
                "agent_count": 0,
                "skill_count": 0,
                "error_count": 0,
                "agents": [],
                "total_agent_duration_s": 0.0,
                "tool_usage": [],
                "mcp_usage": [],
                "hook_usage": [],
                "hooks_configured": False,
                "tool_total": 0,
                "mcp_total": 0,
                "hook_total": 0,
                "hook_runs": 0,
                "hook_runs_total": 0,
                "hook_errors_total": 0,
                "hook_error_total": 0,
                "hook_duration_ms": 0,
                "tool_overflow": 0,
                "mcp_overflow": 0,
                "hook_overflow": 0,
                "token_total": {
                    "input": 0, "output": 0,
                    "cache_read": 0, "cache_create": 0,
                },
                "token_main": {
                    "input": 0, "output": 0,
                    "cache_read": 0, "cache_create": 0,
                },
                "token_nodes": [],
                "token_skill_tree": [],
                "token_agents_standalone": [],
                "tool_timeline": [],
            }

    # ------------------------------------------------------------------
    # Per-turn tool / hook accumulators
    # ------------------------------------------------------------------
    @staticmethod
    def _bump_capped(
        d: dict[str, int],
        key: str,
        overflow: set[str] | None = None,
    ) -> None:
        """Increment ``d[key]``; drop unique name if cap hit.

        When a new key would exceed ``MAX_BREAKDOWN_TOOLS``, the name
        is remembered in ``overflow`` (if provided) so accessors can
        report how many unique names were suppressed. Repeat drops of
        the same name do not inflate the count (it is a set).
        """
        if key in d:
            d[key] += 1
        elif len(d) < MAX_BREAKDOWN_TOOLS:
            d[key] = 1
        elif overflow is not None:
            overflow.add(key)

    def _find_turn_by_timestamp(self, ts_epoch: float) -> int:
        """Return the turn_index whose ``start_ts <= ts < next.start_ts``.

        Linear reverse scan (turns typically <20 per session). Returns
        -1 for pre-first-turn events so the caller can drop them
        silently (D-3).
        """
        if not self._turns:
            return -1
        if ts_epoch < self._turns[0].start_ts:
            return -1
        for i in range(len(self._turns) - 1, -1, -1):
            if ts_epoch >= self._turns[i].start_ts:
                return i
        return -1

    def _handle_hook_summary(self, ev: HarnessEvent) -> bool:
        """Attribute a stop_hook_summary event to the turn whose time
        window contains the event timestamp. Pre-first-turn events are
        silently dropped.
        """
        ts_epoch = ev.ts.timestamp() if ev.ts is not None else 0.0
        turn_idx = self._find_turn_by_timestamp(ts_epoch)
        if turn_idx < 0:
            return False
        try:
            turn = self._turns[turn_idx]
        except IndexError:
            return False

        payload = ev.payload if isinstance(ev.payload, dict) else {}
        turn.hook_events += 1
        try:
            turn.hook_errors += int(payload.get("hook_errors", 0) or 0)
        except (TypeError, ValueError):
            pass

        stop_reason = payload.get("stop_reason")
        default_event_type = (
            stop_reason if isinstance(stop_reason, str) and stop_reason else "Stop"
        )

        hook_infos = payload.get("hook_infos")
        if not isinstance(hook_infos, list):
            hook_infos = []

        remaining_raw_cap = max(0, MAX_HOOK_INFOS - len(turn.hook_infos_raw))
        for info in hook_infos:
            if not isinstance(info, dict):
                continue
            cmd = info.get("command")
            if not isinstance(cmd, str) or not cmd:
                continue
            dur_raw = info.get("durationMs", 0)
            if isinstance(dur_raw, bool) or not isinstance(dur_raw, (int, float)):
                dur = 0
            else:
                dur = int(dur_raw)
            hook_event_name = info.get("hookEventName")
            event_type = (
                hook_event_name
                if isinstance(hook_event_name, str) and hook_event_name
                else default_event_type
            )
            entry = turn.hook_by_command.get(cmd)
            if entry is None:
                if len(turn.hook_by_command) >= MAX_BREAKDOWN_TOOLS:
                    turn.hook_overflow_names.add(cmd)
                    continue
                entry = {
                    "count": 0,
                    "total_ms": 0,
                    "error_count": 0,
                    "event_type": event_type,
                }
                turn.hook_by_command[cmd] = entry
            entry["count"] += 1
            entry["total_ms"] += dur
            if remaining_raw_cap > 0:
                turn.hook_infos_raw.append(
                    {"command": cmd, "durationMs": dur, "event_type": event_type}
                )
                remaining_raw_cap -= 1
        return True

    # ------------------------------------------------------------------
    # Token usage attribution (v0.8.0+)
    # ------------------------------------------------------------------
    @staticmethod
    def _accumulate_tokens(bucket: dict, usage: dict) -> None:
        """Accumulate the four canonical usage fields into ``bucket``.

        Keys are normalized from JSONL names
        (``input_tokens``/``output_tokens``/``cache_creation_input_tokens``/
        ``cache_read_input_tokens``) to the short internal names
        (``input``/``output``/``cache_create``/``cache_read``). Values are
        coerced to non-negative ints; bad types contribute 0 so the
        never-raise guarantee (AC10) holds.
        """
        def _coerce(v) -> int:
            try:
                n = int(v)
            except (TypeError, ValueError):
                return 0
            return n if n > 0 else 0

        bucket["input"] = bucket.get("input", 0) + _coerce(
            usage.get("input_tokens")
        )
        bucket["output"] = bucket.get("output", 0) + _coerce(
            usage.get("output_tokens")
        )
        bucket["cache_read"] = bucket.get("cache_read", 0) + _coerce(
            usage.get("cache_read_input_tokens")
        )
        bucket["cache_create"] = bucket.get("cache_create", 0) + _coerce(
            usage.get("cache_creation_input_tokens")
        )

    @staticmethod
    def _accumulate_total(turn: TurnRecord, usage: dict) -> None:
        """Accumulate into the TurnRecord's scalar totals."""
        def _coerce(v) -> int:
            try:
                n = int(v)
            except (TypeError, ValueError):
                return 0
            return n if n > 0 else 0

        turn.input_tokens += _coerce(usage.get("input_tokens"))
        turn.output_tokens += _coerce(usage.get("output_tokens"))
        turn.cache_creation_input_tokens += _coerce(
            usage.get("cache_creation_input_tokens")
        )
        turn.cache_read_input_tokens += _coerce(
            usage.get("cache_read_input_tokens")
        )

    def _accumulate_node(
        self,
        turn: TurnRecord,
        node_id: str,
        label: str,
        node_type: str,
        usage: dict,
    ) -> None:
        """Accumulate into ``turn.token_nodes[node_id]``, creating the
        entry on first touch. Insertion order is preserved so panel-side
        sorting is stable.
        """
        entry = turn.token_nodes.get(node_id)
        if entry is None:
            entry = {
                "label": label,
                "node_type": node_type,
                "tokens": {
                    "input": 0, "output": 0,
                    "cache_read": 0, "cache_create": 0,
                },
            }
            turn.token_nodes[node_id] = entry
        self._accumulate_tokens(entry["tokens"], usage)

    def _accumulate_skill_tree(
        self,
        turn: TurnRecord,
        skill_node_id: str,
        agent_node_id: str,
        usage: dict,
    ) -> None:
        """Accumulate subagent usage into the hierarchical skill tree
        bucket. Creates the skill entry + agent sub-entry on first touch.

        The ``total`` bucket is a display-only sum (skill body aggregate
        is kept in ``token_main`` per §5.4, so ``total`` here represents
        the sum of child-agent usage that UI renders on the skill row).

        Never raises — bad input drops silently.
        """
        try:
            skill_node = self.nodes.get(skill_node_id)
            skill_label = (
                skill_node.label if skill_node is not None else skill_node_id
            )
            agent_node = self.nodes.get(agent_node_id)
            agent_label = (
                agent_node.label if agent_node is not None else agent_node_id
            )
            skill_entry = turn.token_skill_tree.get(skill_node_id)
            if skill_entry is None:
                skill_entry = {
                    "label": skill_label,
                    "total": {
                        "tokens": {
                            "input": 0, "output": 0,
                            "cache_read": 0, "cache_create": 0,
                        },
                    },
                    "agents": {},
                }
                turn.token_skill_tree[skill_node_id] = skill_entry
            # Display-only aggregate.
            self._accumulate_tokens(skill_entry["total"]["tokens"], usage)
            agents_map = skill_entry.setdefault("agents", {})
            agent_entry = agents_map.get(agent_node_id)
            if agent_entry is None:
                agent_entry = {
                    "label": agent_label,
                    "tokens": {
                        "input": 0, "output": 0,
                        "cache_read": 0, "cache_create": 0,
                    },
                }
                agents_map[agent_node_id] = agent_entry
            self._accumulate_tokens(agent_entry["tokens"], usage)
        except Exception:
            return  # never-raise

    def _accumulate_standalone_agent(
        self,
        turn: TurnRecord,
        node_id: str,
        usage: dict,
    ) -> None:
        """Accumulate subagent usage into the flat standalone-agent
        bucket (no enclosing skill at spawn time).

        Never raises.
        """
        try:
            node = self.nodes.get(node_id)
            label = node.label if node is not None else node_id
            entry = turn.token_agents_standalone.get(node_id)
            if entry is None:
                entry = {
                    "label": label,
                    "tokens": {
                        "input": 0, "output": 0,
                        "cache_read": 0, "cache_create": 0,
                    },
                }
                turn.token_agents_standalone[node_id] = entry
            self._accumulate_tokens(entry["tokens"], usage)
        except Exception:
            return  # never-raise

    def _node_turn_index_for(self, node_id: str) -> int:
        """Return the turn_index of the first FlowRecord that spawned
        this node_id, or -1 if no record is found. Uses the existing
        ``_flow_history`` + ``_flow_tid_to_index`` infrastructure — no
        new bookkeeping required.
        """
        try:
            for rec in self._flow_history:
                if rec.node_id == node_id:
                    return rec.turn_index
        except Exception:
            return -1
        return -1

    def _find_active_skill_span(
        self, turn: TurnRecord, ts_epoch: float,
    ) -> "FlowRecord | None":
        """Return the first FlowRecord in ``turn`` whose time window
        [started_ts, ended_ts] (or open) contains ``ts_epoch`` and whose
        node_type is ``"skill"``. Returns None if no such span is active.
        """
        try:
            for rec in self._flow_history:
                if rec.turn_index != turn.index:
                    continue
                if rec.node_type != "skill":
                    continue
                if rec.started_ts > ts_epoch:
                    continue
                if rec.ended_ts is not None and ts_epoch > rec.ended_ts:
                    continue
                return rec
        except Exception:
            return None
        return None

    def _handle_assistant_usage(self, ev: HarnessEvent) -> bool:
        """Attribute a single ``assistant_message`` event's ``usage`` to
        the correct TurnRecord bucket(s).

        Row-level 1-time attachment is enforced upstream (parser.py):
        only the first event produced per assistant row carries
        ``payload["usage"]``. Subsequent events in the same row have no
        ``usage`` key and are skipped here.

        Routing:
          - subagent_uuid present → agent-side attribution (spawn-time
            turn, per-node bucket)
          - subagent_uuid absent  → main-side attribution. Within a
            ``skill`` tool_use/tool_result span → that skill's bucket;
            otherwise ``token_main``.

        Never raises; any mapping/lookup failure drops the event.
        """
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return False  # second event in a multi-block row, or no usage

        subagent_uuid = payload.get("subagent_uuid")
        if subagent_uuid:
            sub_uuid_str = str(subagent_uuid)
            node_id = self._subagent_uuid_to_node.get(sub_uuid_str)
            if not node_id:
                node_id = self._lazy_resolve_subagent_uuid(sub_uuid_str)
            if not node_id:
                return False  # subagent not yet linked — drop
            node = self.nodes.get(node_id)
            if node is None:
                return False
            turn_idx = self._node_turn_index_for(node_id)
            if turn_idx < 0 or turn_idx >= len(self._turns):
                return False
            turn = self._turns[turn_idx]
            self._accumulate_total(turn, usage)
            self._accumulate_node(
                turn, node_id, node.label, "agent", usage,
            )
            # Hierarchical routing (rev2 / D-15). If this agent was
            # spawned inside a skill span, attribute into the skill
            # tree; otherwise into the standalone bucket. token_nodes
            # above is kept as the legacy flat view (AC-1).
            try:
                skill_node_id = self._agent_to_skill.get(node_id)
                if skill_node_id:
                    self._accumulate_skill_tree(
                        turn, skill_node_id, node_id, usage,
                    )
                else:
                    self._accumulate_standalone_agent(
                        turn, node_id, usage,
                    )
            except Exception:
                pass  # never-raise
            return True

        # Main session path.
        if self._current_turn_index < 0:
            return False  # AC-12: pre-first-turn drop
        try:
            turn = self._turns[self._current_turn_index]
        except IndexError:
            return False
        self._accumulate_total(turn, usage)

        ts_epoch = ev.ts.timestamp() if ev.ts is not None else 0.0
        skill_rec = self._find_active_skill_span(turn, ts_epoch)
        if skill_rec is not None:
            self._accumulate_node(
                turn, skill_rec.node_id, skill_rec.label, "skill", usage,
            )
        else:
            self._accumulate_tokens(turn.token_main, usage)
        return True

    def _get_hooks_configured(self) -> bool:
        """Best-effort detection of ``.claude/settings.json`` hooks.

        Cached per CallGraph instance; file-system access happens at
        most once. Failure (missing file, bad JSON, permission error)
        conservatively returns False so the UI hides the Hooks section.
        """
        cached = getattr(self, "_hooks_configured_cache", None)
        if cached is not None:
            return cached
        result = False
        try:
            from pathlib import Path
            import json as _json
            p = Path(".claude") / "settings.json"
            if p.is_file():
                parsed = _json.loads(p.read_text(encoding="utf-8"))
                if isinstance(parsed, dict) and parsed.get("hooks"):
                    result = True
        except Exception:
            result = False
        self._hooks_configured_cache = result
        return result

    @staticmethod
    def _script_basename(cmd: str) -> str:
        """Extract a short display name from a hook command string.

        Hooks are typically ``node ... /path/script.mjs ARG1``. We pick
        the last whitespace-separated token that looks like a script
        path and return its basename. Fallback: the whole command.
        """
        if not isinstance(cmd, str):
            return ""
        tokens = cmd.strip().split()
        if not tokens:
            return cmd.strip()
        # Prefer the last token that ends in a code extension.
        script_exts = (".sh", ".mjs", ".cjs", ".js", ".ts", ".py")
        for tok in reversed(tokens):
            stripped = tok.strip('"\'')
            if stripped.endswith(script_exts):
                last_slash = max(
                    stripped.rfind("/"), stripped.rfind("\\")
                )
                return stripped[last_slash + 1:] if last_slash >= 0 else stripped
        # Fall back to basename of the last token.
        stripped = tokens[-1].strip('"\'')
        last_slash = max(stripped.rfind("/"), stripped.rfind("\\"))
        return stripped[last_slash + 1:] if last_slash >= 0 else stripped

    def _compute_depths(self) -> dict[str, int]:
        """Back-compat alias for compute_depths()."""
        return self.compute_depths()
