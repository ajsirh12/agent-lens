"""FlowchartPanel — live ASCII flowchart of agent + skill calls.

Supports two orientations (top-down / left-right) and two display modes
(all nodes / running only). Toggle via ``toggle_orientation()`` and
``toggle_mode()`` — the app binds these to keys.
"""

from __future__ import annotations

from typing import Any, Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import Static

from ..events import HarnessEvent
from ..flowchart_layout import (
    LayoutResult,
    NodePos,
    layout_leftright,
    layout_topdown,
)
from ..graph_model import ROOT_ID, CallGraph, Edge, Node
from ..messages import HarnessEventMessage

STATUS_STYLE = {
    "running": "bright_green",
    "done": "dim",
    "error": "red",
}

# Short 2-char abbreviations for the subagent tool-breakdown badge shown
# beneath agent labels. Unknown tools fall back to their first 2 chars.
_TOOL_ABBREV = {
    "Read": "Rd",
    "Edit": "Ed",
    "Bash": "Bs",
    "Grep": "Gp",
    "Glob": "Gl",
    "Write": "Wr",
}


def _format_tool_breakdown(breakdown: dict[str, int], inner_w: int) -> str:
    """Compact badge text like 'Rd12 Ed5 Bs2' that fits in ``inner_w``.

    Tools are shown in descending count order. If the full string won't
    fit, items are dropped from the tail and a trailing '+N' tally is
    appended to indicate the remainder.
    """
    if not breakdown or inner_w <= 0:
        return ""
    items = sorted(breakdown.items(), key=lambda kv: (-kv[1], kv[0]))
    parts: list[str] = []
    for name, count in items:
        abbrev = _TOOL_ABBREV.get(name) or (name[:2] if name else "?")
        parts.append(f"{abbrev}{count}")
    # Greedily fit parts; if any don't fit, append +N remainder tally.
    shown: list[str] = []
    remainder = 0
    for i, piece in enumerate(parts):
        candidate = " ".join(shown + [piece])
        if len(candidate) <= inner_w:
            shown.append(piece)
        else:
            remainder = len(parts) - i
            break
    if remainder > 0:
        tally = f"+{remainder}"
        while shown:
            candidate = " ".join(shown + [tally])
            if len(candidate) <= inner_w:
                return candidate
            shown.pop()
        # Nothing fits alongside the tally — just the tally, truncated.
        return tally[:inner_w]
    return " ".join(shown)[:inner_w]

Orientation = Literal["topdown", "leftright"]
Mode = Literal["all", "running", "flow"]


