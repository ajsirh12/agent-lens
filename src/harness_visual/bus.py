# bus.py - test seam for SessionWatcher. UI delivery goes via app.post_message().
"""≤30 LOC event bus. Production code never calls drain_nowait(); tests do."""
from __future__ import annotations

import asyncio

from .events import HarnessEvent


class EventBus:
    def __init__(self) -> None:
        self._q: asyncio.Queue[HarnessEvent] = asyncio.Queue()

    async def publish(self, event: HarnessEvent) -> None:
        await self._q.put(event)

    def drain_nowait(self) -> list[HarnessEvent]:
        out: list[HarnessEvent] = []
        while not self._q.empty():
            out.append(self._q.get_nowait())
        return out
