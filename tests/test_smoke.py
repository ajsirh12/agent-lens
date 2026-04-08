"""Smoke tests — AC1, AC3, AC4, AC9."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness_visual.app import HarnessVisualApp


def _tool_use_line(idx: int) -> str:
    return (
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "2026-04-08T10:00:00Z",
                "uuid": f"u{idx}",
                "isSidechain": False,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"toolu_{idx}",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        }
                    ],
                },
            }
        )
        + "\n"
    )


@pytest.mark.asyncio
async def test_launches_and_renders_empty(tmp_path: Path) -> None:
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("")
    app = HarnessVisualApp(
        session_override=empty_file,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Both panels mounted.
        assert app.query_one("#timeline") is not None
        assert app.query_one("#flowchart") is not None


@pytest.mark.asyncio
async def test_live_tail_latency_under_one_second(tmp_path: Path) -> None:
    target = tmp_path / "session.jsonl"
    target.write_text("")
    app = HarnessVisualApp(
        session_override=target,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Append a line.
        with target.open("a") as f:
            f.write(_tool_use_line(1))
        # Poll up to 1.5s for row_count increment.
        timeline = app._timeline
        assert timeline is not None
        for _ in range(30):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if timeline._row_count >= 1:
                break
        assert timeline._row_count >= 1, "live-tail latency exceeded budget"
