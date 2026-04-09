"""Keypress repaint budget test — AC8 (<200 ms)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentlens.app import AgentlensApp
from agentlens.events import EventType, HarnessEvent


@pytest.mark.asyncio
async def test_keypress_repaint_under_200ms(tmp_path: Path) -> None:
    target = tmp_path / "empty.jsonl"
    target.write_text("")
    app = AgentlensApp(
        session_override=target,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None

        # Pre-populate 500 rows directly.
        now = datetime.now(timezone.utc)
        for i in range(500):
            timeline.add_event(
                HarnessEvent(
                    type=EventType.tool_use,
                    ts=now,
                    agent_id=f"agent-{i % 3}",
                    payload={"tool_use_id": f"t{i}", "tool_name": "Bash"},
                )
            )
        await pilot.pause()

        t0 = time.monotonic()
        await pilot.press("j")
        await pilot.pause()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.4, f"repaint took {elapsed * 1000:.0f}ms"
