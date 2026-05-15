"""Tests for turn replay feature (design_spec.md FR-1..15, AC1..18).

Strategy: drive ``FlowchartPanel._on_replay_tick()`` directly for
deterministic step-through (no sleeps / no real timer). One pilot test
covers the app-level keybinding for regression.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentlens.app import AgentlensApp
from agentlens.events import EventType, HarnessEvent
from agentlens.graph_model import ROOT_ID, CallGraph, FlowRecord
from agentlens.panels.flowchart import FlowchartPanel


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _user_message(text: str = "hello") -> HarnessEvent:
    return HarnessEvent(
        type=EventType.user_message,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"text": text},
    )


def _agent_use(
    subagent: str,
    *,
    tid: str,
    description: str = "",
) -> HarnessEvent:
    inp: dict[str, Any] = {"subagent_type": subagent}
    if description:
        inp["description"] = description
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={
            "tool_name": "Agent",
            "tool_use_id": tid,
            "input": inp,
        },
    )


def _result(tid: str, *, error: bool = False) -> HarnessEvent:
    return HarnessEvent(
        type=EventType.tool_result,
        ts=datetime.now(timezone.utc),
        agent_id=None,
        payload={"tool_use_id": tid, "is_error": error},
    )


def _add_flow_record(
    graph: CallGraph,
    *,
    node_id: str,
    tid: str,
    turn_index: int,
    label: str | None = None,
    parent_node_id: str = ROOT_ID,
    status: str = "done",
    started_ts: float | None = None,
) -> FlowRecord:
    """Append a synthetic FlowRecord directly to ``_flow_history``.

    We bypass ``update_from_event`` so tests can construct exact
    sequences regardless of the full event-driven semantics.
    """
    rec = FlowRecord(
        node_id=node_id,
        tool_use_id=tid,
        label=label or node_id.split(":", 1)[-1],
        description="",
        node_type="agent",
        started_ts=(
            started_ts
            if started_ts is not None
            else float(len(graph._flow_history))
        ),
        ended_ts=None,
        status=status,  # type: ignore[arg-type]
        turn_index=turn_index,
        parent_node_id=parent_node_id,
    )
    graph._flow_history.append(rec)
    return rec


def _seed_panel_turn(
    panel: FlowchartPanel,
    *,
    turn_index: int,
    count: int,
    node_prefix: str = "agent:worker",
    status: str = "done",
) -> list[FlowRecord]:
    """Append ``count`` FlowRecords to a panel's internal CallGraph."""
    recs: list[FlowRecord] = []
    for i in range(count):
        recs.append(
            _add_flow_record(
                panel._graph,
                node_id=f"{node_prefix}{i}",
                tid=f"t{turn_index}-{i}",
                turn_index=turn_index,
                status=status,
            )
        )
    return recs


# ---------------------------------------------------------------------
# AC1 — basic replay builds correct subgraph frame by frame
# ---------------------------------------------------------------------


def test_ac1_replay_step_builds_subgraph() -> None:
    panel = FlowchartPanel()
    # 4 records in turn 0
    _seed_panel_turn(panel, turn_index=0, count=4)

    started, reason = panel.start_replay(0)
    assert started is True
    assert reason == ""
    # frame 0: prelude only — no cross-turn parents in this case, so
    # subgraph has ROOT only.
    sub = panel._flow_subgraph(history_end_index=0)
    # ROOT is always present (CallGraph __post_init__). Flow node count
    # is the total minus ROOT.
    assert ROOT_ID in sub.nodes
    flow_only = len(sub.nodes) - (1 if ROOT_ID in sub.nodes else 0)
    assert flow_only == 0

    # Advance frame by frame: each tick adds exactly one flow node.
    flow_counts = []
    for _ in range(4):
        panel._on_replay_tick()
        end_index = panel._replay_indices[panel._replay_frame - 1] + 1
        sub = panel._flow_subgraph(history_end_index=end_index)
        flow_counts.append(
            len(sub.nodes) - (1 if ROOT_ID in sub.nodes else 0)
        )

    assert flow_counts == [1, 2, 3, 4]
    # After the last tick, replay enters 'done' state.
    assert panel._replay_state == "done"
    assert panel._replay_frame == 4
    assert panel._replay_total == 4


# ---------------------------------------------------------------------
# AC2 — LIVE turn (no _active_turn) → no-op
# ---------------------------------------------------------------------


def test_ac2_live_turn_noop() -> None:
    panel = FlowchartPanel()
    # No records, no turns: passing None / -1 should fail with "live".
    started, reason = panel.start_replay(-1)
    assert started is False
    assert reason == "live"
    assert panel._replay_state is None
    assert panel._replay_timer is None


# ---------------------------------------------------------------------
# AC3 — empty turn (0 FlowRecords) → (False, "empty")
# ---------------------------------------------------------------------


