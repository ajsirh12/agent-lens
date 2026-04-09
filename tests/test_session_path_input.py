"""Tests for the Shift+S "open session by path" modal."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlens.app import AgentlensApp
from agentlens.panels.session_path_input import SessionPathInputScreen


# ---------------------------------------------------------------------
# Validation unit tests (no Textual lifecycle)
# ---------------------------------------------------------------------


def _make_screen() -> SessionPathInputScreen:
    """Construct a screen without mounting it.

    ``_validate`` touches the #path-input-error Static via query_one,
    but the error helper guards with a try/except so pre-mount calls
    silently no-op.
    """
    return SessionPathInputScreen()


def test_validate_accepts_existing_jsonl(tmp_path: Path) -> None:
    session = tmp_path / "abc.jsonl"
    session.write_text("{}\n")
    screen = _make_screen()
    resolved = screen._validate(str(session))
    assert resolved == session


def test_validate_expands_home_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Path.expanduser() reads the HOME env var directly, not Path.home().
    monkeypatch.setenv("HOME", str(tmp_path))
    # Windows fallbacks used by expanduser if HOME is missing.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    session = tmp_path / "via-home.jsonl"
    session.write_text("{}\n")
    screen = _make_screen()
    resolved = screen._validate("~/via-home.jsonl")
    assert resolved == session


def test_validate_strips_surrounding_quotes(tmp_path: Path) -> None:
    session = tmp_path / "quoted.jsonl"
    session.write_text("{}\n")
    screen = _make_screen()
    # Users often paste paths wrapped in quotes (e.g. from a terminal
    # that quoted a path with spaces).
    assert screen._validate(f'"{session}"') == session
    assert screen._validate(f"'{session}'") == session


def test_validate_rejects_empty_input() -> None:
    screen = _make_screen()
    assert screen._validate("") is None
    assert screen._validate("   ") is None


def test_validate_rejects_missing_file(tmp_path: Path) -> None:
    screen = _make_screen()
    missing = tmp_path / "does-not-exist.jsonl"
    assert screen._validate(str(missing)) is None


def test_validate_rejects_directory(tmp_path: Path) -> None:
    screen = _make_screen()
    assert screen._validate(str(tmp_path)) is None


def test_validate_rejects_non_jsonl_suffix(tmp_path: Path) -> None:
    screen = _make_screen()
    f = tmp_path / "not-a-session.txt"
    f.write_text("{}")
    assert screen._validate(str(f)) is None


# ---------------------------------------------------------------------
# Session ID / prefix resolution
# ---------------------------------------------------------------------


def _make_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a fake ~/.claude/projects under tmp_path and patch home."""
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return projects


def test_validate_resolves_full_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = _make_projects(tmp_path, monkeypatch)
    d = projects / "-some-project"
    d.mkdir()
    target = d / "b0709256-eb61-4ccb-9b57-49aaca263c33.jsonl"
    target.write_text("{}\n")
    screen = _make_screen()
    assert (
        screen._validate("b0709256-eb61-4ccb-9b57-49aaca263c33") == target
    )


def test_validate_resolves_unique_session_id_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = _make_projects(tmp_path, monkeypatch)
    d = projects / "-some-project"
    d.mkdir()
    target = d / "b0709256-eb61-4ccb-9b57-49aaca263c33.jsonl"
    target.write_text("{}\n")
    # Also add a clearly different one so the prefix is still unique.
    other = d / "aaaaaaaa-eeee-4ccb-9b57-49aaca263c33.jsonl"
    other.write_text("{}\n")
    screen = _make_screen()
    assert screen._validate("b0709256") == target


def test_validate_rejects_ambiguous_session_id_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = _make_projects(tmp_path, monkeypatch)
    d = projects / "-some-project"
    d.mkdir()
    (d / "b0709256-eb61-4ccb-9b57-49aaca263c33.jsonl").write_text("{}\n")
    (d / "b0709256-aaaa-bbbb-cccc-dddddddddddd.jsonl").write_text("{}\n")
    screen = _make_screen()
    assert screen._validate("b0709256") is None


def test_validate_rejects_unknown_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_projects(tmp_path, monkeypatch)
    screen = _make_screen()
    assert screen._validate("ffffffff-no-such-session") is None


def test_validate_resolves_across_multiple_project_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lookup walks every project subdir, not just the current slug."""
    projects = _make_projects(tmp_path, monkeypatch)
    (projects / "-dir-a").mkdir()
    (projects / "-dir-b").mkdir()
    (projects / "-dir-a" / "noise.jsonl").write_text("{}\n")
    target = projects / "-dir-b" / "cafebabe-1234.jsonl"
    target.write_text("{}\n")
    screen = _make_screen()
    assert screen._validate("cafebabe") == target


# ---------------------------------------------------------------------
# App integration
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shift_s_pushes_path_input_modal(tmp_path: Path) -> None:
    (tmp_path / "empty.jsonl").write_text("")
    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Trigger the shift+s binding directly via pilot.
        await pilot.press("shift+s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPathInputScreen)


@pytest.mark.asyncio
async def test_uppercase_s_also_pushes_path_input_modal(tmp_path: Path) -> None:
    """A real terminal on Shift+s sends the literal 'S' character, which
    Textual routes to the "S" binding rather than "shift+s". Both forms
    must open the modal.
    """
    (tmp_path / "empty.jsonl").write_text("")
    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        assert isinstance(app.screen, SessionPathInputScreen)


@pytest.mark.asyncio
async def test_open_session_path_swaps_active_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "original.jsonl"
    target = tmp_path / "target.jsonl"
    original.write_text("")
    target.write_text("")

    app = AgentlensApp(
        session_override=original,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_session_path == original

        # Intercept push_screen so we can resolve the modal synchronously
        # without actually mounting a real modal.
        captured: dict = {}

        def fake_push(screen, callback=None):
            captured["screen"] = screen
            captured["callback"] = callback

        monkeypatch.setattr(app, "push_screen", fake_push)
        app.action_open_session_path()
        assert isinstance(captured["screen"], SessionPathInputScreen)

        # Fire the callback with the target path as if the user submitted.
        captured["callback"](target)
        await pilot.pause()

        assert app.active_session_path == target
        assert app.locator_reason == "path-input"
        # Timeline and Flowchart should have been cleared
        # (ends with only the root "main" node in the flowchart).
        assert app._flowchart is not None
        assert len(app._flowchart._graph.nodes) == 1


@pytest.mark.asyncio
async def test_open_session_path_none_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel (dismiss with None) must leave state unchanged."""
    original = tmp_path / "original.jsonl"
    original.write_text("")
    app = AgentlensApp(
        session_override=original,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        captured: dict = {}

        def fake_push(screen, callback=None):
            captured["callback"] = callback

        monkeypatch.setattr(app, "push_screen", fake_push)
        app.action_open_session_path()
        captured["callback"](None)  # simulate cancel
        await pilot.pause()

        assert app.active_session_path == original
        assert app.locator_reason != "path-input"


@pytest.mark.asyncio
async def test_open_session_path_same_file_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Submitting the currently-attached file must not reset state."""
    original = tmp_path / "original.jsonl"
    original.write_text("")
    app = AgentlensApp(
        session_override=original,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        captured: dict = {}

        def fake_push(screen, callback=None):
            captured["callback"] = callback

        monkeypatch.setattr(app, "push_screen", fake_push)
        app.action_open_session_path()
        captured["callback"](original)  # same file
        await pilot.pause()

        assert app.active_session_path == original
        # Should not have been switched to the path-input reason.
        assert app.locator_reason != "path-input"
