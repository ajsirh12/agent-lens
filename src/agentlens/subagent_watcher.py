"""SubagentWatcherManager — discovers and tails subagent JSONL files.

Polls ``SubagentLocator.subagents_dir`` at a fixed interval; for each
newly discovered ``agent-{agentId}.jsonl`` file it spawns a
``PollingTailer`` task that streams parsed events back to the app via
``HarnessEventMessage``. Subagent files only ever grow — they are not
rotated — so simple append-tailing is sufficient.

Every emitted event has its ``payload['subagent_uuid']`` populated by
the parser (using the top-level ``agentId`` field), so the graph model
can route internal tool_use events to the correct Agent node on the
main flowchart without creating new graph nodes.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .subagent_locator import SubagentLocator
from .watcher import PollingTailer

if TYPE_CHECKING:
    from .app import AgentlensApp

log = logging.getLogger(__name__)


class SubagentWatcherManager:
    def __init__(
        self,
        main_session_path: Path,
        *,
        discovery_interval: float = 1.0,
        tail_interval: float = 0.25,
    ) -> None:
        self.main_session_path = main_session_path
        self.discovery_interval = discovery_interval
        self.tail_interval = tail_interval
        self._locator = SubagentLocator(main_session_path=main_session_path)
        self._tasks: dict[Path, asyncio.Task[None]] = {}

    async def run(
        self,
        app: "AgentlensApp | None" = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        log.debug(
            "SubagentWatcherManager starting for %s", self.main_session_path
        )
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    self._discover_and_spawn(app, stop_event)
                except Exception as e:  # never crash the manager
                    log.debug("SubagentWatcherManager discover error: %s", e)
                try:
                    await asyncio.sleep(self.discovery_interval)
                except asyncio.CancelledError:
                    break
        finally:
            # Cancel in-flight tailers on shutdown.
            for t in list(self._tasks.values()):
                t.cancel()
            for t in list(self._tasks.values()):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    def _discover_and_spawn(
        self,
        app: "AgentlensApp | None",
        stop_event: asyncio.Event | None,
    ) -> None:
        for path in self._locator.list_files():
            if path in self._tasks:
                continue
            tailer = PollingTailer(path, interval=self.tail_interval)
            coro = self._run_tailer(tailer, app, stop_event)
            task = asyncio.create_task(coro, name=f"subagent-tail:{path.name}")
            self._tasks[path] = task

    async def _run_tailer(
        self,
        tailer: PollingTailer,
        app: "AgentlensApp | None",
        stop_event: asyncio.Event | None,
    ) -> None:
        try:
            await tailer.run(app=app, bus=None, stop_event=stop_event)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # fault tolerant — log and continue
            log.debug("subagent tailer crashed on %s: %s", tailer.path, e)
