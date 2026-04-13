#!/usr/bin/env python3
"""Replay a JSONL fixture through parser → graph_model and dump boundary shapes.

Usage:
    python replay.py <fixture_path>

Outputs JSON-formatted boundary shape report to stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agentlens.events import EventType
from agentlens.graph_model import ROOT_ID, CallGraph, MAX_NODES
from agentlens.parser import parse_line


def replay(fixture_path: Path) -> dict:
    lines = fixture_path.read_text(encoding="utf-8").splitlines()

    # Boundary 1: JSONL → HarnessEvent
    events = []
    unknowns = 0
    parse_errors = 0
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            evs = list(parse_line(ln))
            for ev in evs:
                events.append(ev)
                if ev.type == EventType.unknown:
                    unknowns += 1
        except Exception as e:
            parse_errors += 1

    boundary1 = {
        "total_events": len(events),
        "unknowns": unknowns,
        "unknown_ratio": unknowns / max(len(events), 1),
        "parse_errors": parse_errors,
        "event_types": {},
    }
    for ev in events:
        t = ev.type.name
        boundary1["event_types"][t] = boundary1["event_types"].get(t, 0) + 1

    # Boundary 2: HarnessEvent → CallGraph
    graph = CallGraph()
    for ev in events:
        graph.update_from_event(ev)

    nodes_info = {}
    for nid, node in graph.nodes.items():
        nodes_info[nid] = {
            "node_type": node.node_type,
            "status": node.status,
            "label": node.label,
            "call_count": node.call_count,
        }

    boundary2 = {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "max_depth": _max_depth(graph),
        "nodes": nodes_info,
        "within_max_nodes": len(graph.nodes) <= MAX_NODES + 1,
    }

    # Boundary 3: Events → Timeline row shape
    tool_events = [
        ev for ev in events
        if ev.type in (EventType.tool_use, EventType.tool_result)
    ]
    tool_use_with_id = sum(
        1 for ev in tool_events
        if ev.type == EventType.tool_use and "tool_use_id" in ev.payload
    )
    tool_use_with_name = sum(
        1 for ev in tool_events
        if ev.type == EventType.tool_use and "tool_name" in ev.payload
    )

    boundary3 = {
        "tool_events": len(tool_events),
        "tool_use_with_id": tool_use_with_id,
        "tool_use_with_name": tool_use_with_name,
    }

    return {
        "fixture": str(fixture_path),
        "lines": len(lines),
        "boundary1_parser": boundary1,
        "boundary2_graph": boundary2,
        "boundary3_timeline": boundary3,
    }


def _max_depth(graph: CallGraph) -> int:
    """Compute max depth of the call graph from root."""
    if ROOT_ID not in graph.nodes:
        return 0
    children: dict[str, list[str]] = {}
    for edge in graph.edges.values():
        children.setdefault(edge.parent_id, []).append(edge.child_id)

    def _depth(nid: str, seen: set) -> int:
        if nid in seen:
            return 0
        seen.add(nid)
        kids = children.get(nid, [])
        if not kids:
            return 0
        return 1 + max(_depth(k, seen) for k in kids)

    return _depth(ROOT_ID, set())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: replay.py <fixture_path>", file=sys.stderr)
        sys.exit(1)
    p = Path(sys.argv[1])
    if not p.exists():
        print(f"File not found: {p}", file=sys.stderr)
        sys.exit(1)
    result = replay(p)
    print(json.dumps(result, indent=2, ensure_ascii=False))
