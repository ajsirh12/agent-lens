"""AgentTreePanel — Tree view of sub-agents."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static, Tree
from textual.widgets.tree import TreeNode

from ..events import EventType, HarnessEvent
from ..messages import HarnessEventMessage


class AgentTreePanel(Container):
    DEFAULT_CSS = ""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._tree: Tree[dict[str, Any]] | None = None
        self._placeholder: Static | None = None
        self._agent_nodes: dict[str, TreeNode[dict[str, Any]]] = {}
        self._updating = False

    def compose(self) -> ComposeResult:
        self._placeholder = Static("no agents yet…", classes="placeholder")
        yield self._placeholder
        tree: Tree[dict[str, Any]] = Tree("agents", id="agent-tree")
        tree.root.expand()
        self._tree = tree
        yield tree

    def on_mount(self) -> None:
        try:
            self.watch(self.app, "selected_agent_id", self._on_app_agent_changed)
        except Exception:
            pass

    def on_harness_event_message(self, message: HarnessEventMessage) -> None:
        ev = message.event
        self.add_event(ev)

    def add_event(self, ev: HarnessEvent) -> None:
        if self._tree is None:
            return
        if ev.type == EventType.agent_spawn:
            aid = ev.agent_id or "?"
            if aid in self._agent_nodes:
                return
            parent_id = ev.payload.get("parent_id")
            parent_node = (
                self._agent_nodes.get(parent_id) if parent_id else self._tree.root
            )
            if parent_node is None:
                parent_node = self._tree.root
            label = str(ev.payload.get("label") or aid)
            status = str(ev.payload.get("status") or "unknown")
            node = parent_node.add(
                f"{label} [{status}]", data={"agent_id": aid, "status": status}
            )
            self._agent_nodes[aid] = node
            self._hide_placeholder()
        elif ev.type == EventType.agent_status:
            aid = ev.agent_id or ""
            if aid in self._agent_nodes:
                node = self._agent_nodes[aid]
                new_status = str(ev.payload.get("status") or "unknown")
                data = node.data or {}
                label = data.get("label") or aid
                node.set_label(f"{label} [{new_status}]")
                if node.data is not None:
                    node.data["status"] = new_status
        elif ev.type == EventType.tool_use and ev.agent_id:
            # Auto-add main-thread agent node so we always have something to
            # cross-highlight even without a spawn event.
            if ev.agent_id not in self._agent_nodes:
                assert self._tree is not None
                label = ev.agent_id[:8] if len(ev.agent_id) > 8 else ev.agent_id
                node = self._tree.root.add(
                    f"{label}", data={"agent_id": ev.agent_id}
                )
                self._agent_nodes[ev.agent_id] = node
                self._hide_placeholder()

    def _hide_placeholder(self) -> None:
        if self._placeholder is not None and self._placeholder.display:
            self._placeholder.display = False

    def on_tree_node_highlighted(self, event: Any) -> None:
        if self._updating or self._tree is None:
            return
        node = getattr(event, "node", None)
        if node is None or node.data is None:
            return
        aid = node.data.get("agent_id")
        if not aid:
            return
        try:
            self.app.selected_agent_id = aid  # type: ignore[attr-defined]
        except Exception:
            pass

    def _on_app_agent_changed(self, new_value: str | None) -> None:
        if self._updating or self._tree is None or new_value is None:
            return
        node = self._agent_nodes.get(new_value)
        if node is None:
            return
        self._updating = True
        try:
            self._tree.select_node(node)
        except Exception:
            pass
        finally:
            self._updating = False
