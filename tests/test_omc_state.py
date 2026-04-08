"""OMC state reader tests — AC5."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_visual.events import EventType
from harness_visual.omc_state import OmcStateReader


@pytest.mark.asyncio
async def test_subagent_tracking_diff_emits_spawn(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    tracking = state / "subagent-tracking.json"

    tracking.write_text(
        json.dumps(
            {
                "agents": [
                    {"id": "agent-1", "status": "running", "name": "executor"},
                ]
            }
        )
    )

    reader = OmcStateReader(state, interval=0.05)
    events = await reader.tick()
    spawns = [e for e in events if e.type == EventType.agent_spawn]
    assert len(spawns) == 1
    assert spawns[0].agent_id == "agent-1"

    # Add a new agent → second tick emits only the new spawn.
    tracking.write_text(
        json.dumps(
            {
                "agents": [
                    {"id": "agent-1", "status": "done", "name": "executor"},
                    {"id": "agent-2", "status": "running", "name": "explorer"},
                ]
            }
        )
    )
    events2 = await reader.tick()
    spawn2 = [e for e in events2 if e.type == EventType.agent_spawn]
    status2 = [e for e in events2 if e.type == EventType.agent_status]
    assert len(spawn2) == 1
    assert spawn2[0].agent_id == "agent-2"
    assert any(e.agent_id == "agent-1" for e in status2)


@pytest.mark.asyncio
async def test_missing_state_dir_is_no_op(tmp_path: Path) -> None:
    reader = OmcStateReader(tmp_path / "nope")
    assert await reader.tick() == []
