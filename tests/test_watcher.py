"""Watcher tests — uses PollingTailer + EventBus test seam."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentlens.bus import EventBus
from agentlens.events import EventType
from agentlens.watcher import PollingTailer


def _line(idx: int) -> str:
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
async def test_polling_tailer_reads_appended_lines(tmp_path: Path) -> None:
    target = tmp_path / "session.jsonl"
    target.write_text("")

    bus = EventBus()
    tailer = PollingTailer(target, interval=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(tailer.run(app=None, bus=bus, stop_event=stop))

    # Append 3 lines.
    with target.open("a") as f:
        for i in range(3):
            f.write(_line(i))

    # Wait up to 1s for delivery.
    for _ in range(20):
        await asyncio.sleep(0.05)
        events = bus.drain_nowait()
        if len(events) >= 3:
            break
    else:
        events = []

    stop.set()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    tool_uses = [e for e in events if e.type == EventType.tool_use]
    assert len(tool_uses) >= 3


@pytest.mark.asyncio
async def test_polling_tailer_handles_truncation(tmp_path: Path) -> None:
    target = tmp_path / "session.jsonl"
    target.write_text(_line(1))

    bus = EventBus()
    tailer = PollingTailer(target, interval=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(tailer.run(app=None, bus=bus, stop_event=stop))

    # Let it read the initial line.
    await asyncio.sleep(0.2)
    bus.drain_nowait()  # discard initial

    # Truncate + rewrite (use explicit truncate + atomic-ish rewrite).
    with target.open("w") as f:
        f.write(_line(99))
        f.flush()
    # Bump mtime explicitly for reliability.
    import os as _os
    st = target.stat()
    _os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))

    events: list = []
    for _ in range(40):
        await asyncio.sleep(0.05)
        events.extend(bus.drain_nowait())
        if any(
            e.type == EventType.tool_use and e.payload.get("tool_use_id") == "toolu_99"
            for e in events
        ):
            break

    stop.set()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    tool_uses = [e for e in events if e.type == EventType.tool_use]
    assert any(e.payload.get("tool_use_id") == "toolu_99" for e in tool_uses), (
        f"rotation not detected; events={events}"
    )


def test_bus_line_count_is_at_most_30() -> None:
    from importlib.util import find_spec

    spec = find_spec("agentlens.bus")
    assert spec is not None and spec.origin is not None
    lines = Path(spec.origin).read_text().splitlines()
    assert len(lines) <= 30, f"bus.py is {len(lines)} lines, must be ≤30"


def test_watcher_clears_head_fingerprint_on_rotation(tmp_path: Path) -> None:
    """After rotation the stale fingerprint must be cleared so the next tick
    captures a fresh one instead of triggering a second spurious rotation."""
    target = tmp_path / "session.jsonl"
    # Write initial content and let the tailer read it so it captures a
    # fingerprint.
    target.write_bytes(b"first content line\n")
    tailer = PollingTailer(target, interval=0.05)
    tailer._read_appended()  # primes _head_fingerprint

    old_fingerprint = tailer._head_fingerprint
    assert old_fingerprint, "fingerprint should have been captured after first read"

    # Overwrite the file with completely different content that is shorter
    # (triggers size-shrink / truncation rotation path) and bump mtime.
    target.write_bytes(b"X\n")
    import os as _os
    st = target.stat()
    _os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))

    # One tick: should detect rotation, reset offset, and clear fingerprint.
    tailer._read_appended()

    # A subsequent tick should now hold a fingerprint for the NEW file content.
    tailer._read_appended()
    new_fingerprint = tailer._head_fingerprint

    assert new_fingerprint != old_fingerprint, (
        "fingerprint was not refreshed after rotation; stale fingerprint still cached"
    )


def test_watcher_drops_oversized_unterminated_line(tmp_path: Path) -> None:
    """A pathological file with no newline and >1 MiB content must be dropped
    rather than retained in _buffer, preventing unbounded memory growth."""
    from agentlens.watcher import MAX_BUFFER_BYTES

    target = tmp_path / "big.jsonl"
    # Write more than MAX_BUFFER_BYTES with no newline character.
    target.write_bytes(b"A" * (MAX_BUFFER_BYTES + 1))

    tailer = PollingTailer(target, interval=0.05)
    # Should not raise.
    tailer._read_appended()

    assert tailer._buffer == b"", (
        f"expected empty buffer after oversized drop, got {len(tailer._buffer)} bytes"
    )


def test_watchfiles_fallback_copies_all_state(tmp_path: Path) -> None:
    """WatchfilesTailer fallback must copy all five state fields to PollingTailer
    so it continues exactly where the original tailer left off."""
    import sys
    import unittest.mock as mock

    from agentlens.watcher import WatchfilesTailer

    target = tmp_path / "session.jsonl"
    target.write_bytes(b"")

    wt = WatchfilesTailer(target)
    # Manually set all state fields to known values.
    wt._offset = 100
    wt._inode = 42
    wt._mtime_ns = 999
    wt._buffer = b"pending"
    wt._head_fingerprint = b"abc"

    captured: list = []

    async def _fake_run(self_inner, **kwargs):  # type: ignore[override]
        captured.append(self_inner)

    # Patch PollingTailer.run so we can inspect the constructed instance
    # without actually running the event loop poll.
    with mock.patch("agentlens.watcher.PollingTailer.run", _fake_run):
        # Force the ImportError branch by hiding watchfiles.
        with mock.patch.dict(sys.modules, {"watchfiles": None}):
            # run() is async; drive it with asyncio.run.
            asyncio.run(wt.run(app=None, bus=None, stop_event=None))

    assert captured, "fallback PollingTailer.run was never called"
    fallback = captured[0]
    assert fallback._offset == 100
    assert fallback._inode == 42
    assert fallback._mtime_ns == 999
    assert fallback._buffer == b"pending"
    assert fallback._head_fingerprint == b"abc"
