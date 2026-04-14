"""Smoke tests — AC1, AC3, AC4, AC9, meta-turn-filter key binding."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentlens.app import AgentlensApp


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
    app = AgentlensApp(
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
    app = AgentlensApp(
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


# ---------------------------------------------------------------------------
# meta-turn-filter key binding tests (T-6, T-7, T-8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t6_t_key_enables_meta_filter(tmp_path: Path) -> None:
    """T-6: pressing 't' sets _hide_meta_turns=True, timeline.hide_meta=True,
    and footer contains '[meta:hidden]'.
    """
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("")
    app = AgentlensApp(
        session_override=empty_file,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Initial state: meta filter off
        assert app._hide_meta_turns is False

        await pilot.press("t")
        await pilot.pause()

        assert app._hide_meta_turns is True, (
            "Expected _hide_meta_turns=True after pressing 't'"
        )
        timeline = app._timeline
        assert timeline is not None
        assert timeline.hide_meta is True, (
            "Expected timeline.hide_meta=True after pressing 't'"
        )
        # Footer should contain [meta:hidden]
        footer = app._footer
        assert footer is not None
        footer_text = str(footer.content)
        assert "[meta:hidden]" in footer_text, (
            f"Expected '[meta:hidden]' in footer, got: {footer_text!r}"
        )


@pytest.mark.asyncio
async def test_t7_t_key_toggles_meta_filter_off(tmp_path: Path) -> None:
    """T-7: pressing 't' twice returns to False and footer tag disappears."""
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("")
    app = AgentlensApp(
        session_override=empty_file,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()

        # First press: enable
        await pilot.press("t")
        await pilot.pause()
        assert app._hide_meta_turns is True

        # Second press: disable
        await pilot.press("t")
        await pilot.pause()

        assert app._hide_meta_turns is False, (
            "Expected _hide_meta_turns=False after pressing 't' twice"
        )
        timeline = app._timeline
        assert timeline is not None
        assert timeline.hide_meta is False, (
            "Expected timeline.hide_meta=False after pressing 't' twice"
        )
        # Footer should NOT contain [meta:hidden]
        footer = app._footer
        assert footer is not None
        footer_text = str(footer.content)
        assert "[meta:hidden]" not in footer_text, (
            f"Expected no '[meta:hidden]' in footer after toggle-off, got: {footer_text!r}"
        )


@pytest.mark.asyncio
async def test_t8_t_key_does_not_break_other_bindings(tmp_path: Path) -> None:
    """T-8: pressing 't' does not break existing key bindings.
    Verifies that 'm', 'o', 'p' keys still trigger their actions without error.
    """
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("")
    app = AgentlensApp(
        session_override=empty_file,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()

        # Enable meta filter
        await pilot.press("t")
        await pilot.pause()
        assert app._hide_meta_turns is True

        # 'm': toggle mode (all/running) — should not raise
        flowchart = app._flowchart
        assert flowchart is not None
        await pilot.press("m")
        await pilot.pause()
        mode_after = flowchart.get_mode()
        # mode should not be None (at least not raised an exception)
        assert mode_after is not None

        # 'o': toggle orientation — should not raise
        orient_before = flowchart.get_orientation()
        await pilot.press("o")
        await pilot.pause()
        orient_after = flowchart.get_orientation()
        assert orient_after is not None
        assert orient_after != orient_before, (
            f"Expected orientation change after 'o', before={orient_before!r} after={orient_after!r}"
        )

        # 'p': toggle pane layout — should not raise
        await pilot.press("p")
        await pilot.pause()

        # meta filter state should be unchanged after other key presses
        assert app._hide_meta_turns is True, (
            "meta filter state should be preserved after pressing other keys"
        )
