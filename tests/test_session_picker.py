"""SessionPickerScreen tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentlens.app import AgentlensApp
from agentlens.panels.session_picker import SessionPickerScreen


def test_format_row_includes_mtime_size_and_name(tmp_path: Path) -> None:
    f = tmp_path / "abc.jsonl"
    f.write_text("hello\n")
    os.utime(f, (1700000000, 1700000000))
    row = SessionPickerScreen._format_row(f)
    assert "abc.jsonl" in row
    assert "KB" in row
    assert "2023" in row  # 1700000000 = 2023-11-...


def test_format_row_handles_missing_file(tmp_path: Path) -> None:
    f = tmp_path / "ghost.jsonl"
    row = SessionPickerScreen._format_row(f)
    assert "ghost.jsonl" in row


@pytest.mark.asyncio
async def test_app_shows_picker_when_two_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cwd = Path("/fake/multi/proj")
    slug = str(fake_cwd).replace("/", "-")
    projects_root = tmp_path / "projects"
    slugged = projects_root / slug
    slugged.mkdir(parents=True)
    (slugged / "older.jsonl").write_text("{}\n")
    (slugged / "newer.jsonl").write_text("{}\n")
    os.utime(slugged / "older.jsonl", (1, 1))
    os.utime(slugged / "newer.jsonl", (2, 2))

    # Patch Path.home() so the app's hardcoded ~/.claude/projects resolves
    # under tmp_path.
    home = tmp_path
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "projects").symlink_to(projects_root)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    app = AgentlensApp(project_root=fake_cwd, no_attach=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Picker should be on the screen stack.
        top = app.screen
        assert isinstance(top, SessionPickerScreen)
        assert len(top.candidates) == 2
        # Pick the first (newest).
        await pilot.press("enter")
        await pilot.pause()
        assert app.active_session_path is not None
        assert app.active_session_path.name == "newer.jsonl"
        assert app.locator_reason == "picker"


@pytest.mark.asyncio
async def test_auto_latest_skips_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cwd = Path("/fake/multi/proj")
    slug = str(fake_cwd).replace("/", "-")
    projects_root = tmp_path / "projects"
    slugged = projects_root / slug
    slugged.mkdir(parents=True)
    (slugged / "older.jsonl").write_text("{}\n")
    (slugged / "newer.jsonl").write_text("{}\n")
    os.utime(slugged / "older.jsonl", (1, 1))
    os.utime(slugged / "newer.jsonl", (2, 2))

    home = tmp_path
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "projects").symlink_to(projects_root)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    app = AgentlensApp(
        project_root=fake_cwd, no_attach=False, auto_latest=True
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Picker should NOT be on screen.
        from agentlens.panels.session_picker import SessionPickerScreen as SPS

        assert not isinstance(app.screen, SPS)
        assert app.active_session_path is not None
        assert app.active_session_path.name == "newer.jsonl"
        assert app.locator_reason == "slug"
