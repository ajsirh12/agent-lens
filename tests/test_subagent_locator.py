"""Tests for SubagentLocator — pure data path & filename parsing."""

from __future__ import annotations

from pathlib import Path

from harness_visual.subagent_locator import SubagentLocator


def test_subagents_dir_computation(tmp_path: Path) -> None:
    main = tmp_path / "abc.jsonl"
    main.write_text("")
    loc = SubagentLocator(main_session_path=main)
    assert loc.subagents_dir == tmp_path / "abc" / "subagents"


def test_list_files_missing_dir(tmp_path: Path) -> None:
    main = tmp_path / "nope.jsonl"
    main.write_text("")
    loc = SubagentLocator(main_session_path=main)
    assert loc.list_files() == []


def test_list_files_empty_dir(tmp_path: Path) -> None:
    main = tmp_path / "session.jsonl"
    main.write_text("")
    (tmp_path / "session" / "subagents").mkdir(parents=True)
    loc = SubagentLocator(main_session_path=main)
    assert loc.list_files() == []


def test_list_files_populated(tmp_path: Path) -> None:
    main = tmp_path / "session.jsonl"
    main.write_text("")
    sd = tmp_path / "session" / "subagents"
    sd.mkdir(parents=True)
    (sd / "agent-abc123def456.jsonl").write_text("")
    (sd / "agent-ffffeeee1111.jsonl").write_text("")
    (sd / "ignored.txt").write_text("")
    (sd / "agent-abc123def456.meta.json").write_text("")
    loc = SubagentLocator(main_session_path=main)
    files = loc.list_files()
    names = sorted(p.name for p in files)
    assert names == ["agent-abc123def456.jsonl", "agent-ffffeeee1111.jsonl"]


def test_agent_id_from_filename() -> None:
    assert (
        SubagentLocator.agent_id_from_filename(Path("agent-a48d2d1088dd1be44.jsonl"))
        == "a48d2d1088dd1be44"
    )
    assert SubagentLocator.agent_id_from_filename(Path("agent-abc.meta.json")) is None
    assert SubagentLocator.agent_id_from_filename(Path("not-an-agent.jsonl")) is None
