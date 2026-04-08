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

from dataclasses import dataclass, field
from typing import Literal

from .events import EventType, HarnessEvent

NodeType = Literal["root", "agent", "skill"]
NodeStatus = Literal["running", "done", "error"]

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
    # Node ids that were touched (spawned/re-invoked) during the current
    # user turn. These remain displayed as "running" until the next
    # user_message event clears the set, so the user has time to observe
    # fast agents before they flip to done on screen.
    _current_turn: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
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
            # New user turn — flush the sticky-running set so nodes from
            # the previous turn transition to their real done/error state
            # on the next render. Returns True only if the set actually
            # had anything to clear, otherwise the render can be skipped.
            if self._current_turn:
                self._current_turn.clear()
                return True
            return False
        return False

    def _handle_tool_use(self, ev: HarnessEvent) -> bool:
        tool = ev.tool_name
        # Claude Code historically used "Task" for subagent spawns; the
        # current harness uses "Agent". Both share the same input shape
        # (a `subagent_type` field), so we accept either.
        if tool not in ("Agent", "Task", "Skill"):
            return False
        inp = ev.payload.get("input")
        if not isinstance(inp, dict):
            return False
        if tool in ("Agent", "Task"):
            raw_name = inp.get("subagent_type")
            if not raw_name:
                return False
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
        new_status: NodeStatus = "error" if ev.is_error else "done"
        if node.status != new_status:
            node.status = new_status
            changed = True
        return changed

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
        if name in breakdown:
            breakdown[name] += 1
            return True
        # Enforce per-node breakdown cap against untrusted payloads.
        if len(breakdown) >= MAX_BREAKDOWN_TOOLS:
            return False
        breakdown[name] = 1
        return True

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
            raw_name = inp.get("subagent_type")
            if not raw_name:
                return False
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

    def _compute_depths(self) -> dict[str, int]:
        """BFS depths from ROOT_ID. With parent-must-exist enforcement in
        _handle_tool_use, every node is reachable from root, so no
        fallback pinning is needed.
        """
        depths: dict[str, int] = {ROOT_ID: 0}
        # Build adjacency from edges in insertion order.
        adj: dict[str, list[str]] = {}
        for (p, c) in self.edges.keys():
            adj.setdefault(p, []).append(c)

        # BFS — use a list as a queue, preserving insertion order.
        queue: list[str] = [ROOT_ID]
        visited: set[str] = {ROOT_ID}
        while queue:
            cur = queue.pop(0)
            for child in adj.get(cur, []):
                if child in visited:
                    continue
                visited.add(child)
                depths[child] = depths[cur] + 1
                queue.append(child)

        return depths