def test_ac3_empty_turn_noop() -> None:
    panel = FlowchartPanel()
    # Seed turn 1 only so turn 0 has 0 records, but a finished turn exists
    # so that turn 0 isn't classified as 'live'.
    _seed_panel_turn(panel, turn_index=1, count=2)
    # Manually inject turns so the live-guard isn't tripped.
    panel._graph._turns = [object(), object()]  # type: ignore[attr-defined]

    started, reason = panel.start_replay(0)
    assert started is False
    assert reason == "empty"
    assert panel._replay_state is None
    assert panel._replay_timer is None


# ---------------------------------------------------------------------
# AC4 — single event turn → (False, "single")
# ---------------------------------------------------------------------


def test_ac4_single_event_noop() -> None:
    panel = FlowchartPanel()
    _seed_panel_turn(panel, turn_index=0, count=1)
    panel._graph._turns = [object(), object()]  # type: ignore[attr-defined]

    started, reason = panel.start_replay(0)
    assert started is False
    assert reason == "single"
    assert panel._replay_state is None
    assert panel._replay_timer is None


# ---------------------------------------------------------------------
# AC5 — pause / resume continues from the same frame
# ---------------------------------------------------------------------


def test_ac5_pause_resume() -> None:
    panel = FlowchartPanel()
    _seed_panel_turn(panel, turn_index=0, count=10)

    started, _ = panel.start_replay(0)
    assert started is True
    # Advance 4 ticks.
    for _ in range(4):
        panel._on_replay_tick()
    assert panel._replay_frame == 4
    assert panel._replay_state == "running"

    panel.pause_replay()
    assert panel._replay_state == "paused"
    # Pause must clear/stop the timer.
    assert panel._replay_timer is None

    panel.resume_replay()
    assert panel._replay_state == "running"
    # Advance one more — should continue from 4, not restart at 0.
    panel._on_replay_tick()
    assert panel._replay_frame == 5


# ---------------------------------------------------------------------
# AC6 — cancel restores full subgraph and clears timer + state
# ---------------------------------------------------------------------


def test_ac6_cancel_replay() -> None:
    panel = FlowchartPanel()
    _seed_panel_turn(panel, turn_index=0, count=10)

    started, _ = panel.start_replay(0)
    assert started is True
    panel._on_replay_tick()
    panel._on_replay_tick()
    assert panel._replay_frame == 2

    panel.cancel_replay()
    assert panel._replay_state is None
    assert panel._replay_turn is None
    assert panel._replay_frame == 0
    assert panel._replay_total == 0
    assert panel._replay_indices == []
    assert panel._replay_timer is None


# ---------------------------------------------------------------------
# AC7 — turn navigation cancels replay (Timeline cursor → cancel_replay)
# ---------------------------------------------------------------------


async def test_ac7_cancel_on_turn_nav(tmp_path: Path) -> None:
    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        flow = app._flowchart
        assert flow is not None
        _seed_panel_turn(flow, turn_index=0, count=5)
        started, _ = flow.start_replay(0)
        assert started is True
        flow._on_replay_tick()
        assert flow.is_replaying() is True

        # Simulate the app's cancel guard (called by turn-nav actions).
        cancelled = app._cancel_replay_if_active()
        assert cancelled is True
        assert flow.is_replaying() is False


# ---------------------------------------------------------------------
# AC10 — never-raise on bad parent_node_id
# ---------------------------------------------------------------------


def test_ac10_never_raise_bad_parent() -> None:
    panel = FlowchartPanel()
    # Two records, the second points at a parent that doesn't exist.
    _add_flow_record(
        panel._graph,
        node_id="agent:a",
        tid="t0",
        turn_index=0,
    )
    _add_flow_record(
        panel._graph,
        node_id="agent:b",
        tid="t1",
        turn_index=0,
        parent_node_id="agent:does-not-exist",
    )

    started, reason = panel.start_replay(0)
    assert started is True
    assert reason == ""
    # Ticking through must not raise.
    panel._on_replay_tick()
    panel._on_replay_tick()
    # Bad-parent fallback: child re-parents to ROOT (NFR-1, AC10).
    end = panel._replay_indices[-1] + 1
    sub = panel._flow_subgraph(history_end_index=end)
    # The second flow node should exist; its parent edge must terminate
    # somewhere (ROOT or a known vid) without raising.
    assert any(nid.startswith("agent:b@") for nid in sub.nodes)


# ---------------------------------------------------------------------
# AC11 — timer cleanup: cancel_replay() stops the timer
# ---------------------------------------------------------------------


class _DummyTimer:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


def test_ac11_timer_cleanup() -> None:
    panel = FlowchartPanel()
    _seed_panel_turn(panel, turn_index=0, count=4)

    # Inject a fake timer so we can verify .stop() is called even outside
    # of a mounted Textual context (where _arm_replay_timer returns None).
    started, _ = panel.start_replay(0)
    assert started is True
    fake = _DummyTimer()
    panel._replay_timer = fake

    panel.cancel_replay()
    assert fake.stopped == 1
    assert panel._replay_timer is None
    assert panel._replay_state is None


# ---------------------------------------------------------------------
# AC12 — live isolation: new events to OTHER turn don't affect replay
# ---------------------------------------------------------------------


