"""Replay real Claude Code slice and assert <5% unknown ratio. AC10."""

from __future__ import annotations

from pathlib import Path

from agentlens.events import EventType
from agentlens.parser import parse_line

FIXTURE = Path(__file__).parent / "fixtures" / "real_session_slice.jsonl"


def test_replay_real_slice_parses_without_unknowns() -> None:
    assert FIXTURE.exists(), f"missing fixture: {FIXTURE}"
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 50, f"fixture too small: {len(lines)} lines"

    total = 0
    unknowns = 0
    for ln in lines:
        for ev in parse_line(ln):
            total += 1
            if ev.type == EventType.unknown:
                unknowns += 1

    assert total > 0
    ratio = unknowns / total
    assert ratio < 0.05, f"unknown ratio {ratio:.3f} >= 5% ({unknowns}/{total})"
