"""Textual Message wrapper so the watcher can call app.post_message()."""

from __future__ import annotations

from dataclasses import dataclass

from textual.message import Message

from .events import HarnessEvent


@dataclass
class HarnessEventMessage(Message):
    """Wraps a HarnessEvent for delivery into the Textual event loop."""

    event: HarnessEvent

    def __init__(self, event: HarnessEvent) -> None:
        self.event = event
        super().__init__()
