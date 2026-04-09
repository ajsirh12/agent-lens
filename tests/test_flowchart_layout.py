"""Unit tests for flowchart_layout.layout_topdown."""

from __future__ import annotations

from datetime import datetime, timezone

from agentlens.events import EventType, HarnessEvent
from agentlens.flowchart_layout import layout_topdown
from agentlens.graph_model import ROOT_ID, CallGraph


def _task(subagent: str, parent: str | None = None, tid: str = "x") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=parent,
        payload={
            "tool_name": "Task",
            "tool_use_id": tid,
            "input": {"subagent_type": subagent},
        },
    )


def test_root_only_layout_has_just_root_box() -> None:
    g = CallGraph()
    layout = layout_topdown(g)
    assert ROOT_ID in layout.nodes
    assert len(layout.nodes) == 1
    assert layout.canvas_width >= 14
    assert layout.canvas_height >= 4
    assert layout.edges == []


def test_linear_chain_stacked_vertically() -> None:
    g = CallGraph()
    g.update_from_event(_task("a", parent=None, tid="t1"))
    # Now make 'a' the parent of 'b'. Parent id is the node id, which
    # is now prefixed with "agent:".
    g.update_from_event(_task("b", parent="agent:a", tid="t2"))
    layout = layout_topdown(g)
    pa = layout.nodes["agent:a"]
    pb = layout.nodes["agent:b"]
    pr = layout.nodes[ROOT_ID]
    # Each layer is on a different row.
    assert pr.row < pa.row < pb.row
    # At least one edge.
    assert len(layout.edges) == 2


def test_branching_two_children_same_row() -> None:
    g = CallGraph()
    g.update_from_event(_task("a", parent=None, tid="t1"))
    g.update_from_event(_task("b", parent=None, tid="t2"))
    layout = layout_topdown(g)
    pa = layout.nodes["agent:a"]
    pb = layout.nodes["agent:b"]
    assert pa.row == pb.row
    assert pa.col != pb.col


def test_layout_is_deterministic() -> None:
    g = CallGraph()
    g.update_from_event(_task("a", parent=None, tid="t1"))
    g.update_from_event(_task("b", parent=None, tid="t2"))
    g.update_from_event(_task("c", parent="agent:a", tid="t3"))
    l1 = layout_topdown(g)
    l2 = layout_topdown(g)
    assert l1.canvas_width == l2.canvas_width
    assert l1.canvas_height == l2.canvas_height
    assert set(l1.nodes.keys()) == set(l2.nodes.keys())
    for nid in l1.nodes:
        a, b = l1.nodes[nid], l2.nodes[nid]
        assert (a.row, a.col, a.width, a.height) == (b.row, b.col, b.width, b.height)


def test_left_align_stability() -> None:
    """Adding a new node at max depth must not shift existing nodes at
    lower depths. Left-align layout guarantees this; a centered layout
    would fail this test.
    """
    g = CallGraph()
    g.update_from_event(_task("a", parent=None, tid="t1"))
    g.update_from_event(_task("b", parent="agent:a", tid="t2"))
    before = layout_topdown(g)
    a_before = (before.nodes["agent:a"].row, before.nodes["agent:a"].col)
    root_before = (before.nodes[ROOT_ID].row, before.nodes[ROOT_ID].col)

    # Add a new sibling at the deepest level — and expand its level.
    g.update_from_event(_task("c", parent="agent:b", tid="t3"))
    g.update_from_event(_task("d", parent="agent:b", tid="t4"))
    g.update_from_event(_task("e", parent="agent:b", tid="t5"))
    after = layout_topdown(g)
    a_after = (after.nodes["agent:a"].row, after.nodes["agent:a"].col)
    root_after = (after.nodes[ROOT_ID].row, after.nodes[ROOT_ID].col)

    assert a_before == a_after
    assert root_before == root_after


# ---- layout_leftright ------------------------------------------------


def test_leftright_root_only() -> None:
    from agentlens.flowchart_layout import layout_leftright
    g = CallGraph()
    lay = layout_leftright(g)
    assert ROOT_ID in lay.nodes
    assert lay.nodes[ROOT_ID].col == 0


def test_leftright_siblings_stack_vertically() -> None:
    from agentlens.flowchart_layout import layout_leftright
    g = CallGraph()
    g.update_from_event(_task("a", tid="t1"))
    g.update_from_event(_task("b", tid="t2"))
    g.update_from_event(_task("c", tid="t3"))
    lay = layout_leftright(g)
    # Root on left (col=0), siblings on right (col > 0)
    assert lay.nodes[ROOT_ID].col == 0
    assert lay.nodes["agent:a"].col > 0
    assert lay.nodes["agent:b"].col == lay.nodes["agent:a"].col
    assert lay.nodes["agent:c"].col == lay.nodes["agent:a"].col
    # Siblings stacked vertically (different rows)
    rows = {lay.nodes[n].row for n in ("agent:a", "agent:b", "agent:c")}
    assert len(rows) == 3


def test_leftright_canvas_width_scales_with_depth_not_siblings() -> None:
    """With 20 siblings at depth 1, left-right canvas width should stay
    bounded by 2 depths, not blow up proportionally to sibling count.
    """
    from agentlens.flowchart_layout import layout_leftright
    g = CallGraph()
    for i in range(20):
        g.update_from_event(_task(f"s{i}", tid=f"t{i}"))
    lay = layout_leftright(g)
    # Only 2 depth levels (root + 1 level of siblings). Width bounded
    # regardless of sibling count.
    assert lay.canvas_width < 50  # 2 cols * 14 + gap
    # Height should reflect all siblings stacked.
    assert lay.canvas_height >= 20 * 4
