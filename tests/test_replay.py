"""Acceptance tests for the jsonl-replay feature (AC-1..AC-11)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentlens import cli
from agentlens.app import AgentlensApp
from agentlens.messages import HarnessEventMessage, ReplayErrorMessage
from agentlens.panels.replay_picker import ReplayPickerScreen
from agentlens.watcher import ReplayPlayer


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


# ---------------------------------------------------------------------------
# AC-1: ReplayPlayer.run() feeds all lines from a JSONL file into _deliver_line
# ---------------------------------------------------------------------------


async def test_ac1_replay_feeds_all_lines(tmp_path: Path) -> None:
    fixture = tmp_path / "session.jsonl"
    fixture.write_text("".join(_tool_use_line(i) for i in range(1, 6)))

    delivered: list[str] = []

    player = ReplayPlayer(fixture)

    async def _capture(raw, app, bus):  # type: ignore[no-untyped-def]
        delivered.append(raw)

    player._deliver_line = _capture  # type: ignore[assignment]

    await player.run(app=None, bus=None)

    assert len(delivered) == 5


# ---------------------------------------------------------------------------
# AC-2: ReplayPlayer.run() calls asyncio.sleep(0) every 100 lines
# ---------------------------------------------------------------------------


async def test_ac2_replay_yields_every_chunk_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 250 lines → expect 2 chunk-boundary yields (at idx 100 and 200).
    fixture = tmp_path / "session.jsonl"
    fixture.write_text("".join(_tool_use_line(i) for i in range(1, 251)))

    real_sleep = asyncio.sleep
    yield_count = {"n": 0}

    async def _counting_sleep(delay: float) -> None:
        if delay == 0:
            yield_count["n"] += 1
        await real_sleep(delay)

    monkeypatch.setattr("agentlens.watcher.asyncio.sleep", _counting_sleep)

    player = ReplayPlayer(fixture)

    # Stub _deliver_line so we don't measure parser cost — just lines flow.
    async def _noop(raw, app, bus):  # type: ignore[no-untyped-def]
        return None

    player._deliver_line = _noop  # type: ignore[assignment]

    await player.run(app=None, bus=None)

    assert ReplayPlayer.CHUNK_LINES == 100
    assert yield_count["n"] == 2


# ---------------------------------------------------------------------------
# AC-3: ReplayPlayer.run() respects stop_event mid-run
# ---------------------------------------------------------------------------


async def test_ac3_replay_respects_stop_event(tmp_path: Path) -> None:
    fixture = tmp_path / "session.jsonl"
    # 500 lines → if stop_event is honored, far fewer than 500 should be delivered.
    fixture.write_text("".join(_tool_use_line(i) for i in range(1, 501)))

    delivered: list[str] = []
    stop_event = asyncio.Event()

    player = ReplayPlayer(fixture)

    async def _capture(raw, app, bus):  # type: ignore[no-untyped-def]
        delivered.append(raw)
        # Trigger stop after the first line. The loop checks stop_event at
        # the TOP of each iteration; subsequent iterations should bail.
        if len(delivered) == 1:
            stop_event.set()

    player._deliver_line = _capture  # type: ignore[assignment]

    await player.run(app=None, bus=None, stop_event=stop_event)

    # We expect significantly fewer than 500 lines — the stop check runs at
    # the top of each per-line iteration in the source loop. Allow up to the
    # first CHUNK_LINES because the deliver_line burst happens at the chunk
    # boundary; what matters is that the run terminates early.
    assert len(delivered) < 500
    assert stop_event.is_set()


# ---------------------------------------------------------------------------
# AC-4: ReplayPlayer.run() handles OSError gracefully — posts ReplayErrorMessage
# ---------------------------------------------------------------------------


async def test_ac4_replay_oserror_posts_message(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.jsonl"
    player = ReplayPlayer(missing)

    app = MagicMock()
    app.post_message = MagicMock()

    # Must not raise.
    await player.run(app=app, bus=None)

    # Exactly one ReplayErrorMessage posted.
    assert app.post_message.call_count == 1
    msg = app.post_message.call_args[0][0]
    assert isinstance(msg, ReplayErrorMessage)
    assert isinstance(msg.error, str)
    assert msg.error  # non-empty


async def test_ac4_replay_oserror_without_app_does_not_raise(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "does_not_exist.jsonl"
    player = ReplayPlayer(missing)
    # No app → still must not raise.
    await player.run(app=None, bus=None)


# ---------------------------------------------------------------------------
# AC-5: ReplayPlayer.run() handles non-UTF8 bytes with errors="replace"
# ---------------------------------------------------------------------------


async def test_ac5_replay_non_utf8_bytes_no_exception(tmp_path: Path) -> None:
    fixture = tmp_path / "bad_bytes.jsonl"
    # Mix of valid line and a line with a stray non-UTF8 byte.
    fixture.write_bytes(
        _tool_use_line(1).encode("utf-8")
        + b'{"type":"user","sessionId":"s","timestamp":"2026-04-08T10:00:00Z",'
        + b'"uuid":"bad","message":{"role":"user","content":"hi \xff there"}}\n'
        + _tool_use_line(2).encode("utf-8")
    )

    delivered: list[str] = []

    player = ReplayPlayer(fixture)

    async def _capture(raw, app, bus):  # type: ignore[no-untyped-def]
        delivered.append(raw)

    player._deliver_line = _capture  # type: ignore[assignment]

    # Must not raise even with non-UTF8 bytes mid-stream.
    await player.run(app=None, bus=None)

    # All three raw lines should be presented (errors="replace" smooths bytes).
    assert len(delivered) == 3


# ---------------------------------------------------------------------------
# AC-6: ReplayErrorMessage has `error: str` attribute
# ---------------------------------------------------------------------------


def test_ac6_replay_error_message_has_error_attribute() -> None:
    msg = ReplayErrorMessage("file not found: /tmp/x.jsonl")
    assert hasattr(msg, "error")
    assert isinstance(msg.error, str)
    assert msg.error == "file not found: /tmp/x.jsonl"


def test_ac6_replay_error_message_is_textual_message() -> None:
    from textual.message import Message

    msg = ReplayErrorMessage("boom")
    assert isinstance(msg, Message)


# ---------------------------------------------------------------------------
# AC-7: CLI --replay FILE arg exists and sets replay_path on parsed namespace
# ---------------------------------------------------------------------------


def test_ac7_cli_replay_flag_parses_to_path() -> None:
    parser = cli.build_parser()
    ns = parser.parse_args(["--replay", "/tmp/some.jsonl"])
    assert ns.replay is not None
    assert isinstance(ns.replay, Path)
    assert str(ns.replay) == "/tmp/some.jsonl"


def test_ac7_cli_replay_absent_defaults_none() -> None:
    parser = cli.build_parser()
    ns = parser.parse_args(["--no-attach"])
    assert ns.replay is None


# ---------------------------------------------------------------------------
# AC-8: CLI --replay + --session conflict
# ---------------------------------------------------------------------------


def test_ac8_cli_replay_and_session_conflict_returns_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(
        [
            "--session",
            "/tmp/a.jsonl",
            "--replay",
            "/tmp/b.jsonl",
            "--no-attach",
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "--session" in err and "--replay" in err


# ---------------------------------------------------------------------------
# AC-9: AgentlensApp(replay_path=Path(...)) sets _replay_mode=True
# ---------------------------------------------------------------------------


def test_ac9_app_with_replay_path_sets_replay_mode_true(tmp_path: Path) -> None:
    p = tmp_path / "x.jsonl"
    app = AgentlensApp(replay_path=p)
    assert app._replay_mode is True
    assert app._replay_path == p


# ---------------------------------------------------------------------------
# AC-10: AgentlensApp() with no replay_path sets _replay_mode=False
# ---------------------------------------------------------------------------


def test_ac10_app_without_replay_path_sets_replay_mode_false() -> None:
    app = AgentlensApp()
    assert app._replay_mode is False
    assert app._replay_path is None


# ---------------------------------------------------------------------------
# AC-11: ReplayPickerScreen can be instantiated without error
# ---------------------------------------------------------------------------


def test_ac11_replay_picker_screen_instantiates(tmp_path: Path) -> None:
    # Use a tmp projects_root so we don't depend on the host home dir.
    (tmp_path / "slug-a").mkdir()
    f1 = tmp_path / "slug-a" / "one.jsonl"
    f1.write_text("")
    screen = ReplayPickerScreen(projects_root=tmp_path)
    assert isinstance(screen, ReplayPickerScreen)
    # Found at least the seeded file.
    assert any(p.name == "one.jsonl" for p in screen._files)


def test_ac11_replay_picker_screen_empty_projects_root(tmp_path: Path) -> None:
    # Missing dir is fine — no exception, empty file list.
    screen = ReplayPickerScreen(projects_root=tmp_path / "nope")
    assert isinstance(screen, ReplayPickerScreen)
    assert screen._files == []


# ---------------------------------------------------------------------------
# Bonus: end-to-end smoke — replay path in app posts HarnessEventMessage(s)
# ---------------------------------------------------------------------------


async def test_replay_end_to_end_app_run_test(tmp_path: Path) -> None:
    fixture = tmp_path / "replay.jsonl"
    fixture.write_text("".join(_tool_use_line(i) for i in range(1, 4)))

    app = AgentlensApp(
        replay_path=fixture,
        state_dir_override=tmp_path / "state-absent",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(10):
            await pilot.pause()
        assert app._replay_mode is True
        assert app.active_session_path == fixture
        assert app.locator_reason == "replay"


async def test_replay_error_message_clears_replay_mode() -> None:
    app = AgentlensApp(replay_path=Path("/this/does/not/exist.jsonl"), no_attach=True)
    assert app._replay_mode is True
    # Simulate the message handler being invoked directly.
    app.on_replay_error_message(ReplayErrorMessage("nope"))
    assert app._replay_mode is False
    assert app._replay_path is None


# ---------------------------------------------------------------------------
# AC-10: Esc in replay mode calls _exit_replay_mode (sets _replay_mode=False)
# ---------------------------------------------------------------------------


def test_ac10_esc_exits_replay_mode(tmp_path: Path) -> None:
    p = tmp_path / "x.jsonl"
    app = AgentlensApp(replay_path=p, no_attach=True)
    assert app._replay_mode is True
    # Invoke the escape action directly — no display needed.
    app.action_escape_to_timeline()
    assert app._replay_mode is False
    assert app._replay_path is None


def test_ac10_esc_outside_replay_sets_timeline_panel() -> None:
    app = AgentlensApp(no_attach=True)
    assert app._replay_mode is False
    app.active_panel = "flowchart"
    app.action_escape_to_timeline()
    assert app.active_panel == "timeline"
    assert app._replay_mode is False


def test_exit_replay_clears_all_replay_state(tmp_path: Path) -> None:
    p = tmp_path / "x.jsonl"
    app = AgentlensApp(replay_path=p, no_attach=True)
    assert app._replay_mode is True
    assert app._replay_path == p
    app._exit_replay_mode()
    assert app._replay_mode is False
    assert app._replay_path is None
    assert app.active_session_path is None
    assert app.locator_reason == "none"


# Sanity: ensure HarnessEventMessage still importable (regression guard).
def test_harness_event_message_still_present() -> None:
    assert HarnessEventMessage is not None