def test_ac12_live_isolation() -> None:
    panel = FlowchartPanel()
    _seed_panel_turn(panel, turn_index=0, count=6)
    panel._graph._turns = [object(), object()]  # type: ignore[attr-defined]

    started, _ = panel.start_replay(0)
    assert started is True
    panel._on_replay_tick()
    panel._on_replay_tick()
    assert panel._replay_state == "running"
    assert panel._replay_frame == 2

    # Append events that belong to a DIFFERENT turn — replay must keep
    # running, indices/total unchanged.
    indices_before = list(panel._replay_indices)
    total_before = panel._replay_total
    _add_flow_record(
        panel._graph,
        node_id="agent:other",
        tid="other-1",
        turn_index=1,
    )
    _add_flow_record(
        panel._graph,
        node_id="agent:other2",
        tid="other-2",
        turn_index=1,
    )
    assert panel._replay_state == "running"
    assert panel._replay_indices == indices_before
    assert panel._replay_total == total_before


# ---------------------------------------------------------------------
# AC13 — live turn auto-cancel: new event lands on REPLAYING turn
# ---------------------------------------------------------------------


def test_ac13_live_turn_auto_cancel() -> None:
    panel = FlowchartPanel()
    # Use real events so update_from_event will emit a FlowRecord with
    # the correct turn_index. First a user_message to open turn 0, then
    # 3 agent spawns so start_replay can succeed (K >= 2).
    panel.add_event(_user_message("turn 0 start"))
    panel.add_event(_agent_use("a", tid="t1"))
    panel.add_event(_agent_use("b", tid="t2"))
    panel.add_event(_agent_use("c", tid="t3"))

    # Determine the live turn_index from the most recent FlowRecord.
    assert panel._graph._flow_history, "no flow history seeded"
    live_turn = panel._graph._flow_history[-1].turn_index

    started, reason = panel.start_replay(live_turn)
    assert started is True, f"expected replay to start; reason={reason}"
    panel._on_replay_tick()
    assert panel.is_replaying() is True

    # A new event arriving on the SAME turn must auto-cancel replay
    # (design_spec.md D-8 / AC13).
    panel.add_event(_agent_use("d", tid="t4"))
    assert panel.is_replaying() is False
    assert panel._replay_state is None


# ---------------------------------------------------------------------
# AC14 — completion state after last tick
# ---------------------------------------------------------------------


def test_ac14_completion_state() -> None:
    panel = FlowchartPanel()
    _seed_panel_turn(panel, turn_index=0, count=3)

    started, _ = panel.start_replay(0)
    assert started is True
    panel._on_replay_tick()
    panel._on_replay_tick()
    panel._on_replay_tick()
    assert panel._replay_state == "done"
    assert panel._replay_frame == panel._replay_total == 3
    # Done border-suffix should include the "replay done" text.
    suffix = panel._replay_border_suffix()
    assert "replay done" in suffix


# ---------------------------------------------------------------------
# AC15 — restart after done: r in 'done' state restarts from frame 0
# ---------------------------------------------------------------------


def test_ac15_restart_after_done() -> None:
    panel = FlowchartPanel()
    _seed_panel_turn(panel, turn_index=0, count=3)

    started, _ = panel.start_replay(0)
    assert started is True
    for _ in range(3):
        panel._on_replay_tick()
    assert panel._replay_state == "done"

    # Restart path = cancel + start (matches action_replay_turn 'done' branch).
    panel.cancel_replay()
    started2, _ = panel.start_replay(0)
    assert started2 is True
    assert panel._replay_state == "running"
    assert panel._replay_frame == 0
    assert panel._replay_total == 3


# ---------------------------------------------------------------------
# AC17 — regression guard: import surface intact, existing tests run.
# ---------------------------------------------------------------------


def test_ac17_regression_guard() -> None:
    # If any of these imports broke, the whole test session would fail
    # at collection time. The explicit references here also catch
    # accidental removal of public symbols.
    from agentlens.panels.flowchart import (  # noqa: F401
        FlowchartPanel,
        REPLAY_FRAME_INTERVAL_S,
        MAX_REPLAY_STEPS,
    )
    panel = FlowchartPanel()
    assert hasattr(panel, "start_replay")
    assert hasattr(panel, "cancel_replay")
    assert hasattr(panel, "pause_replay")
    assert hasattr(panel, "resume_replay")
    assert hasattr(panel, "is_replaying")
    assert hasattr(panel, "get_replay_progress")


# ---------------------------------------------------------------------
# AC18 — arrow keys don't cancel replay (only nav/toggle/modal do)
# ---------------------------------------------------------------------


async def test_ac18_arrow_keys_no_cancel(tmp_path: Path) -> None:
    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        flow = app._flowchart
        assert flow is not None
        _seed_panel_turn(flow, turn_index=0, count=5)
        started, _ = flow.start_replay(0)
        assert started is True
        flow._on_replay_tick()
        assert flow.is_replaying() is True

        # Arrow keys must NOT cancel replay. action_cursor_* don't call
        # _cancel_replay_if_active(). Press a few and re-check.
        for key in ("down", "up", "left", "right", "j", "k", "h", "l"):
            await pilot.press(key)
            await pilot.pause()
        assert flow.is_replaying() is True