class FlowchartPanel(ScrollableContainer):
    """Scrollable panel that owns a CallGraph + layout + rendered canvas.

    Inherits from ``ScrollableContainer`` so large flowcharts get
    automatic horizontal + vertical scrollbars. Mouse wheel scrolls
    vertically; Shift+wheel scrolls horizontally. When focused, PgUp /
    PgDn / Home / End and arrow keys scroll the view.
    """

    DEFAULT_CSS = ""

    can_focus = True

    def __init__(
        self,
        *,
        id: str | None = None,
        orientation: Orientation = "leftright",
        mode: Mode = "all",
    ) -> None:
        super().__init__(id=id)
        self._graph = CallGraph()
        self._orientation: Orientation = orientation
        self._mode: Mode = mode
        self._virtual_to_tid: dict[str, str] = {}
        self._selected_tool_use_id: str | None = None
        self._selected_flow_vid: str | None = None
        self._active_turn: int | None = None
        self._layout: LayoutResult = self._compute_layout()
        self._canvas: Static | None = None
        self._updating = False

    def compose(self) -> ComposeResult:
        self._canvas = Static(self._render_text(), id="flowchart-canvas")
        yield self._canvas

    def on_mount(self) -> None:
        try:
            self.watch(self.app, "selected_agent_id", self._on_app_agent_changed)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def on_harness_event_message(self, message: HarnessEventMessage) -> None:
        self.add_event(message.event)

    def add_event(self, ev: HarnessEvent) -> None:
        changed = self._graph.update_from_event(ev)
        if changed:
            self._layout = self._compute_layout()
            self._refresh_canvas()

    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Reset the graph + layout to a fresh root-only state."""
        self._graph = CallGraph()
        self._virtual_to_tid = {}
        self._selected_tool_use_id = None
        self._selected_flow_vid = None
        self._active_turn = None
        self._layout = self._compute_layout()
        self._refresh_canvas()

    def get_node_count(self) -> int:
        return len(self._layout.nodes)

    def get_edge_count(self) -> int:
        return len(self._layout.edges)

    def get_orientation(self) -> Orientation:
        return self._orientation

    def get_mode(self) -> Mode:
        return self._mode

    def toggle_orientation(self) -> Orientation:
        self._orientation = "topdown" if self._orientation == "leftright" else "leftright"
        self._layout = self._compute_layout()
        self._refresh_canvas()
        return self._orientation

    def toggle_mode(self) -> Mode:
        if self._mode == "all":
            self._mode = "running"
        elif self._mode == "running":
            self._mode = "flow"
        else:
            self._mode = "all"
        self._layout = self._compute_layout()
        self._refresh_canvas()
        return self._mode

    # ------------------------------------------------------------------
    def _compute_layout(self) -> LayoutResult:
        """Filter the graph by current mode, then lay it out."""
        graph = self._graph
        if self._mode == "all":
            if self._active_turn is not None:
                graph = self._all_subgraph()
            # else: use self._graph as-is (current behavior)
        elif self._mode == "running":
            graph = self._running_subgraph()
        elif self._mode == "flow":
            graph = self._flow_subgraph()  # handles _active_turn internally
        if self._orientation == "leftright":
            return layout_leftright(graph)
        return layout_topdown(graph)

    def _running_subgraph(self) -> CallGraph:
        """Return a CallGraph containing only the root and any node
        considered 'live' for the current turn. A node is live if its
        actual status is 'running' OR it was touched in the current
        turn (fast agents that already flipped to 'done' but the user
        hasn't started a new turn yet).

        Phase 1 mode-dependent instance view: if a surviving node has
        tracked ``_instances`` (only top-level agent spawns do), expand
        it into one virtual node per instance so parallel invocations
        show up as separate boxes. Nodes without instances (root, nested
        children, skills) pass through unchanged.
        """
        sub = CallGraph()
        # Clear per-rebuild so stale virtual ids from prior layouts
        # don't leak into subsequent click lookups.
        self._virtual_to_tid.clear()
        keep: set[str] = {ROOT_ID}
        for nid, node in self._graph.nodes.items():
            if nid == ROOT_ID:
                continue
            if node.status == "running" or self._graph.is_in_current_turn(nid):
                keep.add(nid)

        # Track virtual ids grouped by their original node id so edge
        # rewiring below can fan out to every instance.
        expansions: dict[str, list[str]] = {}
        for nid in keep:
            if nid == ROOT_ID:
                continue
            node = self._graph.nodes[nid]
            # Only expand when there are 2+ parallel instances —
            # single-spawn agents keep their canonical id (and existing
            # tests / cross-highlight behavior) unchanged.
            if node.node_type == "agent" and len(node._instances) >= 2:
                vids: list[str] = []
                for tid, inst in node._instances.items():
                    # Claude Code tool_use_ids are of the form
                    # ``toolu_<random>``. Slicing ``tid[:6]`` gives the
                    # same ``toolu_`` prefix for every call, which
                    # collapses all instances to one virtual node via
                    # dict-key collision. Use the TAIL of the id so
                    # each suffix is unique.
                    short_tid = tid[-8:] if len(tid) >= 8 else tid
                    vid = f"{nid}#{short_tid}"
                    sub.nodes[vid] = Node(
                        id=vid,
                        label=node.label,
                        node_type="agent",
                        status=inst.status,
                        call_count=1,
                        last_ts=inst.started_ts,
                        tool_breakdown=dict(inst.tool_breakdown),
                    )
                    vids.append(vid)
                    # Remember the FULL tool_use_id so action_drill_down
                    # can look up the correct Instance on this node.
                    self._virtual_to_tid[vid] = tid
                expansions[nid] = vids
            else:
                sub.nodes[nid] = node

        # Re-add edges. For expanded children, fan out to every virtual
        # instance. If the true parent was filtered out, re-parent onto
        # root so the child stays visible.
        for (pid, cid), edge in self._graph.edges.items():
            if cid not in keep:
                continue
            src = pid if pid in sub.nodes or pid == ROOT_ID else ROOT_ID
            if src != ROOT_ID and src not in sub.nodes:
                src = ROOT_ID
            child_targets = expansions.get(cid, [cid])
            for tgt in child_targets:
                sub.edges[(src, tgt)] = Edge(
                    parent_id=src, child_id=tgt, count=1
                )
        return sub

    def _flow_subgraph(self) -> CallGraph:
        """Build a temporal execution DAG from session-persistent FlowRecords.

        Each invocation becomes its own Node. Temporal edges connect
        each node to the most recently completed predecessor -- the
        latest node whose ended_ts <= this node's started_ts. This
        naturally produces forks for parallel spawns and joins for
        sequential continuations.

        Unlike the old Instance-based approach, FlowRecords survive
        user_message flushes so the full session history is preserved.

        When ``self._active_turn`` is set, only FlowRecords matching
        that turn_index are included. The ``completed`` predecessor
        list operates only on the filtered subset to prevent cross-turn
        edges.
        """
        sub = CallGraph()

        history = self._graph._flow_history
        if not history:
            return sub

        # Always filter by turn — LIVE shows the current turn only
        # (not the entire session), past turns are browsable via [/].
        turn = self._active_turn
        if turn is None:
            turn = self._graph.get_current_turn_index()
        if turn >= 0:
            history = [r for r in history if r.turn_index == turn]
            if not history:
                return sub

        # Track completed nodes in chronological order of their end time.
        # Two lists: foreground completions (usable by any successor) and
        # all completions including background (usable only by non-background).
        # Background agents spawned together should share a parent, not
        # chain through each other.
        #
        # Each entry: (ended_ts, vid).
        completed_fg: list[tuple[float, str]] = []  # foreground only
        completed_all: list[tuple[float, str]] = []  # fg + background

        _MIN_REAL_DURATION = 0.5

        for i, rec in enumerate(history):
            vid = f"{rec.node_id}@{i}"
            flow_label = rec.description if rec.description else rec.label
            sub.nodes[vid] = Node(
                id=vid,
                label=flow_label,
                node_type=rec.node_type,
                status=rec.status,
                call_count=1,
                last_ts=rec.started_ts,
            )

            # Find parent: the most recently COMPLETED node whose
            # end time is <= this node's start time.
            # Background agents only look at foreground completions so
            # parallel background spawns share the same parent instead
            # of chaining through each other.
            parent = ROOT_ID
            source = completed_fg if rec.is_background else completed_all
            for end_ts, completed_vid in reversed(source):
                if end_ts <= rec.started_ts:
                    parent = completed_vid
                    break

            sub.edges[(parent, vid)] = Edge(
                parent_id=parent, child_id=vid, count=1,
            )

            # Add to completed lists if genuinely finished.
            if rec.ended_ts is not None and (rec.ended_ts - rec.started_ts) >= _MIN_REAL_DURATION:
                entry = (rec.ended_ts, vid)
                completed_all.append(entry)
                completed_all.sort(key=lambda x: x[0])
                if not rec.is_background:
                    completed_fg.append(entry)
                    completed_fg.sort(key=lambda x: x[0])

        return sub

    def _all_subgraph(self) -> CallGraph:
        """Return a CallGraph filtered to nodes active in ``_active_turn``.

        Only nodes whose canonical id appears in ``get_nodes_for_turn()``
        (plus ROOT_ID) survive. Edges whose source is outside the
        surviving set are re-parented to ROOT_ID so the child stays
        visible. Mirrors the re-parenting pattern of ``_running_subgraph``.
        """
        turn = self._active_turn
        if turn is None:
            return self._graph

        keep_ids = self._graph.get_nodes_for_turn(turn)
        keep_ids.add(ROOT_ID)

        sub = CallGraph()
        for nid, node in self._graph.nodes.items():
            if nid in keep_ids:
                sub.nodes[nid] = node

        for (pid, cid), edge in self._graph.edges.items():
            if cid not in keep_ids:
                continue
            src = pid if pid in keep_ids else ROOT_ID
            sub.edges[(src, cid)] = Edge(
                parent_id=src, child_id=cid, count=edge.count,
            )

        return sub

    @staticmethod
    def _base_node_id(nid: str | None) -> str | None:
        """Strip the ``#<tid>`` or ``@<seq>`` suffix added to virtual nodes.

        Running-mode may expand a node ``agent:executor`` into virtual
        ids like ``agent:executor#a1b2c3``. Flow-mode uses
        ``agent:executor@0``. Cross-highlight uses the canonical base
        id so timeline matching works without changes.
        """
        if not nid:
            return nid
        # Strip both virtual-instance (#tid) and flow-sequence (@seq)
        # suffixes to recover the canonical base node id.
        base = nid.split("#", 1)[0]
        base = base.split("@", 1)[0]
        return base

    # ------------------------------------------------------------------
    def _refresh_canvas(self) -> None:
        if self._canvas is None:
            return
        self._canvas.update(self._render_text())
        # Update border color and title based on turn filter state.
        if self._active_turn is not None:
            turns = self._graph.get_turns()
            total = len(turns)
            try:
                self.styles.border = ("solid", "yellow")
            except Exception:
                pass
            self.border_title = f"Turn {self._active_turn + 1}/{total}"
        else:
            try:
                self.styles.border = ("solid", "$accent")
            except Exception:
                pass
            self.border_title = ""
        # Force re-layout so the Container re-measures its child's size when
        # the flowchart grows beyond its initial canvas dimensions. Without
        # this, Static.update() replaces the content but Textual keeps the
        # old measured size, so new nodes get clipped / not repainted.
        try:
            self._canvas.refresh(layout=True)
            self.refresh(layout=True)
        except Exception:
            pass

    def _render_text(self) -> Text:
        layout = self._layout
        w = max(1, layout.canvas_width)
        h = max(1, layout.canvas_height)

        # 2D grid of (char, style) cells.
        grid: list[list[tuple[str, str]]] = [
            [(" ", "") for _ in range(w)] for _ in range(h)
        ]

        # Draw edges first (so node borders overwrite edge endpoints cleanly).
        for edge in layout.edges:
            for (r, c) in edge.points:
                if 0 <= r < h and 0 <= c < w:
                    cur = grid[r][c][0]
                    ch = "│"
                    if cur == "─" or cur == "│":
                        ch = "┼" if cur != ch else cur
                    grid[r][c] = (ch, "")
            # Re-walk to draw horizontal segments properly.
            for i, (r, c) in enumerate(edge.points):
                if i == 0:
                    continue
                pr, pc = edge.points[i - 1]
                if pr == r and pc != c:
                    if 0 <= r < h and 0 <= c < w:
                        grid[r][c] = ("─", "")

        # Empty turn placeholder: when filtered to a turn with no tool
        # calls, the layout has only the root node and no edges. Show a
        # dimmed hint so the user knows the turn is empty, not broken.
        if (
            self._active_turn is not None
            and len(layout.nodes) <= 1
            and len(layout.edges) == 0
        ):
            placeholder = "(no tool calls in this turn)"
            row = 6  # below root box
            col = max(0, (w - len(placeholder)) // 2)
            for i, ch in enumerate(placeholder):
                if col + i < w and row < h:
                    grid[row][col + i] = (ch, "dim")

        # Draw node boxes.
        selected = None
        try:
            selected = self.app.selected_agent_id  # type: ignore[attr-defined]
        except Exception:
            selected = None
        selected_base = self._base_node_id(selected)
        selected_tid = self._selected_tool_use_id
        for nid, pos in layout.nodes.items():
            node_base = self._base_node_id(nid)
            base_match = node_base == selected_base and selected_base is not None
            # When the user clicked a virtual instance box, highlight
            # ONLY that exact instance — otherwise two parallel spawns
            # of the same type both light up and the user can't tell
            # which one they clicked. Selection coming from the
            # timeline (no tid recorded) still falls back to base-id
            # matching so every relevant instance lights up.
            if self._selected_flow_vid is not None and "@" in nid:
                highlight = nid == self._selected_flow_vid
            elif selected_tid is not None and "#" in nid:
                highlight = self._virtual_to_tid.get(nid) == selected_tid
            else:
                highlight = base_match
            self._draw_box(grid, pos, highlight=highlight)

        # Convert grid to a single Text.
        text = Text()
        for row_idx, row in enumerate(grid):
            for (ch, style) in row:
                if style:
                    text.append(ch, style=style)
                else:
                    text.append(ch)
            if row_idx < h - 1:
                text.append("\n")
        return text

    def _draw_box(
        self,
        grid: list[list[tuple[str, str]]],
        pos: NodePos,
        *,
        highlight: bool,
    ) -> None:
        node = pos.node
        h = len(grid)
        w = len(grid[0]) if grid else 0
        r0, c0 = pos.row, pos.col
        bw, bh = pos.width, pos.height
        if bh < 3 or bw < 3:
            return

        # Effective status: nodes touched in the current turn keep the
        # running color even if their tool_result already flipped them
        # to done/error. They'll flush to their real status on the next
        # user_message event.
        # Effective status: nodes touched in the current turn keep the
        # running color even if their tool_result already flipped them
        # to done/error. Virtual instance nodes (``agent:name#tid``)
        # must check the BASE id against _current_turn since the raw
        # graph only knows the canonical form.
        effective_status = node.status
        if self._mode != "flow":
            base_id = self._base_node_id(node.id)
            if base_id and self._graph.is_in_current_turn(base_id):
                effective_status = "running"
        style = STATUS_STYLE.get(effective_status, "")
        if highlight:
            style = (style + " bold reverse").strip()

        def put(r: int, c: int, ch: str) -> None:
            if 0 <= r < h and 0 <= c < w:
                grid[r][c] = (ch, style)

        # Top border
        put(r0, c0, "┌")
        for c in range(c0 + 1, c0 + bw - 1):
            put(r0, c, "─")
        put(r0, c0 + bw - 1, "┐")
        # Side borders + interior space
        for r in range(r0 + 1, r0 + bh - 1):
            put(r, c0, "│")
            for c in range(c0 + 1, c0 + bw - 1):
                put(r, c, " ")
            put(r, c0 + bw - 1, "│")
        # Bottom border
        put(r0 + bh - 1, c0, "└")
        for c in range(c0 + 1, c0 + bw - 1):
            put(r0 + bh - 1, c, "─")
        put(r0 + bh - 1, c0 + bw - 1, "┘")

        # Label inside the box. Two lines max: label + count.
        inner_w = bw - 2
        label = node.label
        if len(label) > inner_w:
            label = label[: max(0, inner_w - 1)] + "…"
        label_row = r0 + 1
        label_col = c0 + 1 + max(0, (inner_w - len(label)) // 2)
        for i, ch in enumerate(label):
            put(label_row, label_col + i, ch)

        if node.call_count > 1 and bh >= 4:
            count_str = f"(x{node.call_count})"
            if len(count_str) > inner_w:
                count_str = count_str[:inner_w]
            count_row = r0 + 2
            count_col = c0 + 1 + max(0, (inner_w - len(count_str)) // 2)
            for i, ch in enumerate(count_str):
                put(count_row, count_col + i, ch)

        # Subagent tool-breakdown badge (agent nodes only). Rendered
        # dim on the row just above the bottom border, when there's
        # vertical room inside the box.
        if (
            node.node_type == "agent"
            and node.tool_breakdown
            and bh >= 5
        ):
            badge = _format_tool_breakdown(node.tool_breakdown, inner_w)
            if badge:
                badge_row = r0 + bh - 2
                badge_col = c0 + 1 + max(0, (inner_w - len(badge)) // 2)
                badge_style = (style + " dim").strip() if style else "dim"
                for i, ch in enumerate(badge):
                    r, c = badge_row, badge_col + i
                    if 0 <= r < h and 0 <= c < w:
                        grid[r][c] = (ch, badge_style)

    # ------------------------------------------------------------------
    def on_click(self, event: Any) -> None:
        if self._updating:
            return
        # Convert click coordinates to a (row, col) within our canvas.
        # FlowchartPanel is a ScrollableContainer, so event.x/y are
        # relative to the VISIBLE viewport. The layout node positions
        # are in absolute canvas coordinates. We must add the scroll
        # offset so a click after scrolling still hits the right node.
        try:
            x = int(getattr(event, "x", 0)) + int(self.scroll_x)
            y = int(getattr(event, "y", 0)) + int(self.scroll_y)
        except Exception:
            return
        for nid, pos in self._layout.nodes.items():
            if pos.row <= y < pos.row + pos.height and pos.col <= x < pos.col + pos.width:
                self._updating = True
                try:
                    # Virtual instance ids (``agent:foo#tid``) are
                    # flattened to their canonical base so cross-
                    # highlight and timeline matching work unchanged.
                    base = self._base_node_id(nid)
                    self.app.selected_agent_id = base  # type: ignore[attr-defined]
                    # Remember which specific instance was clicked so
                    # action_drill_down can open the right subagent file
                    # even when multiple parallel instances share a base
                    # node id. None for non-virtual hits.
                    self._selected_tool_use_id = self._virtual_to_tid.get(nid)
                    # For flow mode: remember exact virtual id so only
                    # the clicked node highlights, not all nodes sharing
                    # the same base id.
                    if "@" in nid:
                        self._selected_flow_vid = nid
                    elif "#" in nid:
                        self._selected_flow_vid = None
                    else:
                        self._selected_flow_vid = None
                except Exception:
                    pass
                finally:
                    self._updating = False
                self._refresh_canvas()
                return

    def _on_app_agent_changed(self, new_value: str | None) -> None:
        if self._updating:
            return
        self._refresh_canvas()
