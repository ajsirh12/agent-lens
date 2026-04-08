"""Watcher tests — uses PollingTailer + EventBus test seam."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness_visual.bus import EventBus
from harness_visual.events import EventType
from harness_visual.watcher import PollingTailer


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

    spec = find_spec("harness_visual.bus")
    assert spec is not None and spec.origin is not None
    lines = Path(spec.origin).read_text().splitlines()
    assert len(lines) <= 30, f"bus.py is {len(lines)} lines, must be ≤30"
