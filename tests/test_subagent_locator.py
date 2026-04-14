"""Tests for SubagentLocator — pure data path & filename parsing."""

from __future__ import annotations

from pathlib import Path

from agentlens.subagent_locator import SubagentLocator


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


# ---------------------------------------------------------------------------
# SubagentWatcherManager._emit_meta_link
# ---------------------------------------------------------------------------


def _patch_harness_event_message(monkeypatch: object) -> None:
    """Inject a fake HarnessEventMessage into sys.modules so _emit_meta_link
    works without textual installed (the real class inherits textual.Message)."""
    import sys
    from agentlens.events import HarnessEvent  # noqa: PLC0415

    class FakeHarnessEventMessage:
        def __init__(self, event: HarnessEvent) -> None:
            self.event = event

    import types
    fake_mod = types.ModuleType("agentlens.messages")
    fake_mod.HarnessEventMessage = FakeHarnessEventMessage  # type: ignore[attr-defined]
    sys.modules["agentlens.messages"] = fake_mod


def test_emit_meta_link_posts_event_when_meta_json_exists(
    tmp_path: Path, monkeypatch: object
) -> None:
    """_emit_meta_link reads .meta.json and posts a subagent_meta_link event."""
    import json
    from agentlens.events import EventType
    _patch_harness_event_message(monkeypatch)
    from agentlens.subagent_watcher import SubagentWatcherManager

    main = tmp_path / "session.jsonl"
    main.write_text("")
    sd = tmp_path / "session" / "subagents"
    sd.mkdir(parents=True)

    jsonl = sd / "agent-aabbccdd11223344.jsonl"
    jsonl.write_text("")
    (sd / "agent-aabbccdd11223344.meta.json").write_text(
        json.dumps({"agentType": "verifier-a"})
    )

    posted = []

    class FakeApp:
        def post_message(self, msg: object) -> None:
            posted.append(msg)

    mgr = SubagentWatcherManager(main_session_path=main)
    mgr._emit_meta_link(jsonl, FakeApp())  # type: ignore[arg-type]

    assert len(posted) == 1
    ev = posted[0].event
    assert ev.type == EventType.subagent_meta_link
    assert ev.payload["hex_id"] == "aabbccdd11223344"
    assert ev.payload["agent_type"] == "verifier-a"


def test_emit_meta_link_no_meta_json_is_noop(
    tmp_path: Path, monkeypatch: object
) -> None:
    """_emit_meta_link does nothing when .meta.json is absent."""
    _patch_harness_event_message(monkeypatch)
    from agentlens.subagent_watcher import SubagentWatcherManager

    main = tmp_path / "session.jsonl"
    main.write_text("")
    sd = tmp_path / "session" / "subagents"
    sd.mkdir(parents=True)

    jsonl = sd / "agent-aabbccdd11223344.jsonl"
    jsonl.write_text("")

    posted = []

    class FakeApp:
        def post_message(self, msg: object) -> None:
            posted.append(msg)

    mgr = SubagentWatcherManager(main_session_path=main)
    mgr._emit_meta_link(jsonl, FakeApp())  # type: ignore[arg-type]

    assert posted == []


def test_emit_meta_link_app_none_is_noop(tmp_path: Path) -> None:
    """_emit_meta_link does nothing when app is None — no exception raised."""
    import json
    from agentlens.subagent_watcher import SubagentWatcherManager

    main = tmp_path / "session.jsonl"
    main.write_text("")
    sd = tmp_path / "session" / "subagents"
    sd.mkdir(parents=True)

    jsonl = sd / "agent-aabbccdd11223344.jsonl"
    jsonl.write_text("")
    (sd / "agent-aabbccdd11223344.meta.json").write_text(
        json.dumps({"agentType": "some-agent"})
    )

    mgr = SubagentWatcherManager(main_session_path=main)
    mgr._emit_meta_link(jsonl, None)  # must not raise
