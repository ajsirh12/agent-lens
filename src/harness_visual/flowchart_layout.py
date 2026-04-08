"""Pure Sugiyama-style layout for CallGraph.

Supports two orientations:
- top-down (``layout_topdown``): root at top, depths flow downward.
- left-right (``layout_leftright``): root at left, depths flow rightward,
  siblings stacked vertically. Better for fan-out patterns where many
  agents branch off the root — stacks them vertically instead of running
  off the right edge of the terminal.

No Textual imports. Produces grid coordinates that the FlowchartPanel
can render into a 2D character grid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph_model import ROOT_ID, CallGraph, Node


@dataclass
class NodePos:
    node: Node
    row: int
    col: int
    width: int
    height: int


@dataclass
class EdgePath:
    from_id: str
    to_id: str
    points: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class LayoutResult:
    nodes: dict[str, NodePos]
    edges: list[EdgePath]
    canvas_width: int
    canvas_height: int


def layout_topdown(
    graph: CallGraph,
    node_width: int = 14,
    node_height: int = 5,
    h_gap: int = 2,
    v_gap: int = 2,
) -> LayoutResult:
    """Lay out the call graph top-down.

    Determinism: nodes are placed in their original `graph.nodes`
    insertion order, so re-running on the same graph produces an
    identical LayoutResult.
    """
    # Compute depth of every node from root via BFS preserving insertion
    # order — same approach as CallGraph._compute_depths.
    depths = _bfs_depths(graph)
    max_depth = max(depths.values()) if depths else 0

    # Group node ids by depth, preserving graph.nodes insertion order.
    by_depth: dict[int, list[str]] = {}
    for nid in graph.nodes.keys():
        d = depths.get(nid, 0)
        by_depth.setdefault(d, []).append(nid)

    # Compute canvas width: the widest level dictates the width.
    widest_count = max((len(v) for v in by_depth.values()), default=1)
    canvas_width = max(
        node_width,
        widest_count * node_width + (widest_count - 1) * h_gap,
    )
    # Total height: per-depth = node_height + v_gap, minus trailing gap.
    total_levels = max_depth + 1
    canvas_height = total_levels * node_height + (total_levels - 1) * v_gap

    # Place nodes — center each level horizontally on the canvas.
    nodes_pos: dict[str, NodePos] = {}
    for depth in range(total_levels):
        ids_here = by_depth.get(depth, [])
        if not ids_here:
            continue
        # Left-align each level. Centering looks prettier but causes all
        # lower depths to visually jump whenever the widest level grows
        # during a live update. Stability wins.
        start_col = 0
        row = depth * (node_height + v_gap)
        for i, nid in enumerate(ids_here):
            col = start_col + i * (node_width + h_gap)
            nodes_pos[nid] = NodePos(
                node=graph.nodes[nid],
                row=row,
                col=col,
                width=node_width,
                height=node_height,
            )

    # Build edge paths — straight vertical with one horizontal jog at midpoint.
    edges_out: list[EdgePath] = []
    for (parent_id, child_id) in graph.edges.keys():
        p = nodes_pos.get(parent_id)
        c = nodes_pos.get(child_id)
        if p is None or c is None:
            continue
        path = _route_edge(p, c)
        edges_out.append(EdgePath(from_id=parent_id, to_id=child_id, points=path))

    return LayoutResult(
        nodes=nodes_pos,
        edges=edges_out,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )


def _bfs_depths(graph: CallGraph) -> dict[str, int]:
    depths: dict[str, int] = {ROOT_ID: 0}
    adj: dict[str, list[str]] = {}
    for (p, c) in graph.edges.keys():
        adj.setdefault(p, []).append(c)
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


def _route_edge(parent: NodePos, child: NodePos) -> list[tuple[int, int]]:
    """Return a list of (row, col) cells for a parent→child edge."""
    p_bottom_row = parent.row + parent.height - 1
    p_center_col = parent.col + parent.width // 2
    c_top_row = child.row
    c_center_col = child.col + child.width // 2

    # Start one row below parent's bottom, end one row above child's top.
    start_row = p_bottom_row + 1
    end_row = c_top_row - 1
    if end_row < start_row:
        return []

    points: list[tuple[int, int]] = []
    midpoint_row = (start_row + end_row) // 2

    # Vertical down from parent center column to midpoint row.
    for r in range(start_row, midpoint_row + 1):
        points.append((r, p_center_col))
    # Horizontal jog at midpoint row.
    if p_center_col != c_center_col:
        lo = min(p_center_col, c_center_col)
        hi = max(p_center_col, c_center_col)
        for col in range(lo, hi + 1):
            points.append((midpoint_row, col))
    # Vertical down from midpoint row to child top.
    for r in range(midpoint_row, end_row + 1):
        points.append((r, c_center_col))

    return points


def layout_leftright(
    graph: CallGraph,
    node_width: int = 14,
    node_height: int = 5,
    h_gap: int = 3,
    v_gap: int = 1,
) -> LayoutResult:
    """Lay out the call graph left-to-right.

    Root is placed on the left; each depth level is a column extending
    rightward; siblings at the same depth are stacked vertically in
    insertion order. This is the preferred orientation for graphs that
    fan out heavily from root (many direct children of ``main``) since
    it stacks them vertically rather than running off the right edge.
    """
    depths = _bfs_depths(graph)
    max_depth = max(depths.values()) if depths else 0

    by_depth: dict[int, list[str]] = {}
    for nid in graph.nodes.keys():
        d = depths.get(nid, 0)
        by_depth.setdefault(d, []).append(nid)

    # Canvas width is number-of-columns * (node_width + h_gap).
    total_levels = max_depth + 1
    canvas_width = total_levels * node_width + (total_levels - 1) * h_gap
    # Canvas height is the tallest level (most siblings at a single depth).
    tallest_count = max((len(v) for v in by_depth.values()), default=1)
    canvas_height = max(
        node_height,
        tallest_count * node_height + (tallest_count - 1) * v_gap,
    )

    nodes_pos: dict[str, NodePos] = {}
    for depth in range(total_levels):
        ids_here = by_depth.get(depth, [])
        if not ids_here:
            continue
        col = depth * (node_width + h_gap)
        for i, nid in enumerate(ids_here):
            row = i * (node_height + v_gap)
            nodes_pos[nid] = NodePos(
                node=graph.nodes[nid],
                row=row,
                col=col,
                width=node_width,
                height=node_height,
            )

    edges_out: list[EdgePath] = []
    for (parent_id, child_id) in graph.edges.keys():
        p = nodes_pos.get(parent_id)
        c = nodes_pos.get(child_id)
        if p is None or c is None:
            continue
        path = _route_edge_horizontal(p, c)
        edges_out.append(EdgePath(from_id=parent_id, to_id=child_id, points=path))

    return LayoutResult(
        nodes=nodes_pos,
        edges=edges_out,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )


def _route_edge_horizontal(parent: NodePos, child: NodePos) -> list[tuple[int, int]]:
    """Route a parent→child edge for a left-right layout.

    Path: out of parent's right-center, horizontal until a mid column,
    vertical jog to child's row, horizontal until child's left-center.
    """
    p_right_col = parent.col + parent.width - 1
    p_center_row = parent.row + parent.height // 2
    c_left_col = child.col
    c_center_row = child.row + child.height // 2

    # Start one col right of parent's right border, end one col left of child.
    start_col = p_right_col + 1
    end_col = c_left_col - 1
    if end_col < start_col:
        return []

    points: list[tuple[int, int]] = []
    midpoint_col = (start_col + end_col) // 2

    # Horizontal right from parent center row to midpoint col.
    for c in range(start_col, midpoint_col + 1):
        points.append((p_center_row, c))
    # Vertical jog at midpoint col.
    if p_center_row != c_center_row:
        lo = min(p_center_row, c_center_row)
        hi = max(p_center_row, c_center_row)
        for r in range(lo, hi + 1):
            points.append((r, midpoint_col))
    # Horizontal right from midpoint col to child left.
    for c in range(midpoint_col, end_col + 1):
        points.append((c_center_row, c))

    return points
