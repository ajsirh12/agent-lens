#!/usr/bin/env python3
"""JSONL schema probe — event type/field frequency analysis.

Usage:
    python probe.py <jsonl_path> [--sample N]

Outputs a markdown frequency report to stdout.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _collect_keys(obj: dict, prefix: str = "", depth: int = 0, max_depth: int = 2) -> list[str]:
    """Recursively collect dotted key paths up to max_depth."""
    keys: list[str] = []
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        keys.append(full)
        if isinstance(v, dict) and depth < max_depth:
            keys.extend(_collect_keys(v, full, depth + 1, max_depth))
    return keys


def probe(path: Path, max_lines: int = 100_000) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()

    # Sampling for large files
    if len(lines) > max_lines:
        head = lines[:10_000]
        tail = lines[-10_000:]
        mid = random.sample(lines[10_000:-10_000], min(10_000, len(lines) - 20_000))
        lines = head + mid + tail

    type_counter: Counter[str] = Counter()
    content_type_counter: Counter[str] = Counter()
    tool_name_counter: Counter[str] = Counter()
    field_counter: Counter[str] = Counter()
    parse_errors = 0

    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            parse_errors += 1
            continue

        if not isinstance(row, dict):
            continue

        row_type = row.get("type", "<missing>")
        type_counter[row_type] += 1

        for key in _collect_keys(row):
            field_counter[key] += 1

        msg = row.get("message", {})
        if isinstance(msg, dict):
            for block in msg.get("content", []):
                if isinstance(block, dict):
                    ct = block.get("type", "<missing>")
                    content_type_counter[ct] += 1
                    if ct == "tool_use":
                        tool_name_counter[block.get("name", "<missing>")] += 1

    # Build report
    out: list[str] = []
    out.append(f"# JSONL Schema Probe Report\n")
    out.append(f"**File:** `{path}`")
    out.append(f"**Lines analyzed:** {len(lines)} (parse errors: {parse_errors})\n")

    out.append("## Event types (`type`)\n")
    out.append("| type | count |")
    out.append("|------|-------|")
    for t, c in type_counter.most_common():
        out.append(f"| `{t}` | {c} |")

    out.append("\n## Content block types (`message.content[].type`)\n")
    out.append("| type | count |")
    out.append("|------|-------|")
    for t, c in content_type_counter.most_common():
        out.append(f"| `{t}` | {c} |")

    out.append("\n## Tool names (`tool_use.name`)\n")
    out.append("| name | count |")
    out.append("|------|-------|")
    for t, c in tool_name_counter.most_common():
        out.append(f"| `{t}` | {c} |")

    out.append("\n## All fields (depth ≤ 2)\n")
    out.append("| field | count |")
    out.append("|-------|-------|")
    for f, c in field_counter.most_common(100):
        out.append(f"| `{f}` | {c} |")

    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: probe.py <jsonl_path>", file=sys.stderr)
        sys.exit(1)
    p = Path(sys.argv[1])
    if not p.exists():
        print(f"File not found: {p}", file=sys.stderr)
        sys.exit(1)
    print(probe(p))
