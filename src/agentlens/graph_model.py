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


# Maximum number of distinct tool types tracked per subagent breakdown.
# Caps memory growth when fed untrusted / extremely diverse payloads.
MAX_BREAKDOWN_TOOLS = 20

# Per-turn cap on raw hookInfos snapshot retention. Mirrors parser.MAX_HOOK_INFOS
# so a single turn can never retain an unbounded number of hook entries.
MAX_HOOK_INFOS = 50


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
            if ev.payload.get("subagent_uuid"):
                return self._handle_subagent_tool_use(ev)
            return self._handle_tool_use(ev)
        if ev.type == EventType.tool_result:
            return self._handle_tool_result(ev)
        if ev.type == EventType.task_notification:
            return self._handle_task_notification(ev)
        if ev.type == EventType.hook_summary:
            return self._handle_hook_summary(ev)
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
            prompt_text = str(ev.payload.get("text", ""))[:40]
            if self._turns:
                self._turns[-1].end_ts = ts_epoch
            self._turns.append(TurnRecord(
                index=len(self._turns),
                start_ts=ts_epoch,
                prompt_preview=prompt_text,
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

        # Mark this node as belonging to the current user turn. It will
        # be kept visually "running" until the next user_message clears
        # the turn set.
        self._current_turn.add(child_id)

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
        node_id = self._subagent_uuid_to_node.get(str(sub_uuid))
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
        parent_id = self._subagent_uuid_to_node.get(str(sub_uuid))
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
                "prompt": turn.prompt_preview if turn is not None else "",
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
