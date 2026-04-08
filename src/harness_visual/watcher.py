"""SessionWatcher implementations: WatchfilesTailer + PollingTailer."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

# A single line exceeding 1 MiB is either corrupted or adversarial; discard
# rather than OOM.
MAX_BUFFER_BYTES = 1_048_576

from .bus import EventBus
from .events import HarnessEvent
from .parser import parse_line

if TYPE_CHECKING:
    from .app import HarnessVisualApp

log = logging.getLogger(__name__)


class SessionWatcher:
    """Base class. Concrete subclasses implement `run()`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._offset = 0
        self._buffer = b""
        self._inode: int | None = None
        self._mtime_ns: int = 0
        self._head_fingerprint: bytes = b""

    async def _deliver(
        self,
        event: HarnessEvent,
        app: "HarnessVisualApp | None",
        bus: EventBus | None,
    ) -> None:
        if app is not None:
            from .messages import HarnessEventMessage

            app.post_message(HarnessEventMessage(event))
        if bus is not None:
            await bus.publish(event)

    _HEAD_BYTES = 256

    def _read_appended(self) -> list[str]:
        """Read new bytes since last offset. Returns complete lines."""
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return []
        # Rotation detection: (a) inode changed, (b) size shrank, or
        # (c) the first HEAD_BYTES bytes differ from the fingerprint we saw
        # last tick — that catches in-place rewrites that grow the file.
        if self._inode is None:
            self._inode = st.st_ino
        elif st.st_ino != self._inode:
            log.debug("inode change on %s, resetting offset", self.path)
            self._inode = st.st_ino
            self._offset = 0
            self._buffer = b""
            # Clear stale fingerprint so the next tick captures a fresh one
            # from the new file, preventing a spurious second rotation trigger.
            self._head_fingerprint = b""
        if st.st_size < self._offset:
            log.debug("truncation detected on %s, resetting offset", self.path)
            self._offset = 0
            self._buffer = b""
            # Clear stale fingerprint so the next tick captures a fresh one
            # from the truncated/rewritten file, preventing a spurious second
            # rotation trigger.
            self._head_fingerprint = b""

        # Fingerprint check — only meaningful when mtime advanced.
        if self._mtime_ns and st.st_mtime_ns != self._mtime_ns:
            try:
                with self.path.open("rb") as fh:
                    head = fh.read(self._HEAD_BYTES)
            except FileNotFoundError:
                head = b""
            if self._head_fingerprint and head and head != self._head_fingerprint:
                log.debug("head fingerprint changed on %s, resetting offset", self.path)
                self._offset = 0
                self._buffer = b""
                # Clear stale fingerprint so the next tick captures a fresh one
                # from the rewritten file, preventing a spurious second rotation
                # trigger on the following tick.
                self._head_fingerprint = b""
            if head:
                self._head_fingerprint = head

        self._mtime_ns = st.st_mtime_ns
        if st.st_size == self._offset:
            return []
        try:
            with self.path.open("rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
                # First-time read: capture fingerprint from start of file.
                if not self._head_fingerprint:
                    fh.seek(0)
                    self._head_fingerprint = fh.read(self._HEAD_BYTES)
        except FileNotFoundError:
            return []
        self._offset += len(chunk)
        data = self._buffer + chunk
        if b"\n" not in data:
            self._buffer = data
            if len(self._buffer) > MAX_BUFFER_BYTES:
                log.debug(
                    "dropping oversized line: %d bytes", len(self._buffer)
                )
                self._buffer = b""
            return []
        *lines, tail = data.split(b"\n")
        self._buffer = tail
        return [ln.decode("utf-8", errors="replace") for ln in lines]

    async def _deliver_appended(
        self, app: "HarnessVisualApp | None", bus: EventBus | None
    ) -> None:
        for ln in self._read_appended():
            for ev in parse_line(ln):
                await self._deliver(ev, app, bus)

    async def run(
        self,
        app: "HarnessVisualApp | None" = None,
        bus: EventBus | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        raise NotImplementedError


class PollingTailer(SessionWatcher):
    """Stdlib fallback: poll every 250ms."""

    def __init__(self, path: Path, interval: float = 0.25) -> None:
        super().__init__(path)
        self.interval = interval

    async def run(
        self,
        app: "HarnessVisualApp | None" = None,
        bus: EventBus | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        log.debug("PollingTailer starting on %s", self.path)
        # Prime with any existing content.
        await self._deliver_appended(app, bus)
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                return
            try:
                await self._deliver_appended(app, bus)
            except Exception as e:  # never crash watcher
                log.debug("PollingTailer deliver error: %s", e)


class WatchfilesTailer(SessionWatcher):
    """watchfiles-backed tailer; falls back to polling on import error."""

    async def run(
        self,
        app: "HarnessVisualApp | None" = None,
        bus: EventBus | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        try:
            from watchfiles import awatch  # type: ignore
        except ImportError:
            log.debug("watchfiles unavailable, using PollingTailer")
            fallback = PollingTailer(self.path)
            # Copy ALL tailer state so the fallback continues exactly where
            # this tailer left off.  Copying only _offset would cause the next
            # tick to see _inode=None, trigger a spurious rotation, and
            # re-deliver every already-processed line; omitting _buffer would
            # also lose any in-flight partial line.
            fallback._offset = self._offset
            fallback._inode = self._inode
            fallback._mtime_ns = self._mtime_ns
            fallback._buffer = self._buffer
            fallback._head_fingerprint = self._head_fingerprint
            await fallback.run(app=app, bus=bus, stop_event=stop_event)
            return

        # Prime initial content first.
        await self._deliver_appended(app, bus)
        try:
            async for _changes in awatch(str(self.path.parent), stop_event=stop_event):
                try:
                    await self._deliver_appended(app, bus)
                except Exception as e:
                    log.debug("WatchfilesTailer deliver error: %s", e)
        except asyncio.CancelledError:
            return


def make_tailer(path: Path) -> SessionWatcher:
    """Factory: respects HARNESS_VISUAL_BACKEND=polling env override."""
    if os.environ.get("HARNESS_VISUAL_BACKEND") == "polling":
        return PollingTailer(path)
    try:
        import watchfiles  # noqa: F401

        return WatchfilesTailer(path)
    except ImportError:
        return PollingTailer(path)
