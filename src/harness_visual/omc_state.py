"""OMC state reader — emits agent_spawn / agent_status events by diffing JSON snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .bus import EventBus
from .events import EventType, HarnessEvent

if TYPE_CHECKING:
    from .app import HarnessVisualApp

log = logging.getLogger(__name__)


class OmcStateReader:
    """Periodically snapshots .omc/state/ and emits diff events."""

    def __init__(self, state_dir: Path, interval: float = 0.5) -> None:
        self.state_dir = state_dir
        self.interval = interval
        self._known_agents: set[str] = set()
        self._agent_status: dict[str, str] = {}
        self._mission_hash: str = ""

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _safe_load_json(path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, PermissionError):
            return None
        except json.JSONDecodeError as e:
            log.debug("omc_state: bad JSON %s: %s", path, e)
            return None

    def _hash_bytes(self, path: Path) -> str:
        try:
            return hashlib.sha1(path.read_bytes()).hexdigest()
        except (FileNotFoundError, PermissionError):
            return ""

    def _subagent_entries(self, data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        """Normalize subagent-tracking.json into {agent_id: entry}."""
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        # Two common shapes: {agents: [...]}  or  {agent_id: entry, ...}
        agents = data.get("agents")
        if isinstance(agents, list):
            for a in agents:
                if not isinstance(a, dict):
                    continue
                aid = a.get("id") or a.get("agent_id") or a.get("name")
                if aid:
                    out[str(aid)] = a
            return out
        for k, v in data.items():
            if isinstance(v, dict):
                out[str(k)] = v
        return out

    def _emit_diff(self, entries: dict[str, dict[str, Any]]) -> list[HarnessEvent]:
        out: list[HarnessEvent] = []
        now = datetime.now(timezone.utc)
        for aid, entry in entries.items():
            status = str(entry.get("status") or entry.get("state") or "unknown")
            if aid not in self._known_agents:
                self._known_agents.add(aid)
                self._agent_status[aid] = status
                out.append(
                    HarnessEvent(
                        type=EventType.agent_spawn,
                        ts=now,
                        agent_id=aid,
                        payload={
                            "parent_id": entry.get("parent_id")
                            or entry.get("parent")
                            or None,
                            "label": entry.get("name") or entry.get("type") or aid,
                            "status": status,
                        },
                    )
                )
            elif self._agent_status.get(aid) != status:
                self._agent_status[aid] = status
                out.append(
                    HarnessEvent(
                        type=EventType.agent_status,
                        ts=now,
                        agent_id=aid,
                        payload={"status": status},
                    )
                )
        return out

    # --- main loop -------------------------------------------------------

    async def tick(self) -> list[HarnessEvent]:
        """One pass. Returns events to deliver. Public for tests."""
        events: list[HarnessEvent] = []
        if not self.state_dir.is_dir():
            return events

        subagent = self.state_dir / "subagent-tracking.json"
        mission = self.state_dir / "mission-state.json"

        data = self._safe_load_json(subagent)
        if data is not None:
            events.extend(self._emit_diff(self._subagent_entries(data)))

        # mission-state.json: hash-cache to avoid re-parsing large files.
        if mission.exists():
            h = self._hash_bytes(mission)
            if h and h != self._mission_hash:
                self._mission_hash = h
                mdata = self._safe_load_json(mission)
                if isinstance(mdata, dict):
                    subs = mdata.get("subagents") or mdata.get("agents")
                    if isinstance(subs, (list, dict)):
                        events.extend(
                            self._emit_diff(
                                self._subagent_entries(
                                    {"agents": subs} if isinstance(subs, list) else subs
                                )
                            )
                        )

        # sessions/{id}/ is optional — only probe if present.
        sessions = self.state_dir / "sessions"
        if sessions.is_dir():
            try:
                for _ in sessions.iterdir():
                    break  # existence check only; cheap
            except (PermissionError, FileNotFoundError):
                pass

        return events

    async def run(
        self,
        app: "HarnessVisualApp | None" = None,
        bus: EventBus | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        from .messages import HarnessEventMessage

        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                events = await self.tick()
            except Exception as e:
                log.debug("omc_state tick error: %s", e)
                events = []
            for ev in events:
                if app is not None:
                    app.post_message(HarnessEventMessage(ev))
                if bus is not None:
                    await bus.publish(ev)
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                return
