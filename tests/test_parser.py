"""Parser unit tests — AC10 (schema tolerance)."""

from __future__ import annotations

import json

from harness_visual.events import EventType
from harness_visual.parser import parse_line


def test_empty_line_returns_empty_list():
    assert parse_line("") == []
    assert parse_line("   \n") == []


def test_malformed_json_does_not_raise():
    events = parse_line("{not valid json")
    assert len(events) == 1
    assert events[0].type == EventType.unknown


def test_unknown_top_type_is_unknown_event():
    line = json.dumps({"type": "rumpelstiltskin", "timestamp": "2026-04-08T00:00:00Z"})
    events = parse_line(line)
    assert len(events) == 1
    assert events[0].type == EventType.unknown


def test_tool_use_assistant_row_parses():
    row = {
        "type": "assistant",
        "sessionId": "sess-1",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "uuid-1",
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_abc", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 1
    assert events[0].type == EventType.tool_use
    assert events[0].payload["tool_name"] == "Bash"
    assert events[0].payload["tool_use_id"] == "toolu_abc"
    assert events[0].agent_id == "sess-1"


def test_tool_result_user_row_parses():
    row = {
        "type": "user",
        "sessionId": "sess-1",
        "timestamp": "2026-04-08T10:00:01Z",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_abc",
                    "content": "ok",
                    "is_error": False,
                },
            ],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 1
    assert events[0].type == EventType.tool_result
    assert events[0].payload["tool_use_id"] == "toolu_abc"
    assert events[0].is_error is False


def test_sidechain_row_gets_sub_agent_id():
    row = {
        "type": "assistant",
        "sessionId": "sess-1",
        "isSidechain": True,
        "parentUuid": "parentuuid12345",
        "timestamp": "2026-04-08T10:00:00Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    }
    events = parse_line(json.dumps(row))
    assert events[0].agent_id is not None
    assert events[0].agent_id.startswith("sub:")
