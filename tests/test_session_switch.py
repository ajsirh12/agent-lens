"""Mid-session switch — user presses ``s`` to swap the attached JSONL."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentlens.app import AgentlensApp
from agentlens.panels.session_picker import SessionPickerScreen


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


def _make_slug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str]) -> tuple[Path, list[Path]]:
    fake_cwd = Path("/fake/switch/proj")
    slug = str(fake_cwd).replace("/", "-")
    projects_root = tmp_path / "projects"
    slugged = projects_root / slug
    slugged.mkdir(parents=True)
    paths: list[Path] = []
    for i, name in enumerate(names):
        p = slugged / name
        p.write_text("")
        os.utime(p, (100 + i, 100 + i))
        paths.append(p)
    home = tmp_path
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "projects").symlink_to(projects_root)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # Newest-mtime-first order to match SessionLocator.find_candidates.
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return fake_cwd, paths


@pytest.mark.asyncio
async def test_switch_session_key_opens_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cwd, paths = _make_slug(tmp_path, monkeypatch, ["a.jsonl", "b.jsonl"])
    app = AgentlensApp(
        session_override=paths[0],
        project_root=fake_cwd,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        top = app.screen
        assert isinstance(top, SessionPickerScreen)
        assert set(p.name for p in top.candidates) == {"a.jsonl", "b.jsonl"}


@pytest.mark.asyncio
async def test_switch_session_to_different_file_stops_old_starts_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cwd, paths = _make_slug(tmp_path, monkeypatch, ["a.jsonl", "b.jsonl"])
    path_a = paths[0]
    path_b = paths[1]
    # Seed A with one tool_use line so its timeline has content pre-switch.
    path_a.write_text(_tool_use_line(1))
    app = AgentlensApp(
        session_override=path_a,
        project_root=fake_cwd,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Let the initial tail ingest any content.
        for _ in range(5):
            await pilot.pause()
        assert app.active_session_path == path_a
        # Open picker and dismiss with path_b.
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPickerScreen)
        app.screen.dismiss(path_b)
        await pilot.pause()
        assert app.active_session_path == path_b
        assert app.locator_reason == "switched"
        # Timeline was cleared.
        assert app._timeline is not None
        assert app._timeline._row_count == 0
        # Flowchart graph reset to main only.
        assert app._flowchart is not None
        assert len(app._flowchart._graph.nodes) == 1


@pytest.mark.asyncio
async def test_switch_session_to_same_file_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cwd, paths = _make_slug(tmp_path, monkeypatch, ["a.jsonl", "b.jsonl"])
    path_a = paths[0]
    path_a.write_text(_tool_use_line(1))
    app = AgentlensApp(
        session_override=path_a,
        project_root=fake_cwd,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(5):
            await pilot.pause()
        rows_before = app._timeline._row_count if app._timeline else 0
        reason_before = app.locator_reason
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPickerScreen)
        app.screen.dismiss(path_a)  # same as current
        await pilot.pause()
        assert app.active_session_path == path_a
        assert app.locator_reason == reason_before
        assert app._timeline is not None
        assert app._timeline._row_count == rows_before


@pytest.mark.asyncio
async def test_switch_session_cancelled_keeps_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cwd, paths = _make_slug(tmp_path, monkeypatch, ["a.jsonl", "b.jsonl"])
    path_a = paths[0]
    path_a.write_text(_tool_use_line(1))
    app = AgentlensApp(
        session_override=path_a,
        project_root=fake_cwd,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(5):
            await pilot.pause()
        rows_before = app._timeline._row_count if app._timeline else 0
        nodes_before = len(app._flowchart._graph.nodes) if app._flowchart else 0
        reason_before = app.locator_reason
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPickerScreen)
        app.screen.dismiss(None)  # Esc equivalent
        await pilot.pause()
        assert app.active_session_path == path_a
        assert app.locator_reason == reason_before
        assert app._timeline is not None
        assert app._timeline._row_count == rows_before
        assert app._flowchart is not None
        assert len(app._flowchart._graph.nodes) == nodes_before


def test_picker_current_marker_in_format_row(tmp_path: Path) -> None:
    f = tmp_path / "cur.jsonl"
    f.write_text("x")
    os.utime(f, (1700000000, 1700000000))
    screen = SessionPickerScreen([f], current_path=f)
    row = screen._format_row(f)
    assert "cur.jsonl" in row
    assert "(current)" in row
    assert "✓" in row


@pytest.mark.asyncio
async def test_switch_session_no_candidates_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # project_root whose slug dir does not exist.
    fake_cwd = Path("/fake/empty/proj")
    home = tmp_path
    (home / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("")
    app = AgentlensApp(
        session_override=empty_file,
        project_root=fake_cwd,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        screen_before = app.screen
        await pilot.press("s")
        await pilot.pause()
        # No modal pushed — still on the same root screen.
        assert app.screen is screen_before
        assert not isinstance(app.screen, SessionPickerScreen)
