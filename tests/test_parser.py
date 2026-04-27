"""Parser unit tests — AC10 (schema tolerance)."""

from __future__ import annotations

import json

from agentlens.events import EventType
from agentlens.parser import _extract_usage, parse_line


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


def test_parse_line_truncates_raw_line_at_cap():
    from agentlens.parser import MAX_RAW_LINE

    # Build a valid tool_use JSONL line that is well over 100 KB by stuffing a
    # long string into the tool input.
    big_input = "x" * 100_000
    row = {
        "type": "assistant",
        "sessionId": "sess-trunc",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "u-trunc",
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_trunc",
                    "name": "Bash",
                    "input": {"command": big_input},
                }
            ],
        },
    }
    big_line = json.dumps(row)
    assert len(big_line) > 100_000  # confirm the line is actually big

    events = parse_line(big_line)
    assert events, "expected at least one event"
    for ev in events:
        raw = ev.raw_line
        if raw:
            assert len(raw) <= MAX_RAW_LINE, (
                f"raw_line length {len(raw)} exceeds MAX_RAW_LINE {MAX_RAW_LINE}"
            )


# --- _extract_usage unit tests (FR-7 / AC10) ---


def test__extract_usage_normal() -> None:
    msg = {
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 3,
        }
    }
    result = _extract_usage(msg)
    assert result is not None
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 20
    assert result["cache_creation_input_tokens"] == 5
    assert result["cache_read_input_tokens"] == 3


def test__extract_usage_null() -> None:
    msg = {"usage": None}
    result = _extract_usage(msg)
    assert result is None


def test__extract_usage_str_coerce() -> None:
    msg = {
        "usage": {
            "input_tokens": "6",
            "output_tokens": 2,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": 0,
        }
    }
    result = _extract_usage(msg)
    # string "6" → parser coerces via int(); "6" parses to 6
    assert result is not None
    assert result["input_tokens"] == 6
    # None → coerce to 0
    assert result["cache_creation_input_tokens"] == 0


def test__extract_usage_negative() -> None:
    msg = {
        "usage": {
            "input_tokens": -1,
            "output_tokens": -99,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    }
    result = _extract_usage(msg)
    assert result is not None
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0


def test__first_event_only() -> None:
    """assistant row with text+tool_use+text → only the first event carries 'usage'."""
    row = {
        "type": "assistant",
        "sessionId": "sess-1",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "uuid-first",
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "usage": {"input_tokens": 7, "output_tokens": 3,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {"command": "ls"}},
                {"type": "text", "text": "done"},
            ],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 3
    # Only the first event should carry usage.
    assert "usage" in events[0].payload
    for ev in events[1:]:
        assert "usage" not in ev.payload


def test__empty_blocks_no_crash() -> None:
    """assistant row with empty content list must not raise."""
    row = {
        "type": "assistant",
        "sessionId": "sess-empty",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "uuid-empty",
        "isSidechain": False,
        "message": {"role": "assistant", "content": []},
    }
    # Must not raise.
    events = parse_line(json.dumps(row))
    assert isinstance(events, list)


# --- User prompt cap relaxation (turn-detail-tokens F1) ---


def test_user_prompt_text_preserved_above_legacy_500_cap() -> None:
    """Real user prompts (top_type=user, not isMeta, not isSidechain) keep
    up to _USER_PROMPT_MAX_LEN chars instead of the legacy 500-char cap.

    Uses 1500-char prompt as a representative value safely above 500 and
    well below 10_000.
    """
    big_prompt = "u" * 1500
    row = {
        "type": "user",
        "sessionId": "sess-prompt",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "uuid-prompt",
        "isSidechain": False,
        "isMeta": False,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": big_prompt}],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 1
    assert events[0].type == EventType.user_message
    assert len(events[0].payload["text"]) == 1500
    assert events[0].payload["text"] == big_prompt


def test_assistant_text_still_capped_at_500() -> None:
    """Assistant text blocks must keep the legacy 500-char cap even after
    the user-prompt relaxation (regression guard).
    """
    big_text = "a" * 1500
    row = {
        "type": "assistant",
        "sessionId": "sess-asst",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "uuid-asst",
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": big_text}],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 1
    assert events[0].type == EventType.assistant_message
    assert len(events[0].payload["text"]) == 500


def test_meta_user_text_still_capped_at_500() -> None:
    """isMeta=True user rows are system-injected notices, not real prompts,
    and must keep the 500-char cap (regression guard).
    """
    big_text = "m" * 1500
    row = {
        "type": "user",
        "sessionId": "sess-meta",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "uuid-meta",
        "isSidechain": False,
        "isMeta": True,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": big_text}],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 1
    assert len(events[0].payload["text"]) == 500


def test_sidechain_user_text_still_capped_at_500() -> None:
    """Sidechain user rows are sub-agent prompts, not main-session prompts,
    and must keep the 500-char cap (regression guard).
    """
    big_text = "s" * 1500
    row = {
        "type": "user",
        "sessionId": "sess-side",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "uuid-side",
        "parentUuid": "parentuuid-aaa",
        "isSidechain": True,
        "isMeta": False,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": big_text}],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 1
    assert len(events[0].payload["text"]) == 500


def test_user_prompt_caps_at_user_prompt_max_len() -> None:
    """User prompts longer than _USER_PROMPT_MAX_LEN (10_000) get capped."""
    from agentlens.parser import _USER_PROMPT_MAX_LEN

    big_prompt = "z" * (_USER_PROMPT_MAX_LEN + 5_000)
    row = {
        "type": "user",
        "sessionId": "sess-overflow",
        "timestamp": "2026-04-08T10:00:00Z",
        "uuid": "uuid-overflow",
        "isSidechain": False,
        "isMeta": False,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": big_prompt}],
        },
    }
    events = parse_line(json.dumps(row))
    assert len(events) == 1
    assert len(events[0].payload["text"]) == _USER_PROMPT_MAX_LEN
