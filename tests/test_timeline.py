"""Tests for TimelinePanel: sanitization, pending_use cap, input_summary, meta-turn-filter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentlens.events import EventType, HarnessEvent
from agentlens.panels.timeline import MAX_PENDING


def _make_event(
    tool_name: str = "Bash",
    tool_use_id: str = "tid1",
    agent_id: str | None = None,
    inp: object = None,
    ts: datetime | None = None,
) -> HarnessEvent:
    payload: dict = {"tool_name": tool_name, "tool_use_id": tool_use_id}
    if inp is not None:
        payload["input"] = inp
    return HarnessEvent(
        type=EventType.tool_use,
        ts=ts or datetime.now(timezone.utc),
        agent_id=agent_id,
        payload=payload,
    )


def _make_result_event(
    tool_use_id: str = "tid1",
    is_error: bool = False,
    agent_id: str | None = None,
    ts: datetime | None = None,
) -> HarnessEvent:
    payload: dict = {"tool_use_id": tool_use_id, "is_error": is_error}
    return HarnessEvent(
        type=EventType.tool_result,
        ts=ts or datetime.now(timezone.utc),
        agent_id=agent_id,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_timeline_sanitizes_ansi_escape_in_cells(tmp_path: Path) -> None:
    """ANSI escape sequences in tool_name must be stripped from DataTable cells."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        ev = _make_event(tool_name="\x1b[31mbad\x1b[0m", tool_use_id="tid-ansi")
        timeline.add_event(ev)
        await pilot.pause()
        # Inspect via public method
        # Move cursor to the row and check cells
        assert timeline._table is not None
        timeline.get_selected_row_cells()
        # cells could be None if cursor not on our row yet; check the row directly
        # via _table rows
        rows = list(timeline._table.rows.keys())
        assert len(rows) >= 1
        row_key = rows[-1]
        row_cells = [timeline._table.get_cell_at((timeline._row_index(row_key), c)) for c in range(5)]
        tool_cell = str(row_cells[1])
        assert "\x1b" not in tool_cell, f"ANSI escape found in cell: {tool_cell!r}"
        assert "bad" in tool_cell  # content preserved without escape codes


@pytest.mark.asyncio
async def test_timeline_pending_use_cap_evicts_oldest(tmp_path: Path) -> None:
    """Feeding 2001 tool_use events should keep _pending_use at <= MAX_PENDING."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        for i in range(MAX_PENDING + 1):
            ev = _make_event(tool_name="Bash", tool_use_id=f"tid-{i}")
            timeline.add_event(ev)
        assert len(timeline._pending_use) <= MAX_PENDING


@pytest.mark.asyncio
async def test_action_show_detail_passes_populated_input_summary(tmp_path: Path) -> None:
    """action_show_detail must pass a non-empty input_summary when input has a command."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    captured: list = []

    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None

        ev = _make_event(
            tool_name="Bash",
            tool_use_id="tid-input",
            inp={"command": "echo hi"},
        )
        timeline.add_event(ev)
        await pilot.pause()

        # Move cursor to the new row (last row)
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        last_idx = len(rows) - 1
        timeline._table.move_cursor(row=last_idx)
        await pilot.pause()

        # Intercept push_screen
        original_push = app.push_screen

        def _capture_push(screen, *args, **kwargs):
            captured.append(screen)
            return original_push(screen, *args, **kwargs)

        app.push_screen = _capture_push  # type: ignore[method-assign]
        app.action_show_detail()
        await pilot.pause()

    assert len(captured) >= 1, "push_screen was not called"
    modal = captured[0]
    assert "echo hi" in modal.input_summary, (
        f"Expected 'echo hi' in input_summary, got: {modal.input_summary!r}"
    )


@pytest.mark.asyncio
async def test_detail_modal_sanitizes_fields(tmp_path: Path) -> None:
    """ToolDetailScreen must strip ANSI and CR from all fields in rendered output."""
    from agentlens.app import AgentlensApp
    from agentlens.panels.detail_modal import ToolDetailScreen

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()

        modal = ToolDetailScreen(
            tool_name="\x1b[31mbad\x1b[0m",
            input_summary="\rfoo",
            status="ok",
            duration_ms="42",
        )

        await app.push_screen(modal)
        await pilot.pause()

        # Inspect Static widgets inside the modal
        for widget in modal.query("Static"):
            rendered = str(widget.content)
            assert "\x1b" not in rendered, f"ANSI escape in rendered text: {rendered!r}"
            assert "\r" not in rendered, f"CR in rendered text: {rendered!r}"


@pytest.mark.asyncio
async def test_tool_use_row_has_start_prefix(tmp_path: Path) -> None:
    """tool_use rows should have a '▶ ' prefix on the tool column."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        ev = _make_event(tool_name="Bash", tool_use_id="tid-prefix")
        timeline.add_event(ev)
        await pilot.pause()
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        assert len(rows) >= 1
        row_key = rows[-1]
        tool_cell = str(timeline._table.get_cell_at((timeline._row_index(row_key), 1)))
        assert tool_cell.startswith("▶ "), f"Expected '▶ ' prefix, got: {tool_cell!r}"


@pytest.mark.asyncio
async def test_tool_result_adds_completion_row(tmp_path: Path) -> None:
    """tool_result should add a second completion row with '✓' prefix."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        use_ev = _make_event(tool_name="Bash", tool_use_id="tid-comp")
        timeline.add_event(use_ev)
        result_ev = _make_result_event(tool_use_id="tid-comp")
        timeline.add_event(result_ev)
        await pilot.pause()
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
        # First row: start row with ▶ prefix, status updated to ok
        start_tool = str(timeline._table.get_cell_at((0, 1)))
        start_status = str(timeline._table.get_cell_at((0, 3)))
        assert start_tool == "▶ Bash", f"Start row tool: {start_tool!r}"
        assert start_status == "ok", f"Start row status: {start_status!r}"
        # Second row: completion row with ✓ prefix
        end_tool = str(timeline._table.get_cell_at((1, 1)))
        end_status = str(timeline._table.get_cell_at((1, 3)))
        assert end_tool == "✓ Bash", f"Completion row tool: {end_tool!r}"
        assert end_status == "ok", f"Completion row status: {end_status!r}"


@pytest.mark.asyncio
async def test_completion_row_has_end_timestamp(tmp_path: Path) -> None:
    """Completion row timestamp should be the result's ts, not the start's."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    t1 = datetime(2025, 1, 15, 14, 2, 1, tzinfo=timezone.utc)
    t2 = datetime(2025, 1, 15, 14, 2, 15, tzinfo=timezone.utc)
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        use_ev = _make_event(tool_name="Bash", tool_use_id="tid-ts", ts=t1)
        timeline.add_event(use_ev)
        result_ev = _make_result_event(tool_use_id="tid-ts", ts=t2)
        timeline.add_event(result_ev)
        await pilot.pause()
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        assert len(rows) == 2
        ts1 = str(timeline._table.get_cell_at((0, 0)))
        ts2 = str(timeline._table.get_cell_at((1, 0)))
        assert ts1 == "14:02:01", f"Start ts: {ts1!r}"
        assert ts2 == "14:02:15", f"End ts: {ts2!r}"


@pytest.mark.asyncio
async def test_orphan_result_has_completion_prefix(tmp_path: Path) -> None:
    """Orphan tool_result (no prior tool_use) should have '✓ ' prefix."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        result_ev = _make_result_event(tool_use_id="tid-orphan")
        timeline.add_event(result_ev)
        await pilot.pause()
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        assert len(rows) == 1
        tool_cell = str(timeline._table.get_cell_at((0, 1)))
        assert tool_cell.startswith("✓ "), f"Expected '✓ ' prefix, got: {tool_cell!r}"


# ---------------------------------------------------------------------------
# meta-turn-filter tests (T-1 through T-5)
# ---------------------------------------------------------------------------


def _make_user_event(
    is_meta: object = None,
    text: str = "hello",
    ts: datetime | None = None,
) -> HarnessEvent:
    """Create a user_message HarnessEvent."""
    payload: dict = {"text": text}
    if is_meta is not None:
        payload["is_meta"] = is_meta
    return HarnessEvent(
        type=EventType.user_message,
        ts=ts or datetime.now(timezone.utc),
        agent_id=None,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_t1_meta_span_tool_rows_blocked_when_hide_meta_true(tmp_path: Path) -> None:
    """T-1: hide_meta=True + meta user -> tool_use -> tool_result => 0 rows added."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        timeline.hide_meta = True

        # meta user_message starts the span
        meta_ev = _make_user_event(is_meta=True)
        timeline.add_event(meta_ev)
        # tool_use inside meta span
        use_ev = _make_event(tool_name="SendMessage", tool_use_id="tid-meta-1")
        timeline.add_event(use_ev)
        # tool_result inside meta span
        result_ev = _make_result_event(tool_use_id="tid-meta-1")
        timeline.add_event(result_ev)

        await pilot.pause()
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        assert len(rows) == 0, (
            f"Expected 0 rows when hide_meta=True, got {len(rows)}"
        )


@pytest.mark.asyncio
async def test_t2_meta_span_tool_rows_visible_when_hide_meta_false(tmp_path: Path) -> None:
    """T-2: hide_meta=False (default) + meta user -> tool_use -> tool_result => 2 rows added."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        # Default hide_meta=False
        assert timeline.hide_meta is False

        # meta user_message starts the span
        meta_ev = _make_user_event(is_meta=True)
        timeline.add_event(meta_ev)
        # tool_use inside meta span
        use_ev = _make_event(tool_name="SendMessage", tool_use_id="tid-meta-2")
        timeline.add_event(use_ev)
        # tool_result inside meta span
        result_ev = _make_result_event(tool_use_id="tid-meta-2")
        timeline.add_event(result_ev)

        await pilot.pause()
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        # tool_use row + completion row = 2 rows (meta user row itself is never shown)
        assert len(rows) == 2, (
            f"Expected 2 rows when hide_meta=False, got {len(rows)}"
        )


@pytest.mark.asyncio
async def test_t3_meta_span_a_blocked_real_turn_and_b_shown(tmp_path: Path) -> None:
    """T-3: hide_meta=True + meta user -> tool_use A -> real user -> tool_use B.
    A is blocked, real turn marker + B are shown.
    """
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        timeline.hide_meta = True

        # meta user starts span
        meta_ev = _make_user_event(is_meta=True)
        timeline.add_event(meta_ev)
        # tool_use A — inside meta span, should be blocked
        use_a = _make_event(tool_name="SendMessage", tool_use_id="tid-a")
        timeline.add_event(use_a)

        # real user message — ends meta span, creates turn marker
        real_ev = _make_user_event(is_meta=None, text="actual user prompt")
        timeline.add_event(real_ev)

        # tool_use B — after span closed, should be shown
        use_b = _make_event(tool_name="Bash", tool_use_id="tid-b")
        timeline.add_event(use_b)

        await pilot.pause()
        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        # turn marker row + tool_use B row = 2 rows
        assert len(rows) == 2, (
            f"Expected 2 rows (turn marker + tool_use B), got {len(rows)}: "
            f"{[str(timeline._table.get_cell_at((i, 1))) for i in range(len(rows))]}"
        )
        # Check that meta span is closed
        assert timeline._in_meta_span is False


@pytest.mark.asyncio
async def test_t4_clear_resets_in_meta_span_and_bare_tool_use_shown(tmp_path: Path) -> None:
    """T-4: clear() resets _in_meta_span=False.
    hide_meta=True without prior meta user => tool_use is shown.
    """
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        timeline.hide_meta = True

        # First: enter a meta span
        meta_ev = _make_user_event(is_meta=True)
        timeline.add_event(meta_ev)
        assert timeline._in_meta_span is True

        # clear() should reset span state
        timeline.clear()
        assert timeline._in_meta_span is False, (
            "_in_meta_span should be False after clear()"
        )
        # hide_meta preserved
        assert timeline.hide_meta is True

        # Now: tool_use without any meta user preceding — should NOT be blocked
        use_ev = _make_event(tool_name="Bash", tool_use_id="tid-bare")
        timeline.add_event(use_ev)
        await pilot.pause()

        assert timeline._table is not None
        rows = list(timeline._table.rows.keys())
        assert len(rows) == 1, (
            f"Expected 1 row (bare tool_use not in meta span), got {len(rows)}"
        )


@pytest.mark.asyncio
async def test_t5_invalid_is_meta_payloads_no_exception(tmp_path: Path) -> None:
    """T-5: AC-10: None, 'yes', 1, 0 as is_meta values must not raise exceptions."""
    from agentlens.app import AgentlensApp

    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        timeline = app._timeline
        assert timeline is not None
        timeline.hide_meta = True

        for is_meta_value in (None, "yes", 1, 0):
            # Must not raise — AC-10 never-raise principle
            ev = _make_user_event(is_meta=is_meta_value, text="test payload")
            try:
                timeline.add_event(ev)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"add_event raised {type(exc).__name__} with is_meta={is_meta_value!r}: {exc}"
                )

        await pilot.pause()
        # Verify consistent state — no assertion on row count, just no crash
        assert timeline._table is not None
