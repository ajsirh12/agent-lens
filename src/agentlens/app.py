"""AgentlensApp — Textual App wiring panels + watchers."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Static
from textual.worker import Worker

from .locator import SessionLocator
from .messages import HarnessEventMessage
from .omc_state import OmcStateReader
from .panels.detail_modal import ToolDetailScreen
from .panels.flowchart import FlowchartPanel
from .panels.session_path_input import SessionPathInputScreen
from .panels.session_picker import SessionPickerScreen
from .panels.subagent_detail import SubagentDetailScreen
from .panels.timeline import TimelinePanel
from .parser import parse_line
from .subagent_locator import SubagentLocator
from .subagent_watcher import SubagentWatcherManager
from .watcher import SessionWatcher, make_tailer

log = logging.getLogger(__name__)


class AgentlensApp(App[int]):
    CSS_PATH = "app.tcss"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("up", "cursor_up", "Up"),
        ("enter", "show_detail", "Detail"),
        ("d", "drill_down", "Subagent detail"),
        ("m", "toggle_mode", "Mode: All/Running"),
        ("o", "toggle_orientation", "Orient: TD/LR"),
        ("p", "toggle_pane_layout", "Panes: H/V"),
        ("shift+h", "flowchart_scroll_left", "Flow ←"),
        ("shift+l", "flowchart_scroll_right", "Flow →"),
        ("pageup", "flowchart_scroll_up", "Flow ↑"),
        ("pagedown", "flowchart_scroll_down", "Flow ↓"),
        ("home", "flowchart_scroll_home", "Flow ⇱"),
        ("end", "flowchart_scroll_end", "Flow ⇲"),
        ("s", "switch_session", "Switch session"),
        # Bind BOTH forms: Textual's pilot.press("shift+s") synthesizes
        # the modifier-prefixed key directly, while a real terminal just
        # sends the uppercase character 'S' on Shift+s. Registering both
        # names makes the binding work in tests AND in a live TTY.
        ("shift+s", "open_session_path", "Open session by path"),
        ("S", "open_session_path", "Open session by path"),
    ]

    selected_agent_id: reactive[str | None] = reactive(None)
    selected_event_id: reactive[str | None] = reactive(None)
    last_event_monotonic: reactive[float] = reactive(0.0)

    def __init__(
        self,
        *,
        session_override: Path | None = None,
        project_root: Path | None = None,
        state_dir_override: Path | None = None,
        self_test: bool = False,
        no_attach: bool = False,
        auto_latest: bool = False,
    ) -> None:
        super().__init__()
        self.session_override = session_override
        self.project_root = project_root
        self.state_dir = state_dir_override or Path.cwd() / ".omc" / "state"
        self.self_test = self_test
        self.no_attach = no_attach
        self.auto_latest = auto_latest
        self.active_session_path: Path | None = None
        self.locator_reason: str = "none"
        self._timeline: TimelinePanel | None = None
        self._flowchart: FlowchartPanel | None = None
        self._footer: Static | None = None
        self._watcher: SessionWatcher | None = None
        self._omc_reader: OmcStateReader | None = None
        self._subagent_manager: SubagentWatcherManager | None = None
        self._watcher_worker: Worker | None = None
        self._subagent_worker: Worker | None = None

    def compose(self) -> ComposeResult:
        with Container(id="main"):
            self._timeline = TimelinePanel(id="timeline")
            yield self._timeline
            self._flowchart = FlowchartPanel(id="flowchart")
            yield self._flowchart
        self._footer = Static("agentlens starting…", id="status-footer")
        yield self._footer

    async def on_mount(self) -> None:
        # Resolve session path.
        if not self.no_attach:
            if self.session_override is not None:
                self.active_session_path = self.session_override
                self.locator_reason = "override"
            else:
                locator = SessionLocator(
                    cwd=self.project_root or Path.cwd(),
                    projects_root=Path.home() / ".claude" / "projects",
                )
                candidates = locator.find_candidates()
                if len(candidates) >= 2 and not self.auto_latest and not self.self_test:
                    # Defer attachment until the user picks. push_screen with a
                    # callback avoids needing worker context.
                    def _on_picked(chosen: Path | None) -> None:
                        if chosen is not None:
                            self.active_session_path = chosen
                            self.locator_reason = "picker"
                        else:
                            self.active_session_path = candidates[0]
                            self.locator_reason = "slug"
                        self._finalize_attach()

                    self.push_screen(
                        SessionPickerScreen(candidates), _on_picked
                    )
                    return
                elif len(candidates) == 1:
                    self.active_session_path = candidates[0]
                    self.locator_reason = "slug"
                else:
                    # 0 candidates → use existing find_active() fallback path.
                    self.active_session_path = locator.find_active()
                    self.locator_reason = locator.chosen_reason

        self._finalize_attach()

    def _finalize_attach(self) -> None:
        """Start watcher + omc reader once a session has been resolved."""
        self._update_footer()

        if self.self_test:
            self.set_timer(0.1, self.exit)
            return

        if self.active_session_path is not None:
            self._start_session_workers(self.active_session_path)

        if self._omc_reader is None:
            self._omc_reader = OmcStateReader(self.state_dir)
            self.run_worker(
                self._omc_reader.run(app=self, bus=None),
                exclusive=False,
                name="omc_state",
            )

            self.set_interval(1.0, self._refresh_idle_footer)

    def _start_session_workers(self, path: Path) -> None:
        """Spawn the main watcher + subagent manager for ``path``.

        Idempotent when existing handles are present (won't double-spawn).
        """
        if self._watcher is None:
            self._watcher = make_tailer(path)
            self._watcher_worker = self.run_worker(
                self._watcher.run(app=self, bus=None),
                exclusive=False,
                name="watcher",
            )
        if not self.no_attach and self._subagent_manager is None:
            self._subagent_manager = SubagentWatcherManager(
                main_session_path=path
            )
            self._subagent_worker = self.run_worker(
                self._subagent_manager.run(app=self),
                exclusive=False,
                name="subagent_manager",
            )

    def _stop_session_workers(self) -> None:
        """Cancel in-flight watcher + subagent workers so a fresh pair
        can be started for a different session path.
        """
        if self._watcher_worker is not None:
            try:
                self._watcher_worker.cancel()
            except Exception:
                pass
            self._watcher_worker = None
        if self._subagent_worker is not None:
            try:
                self._subagent_worker.cancel()
            except Exception:
                pass
            self._subagent_worker = None
        self._watcher = None
        self._subagent_manager = None

    def _flowchart_counts_suffix(self) -> str:
        if self._flowchart is None:
            return ""
        try:
            n = self._flowchart.get_node_count()
            e = self._flowchart.get_edge_count()
            mode = self._flowchart.get_mode()
            orient = self._flowchart.get_orientation()
        except Exception:
            return ""
        orient_tag = "LR" if orient == "leftright" else "TD"
        mode_tag = "running" if mode == "running" else "all"
        # Pane layout: V if the main container has the vpanes class,
        # H otherwise (default horizontal layout).
        pane_tag = "H"
        try:
            main = self.query_one("#main")
            if "vpanes" in main.classes:
                pane_tag = "V"
        except Exception:
            pass
        return f"  nodes: {n} edges: {e}  [{mode_tag}/{orient_tag}/{pane_tag}]"

    def _short_session_path(self) -> str:
        """Compact form of active_session_path that fits narrow terminals.

        Returns just the filename of the active session, since the
        parent directory (the slugged project dir under ~/.claude/projects)
        is always the same for a given project and adds no information
        the user can't derive from their cwd. A typical Claude Code
        session file ``b0709256-....jsonl`` is ~41 chars.
        """
        if self.active_session_path is None:
            return "(none)"
        return self.active_session_path.name

    def _update_footer(self) -> None:
        if self._footer is None:
            return
        path = self._short_session_path()
        self._footer.update(
            f"session: {path} [{self.locator_reason}]{self._flowchart_counts_suffix()}"
        )

    def _refresh_idle_footer(self) -> None:
        if self._footer is None or self.active_session_path is None:
            return
        now = time.monotonic()
        last = float(self.last_event_monotonic)
        idle_suffix = ""
        if last > 0 and (now - last) > 30:
            idle_suffix = "  — session idle"
        path = self._short_session_path()
        self._footer.update(
            f"session: {path} [{self.locator_reason}]{idle_suffix}"
            f"{self._flowchart_counts_suffix()}"
        )

    # --- message routing -------------------------------------------------

    def on_harness_event_message(self, message: HarnessEventMessage) -> None:
        """Root handler fans event out to both panels."""
        self.last_event_monotonic = time.monotonic()
        if self._timeline is not None:
            self._timeline.add_event(message.event)
        if self._flowchart is not None:
            self._flowchart.add_event(message.event)
        self._update_footer()

    # --- actions ---------------------------------------------------------

    def action_cursor_down(self) -> None:
        if self._timeline is not None:
            self._timeline.move_cursor("down")

    def action_cursor_up(self) -> None:
        if self._timeline is not None:
            self._timeline.move_cursor("up")

    def action_show_detail(self) -> None:
        if self._timeline is None:
            return
        cells = self._timeline.get_selected_row_cells()
        if cells is None:
            return
        # cells: ts, tool, agent, status, dur_ms
        self.push_screen(
            ToolDetailScreen(
                tool_name=cells[1],
                input_summary=self._timeline.get_selected_input_summary(),
                status=cells[3],
                duration_ms=cells[4],
            )
        )

    def action_drill_down(self) -> None:
        """Open the SubagentDetailScreen for the currently selected
        agent node, loading its linked subagent JSONL file on demand.
        """
        if self._flowchart is None:
            return
        nid = self.selected_agent_id
        if not nid:
            return
        graph = self._flowchart._graph
        node = graph.nodes.get(nid)
        if node is None or node.node_type != "agent":
            return
        # Prefer the specific instance the user clicked. Fall back to the
        # node-level link if no virtual node was selected (single-spawn
        # case or cross-highlight came from the timeline panel).
        sub_uuid: str | None = None
        instance_label_suffix = ""
        tid = self._flowchart._selected_tool_use_id
        if tid and tid in node._instances:
            inst = node._instances[tid]
            sub_uuid = inst.subagent_uuid
            total = len(node._instances)
            if total > 1:
                try:
                    idx = list(node._instances.keys()).index(tid) + 1
                    instance_label_suffix = f" (instance {idx} of {total})"
                except ValueError:
                    pass
        if sub_uuid is None:
            sub_uuid = node.subagent_uuid

        events: list[dict[str, Any]] = []
        if sub_uuid and self.active_session_path is not None:
            locator = SubagentLocator(main_session_path=self.active_session_path)
            target = None
            for p in locator.list_files():
                if SubagentLocator.agent_id_from_filename(p) == sub_uuid:
                    target = p
                    break
            if target is not None:
                events = self._load_subagent_events(target)
        self.push_screen(
            SubagentDetailScreen(
                node_label=node.label + instance_label_suffix,
                events=events,
            )
        )

    def _load_subagent_events(self, path: Path) -> list[dict[str, Any]]:
        """Parse a subagent JSONL file into a list of tool_use event
        dicts suitable for SubagentDetailScreen. Caps at 500 most recent.
        Returns [] on any failure.
        """
        out: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except (OSError, FileNotFoundError):
            return out
        # tool_use_id -> index in out, so we can flip status on result.
        pending: dict[str, int] = {}
        for ln in lines:
            try:
                parsed = parse_line(ln)
            except Exception:
                continue
            for ev in parsed:
                if ev.type.value == "tool_use":
                    tool_name = ev.tool_name
                    if not tool_name:
                        continue
                    inp = ev.payload.get("input")
                    summary = ""
                    if isinstance(inp, dict):
                        # Prefer a common descriptive field.
                        for key in ("command", "pattern", "file_path", "path", "prompt", "description"):
                            v = inp.get(key)
                            if isinstance(v, str) and v:
                                summary = v
                                break
                        if not summary:
                            summary = str(inp)
                    elif inp is not None:
                        summary = str(inp)
                    out.append(
                        {
                            "ts": ev.ts,
                            "tool_name": tool_name,
                            "input_summary": summary[:80],
                            "status": "running",
                        }
                    )
                    tid = ev.tool_use_id
                    if tid:
                        pending[tid] = len(out) - 1
                elif ev.type.value == "tool_result":
                    tid = ev.tool_use_id
                    if tid and tid in pending:
                        idx = pending.pop(tid)
                        if 0 <= idx < len(out):
                            out[idx]["status"] = "error" if ev.is_error else "done"
        if len(out) > 500:
            out = out[-500:]
        return out

    def action_toggle_mode(self) -> None:
        if self._flowchart is not None:
            try:
                self._flowchart.toggle_mode()
            except Exception:
                pass
            self._update_footer()

    def action_toggle_orientation(self) -> None:
        if self._flowchart is not None:
            try:
                self._flowchart.toggle_orientation()
            except Exception:
                pass
            self._update_footer()

    def action_switch_session(self) -> None:
        """Open the picker on the same slug dir and, on selection, swap
        the active session without restarting the app.
        """
        locator = SessionLocator(
            cwd=self.project_root or Path.cwd(),
            projects_root=Path.home() / ".claude" / "projects",
        )
        candidates = locator.find_candidates()
        if not candidates:
            return

        def _on_picked(chosen: Path | None) -> None:
            if chosen is None:
                return
            if chosen == self.active_session_path:
                return
            self._stop_session_workers()
            self.active_session_path = chosen
            self.locator_reason = "switched"
            if self._timeline is not None:
                self._timeline.clear()
            if self._flowchart is not None:
                self._flowchart.clear()
            self.last_event_monotonic = 0.0
            self.selected_agent_id = None
            self._start_session_workers(chosen)
            self._update_footer()

        self.push_screen(
            SessionPickerScreen(
                candidates, current_path=self.active_session_path
            ),
            _on_picked,
        )

    def action_open_session_path(self) -> None:
        """Open a modal where the user pastes a JSONL path and, on
        submit, swap the active session without restarting the app.
        Provides an escape hatch when the normal slug-based picker
        cannot find the intended session (e.g. Windows path mismatch).
        """

        def _on_picked(chosen: Path | None) -> None:
            if chosen is None:
                return
            if chosen == self.active_session_path:
                return
            self._stop_session_workers()
            self.active_session_path = chosen
            self.locator_reason = "path-input"
            if self._timeline is not None:
                self._timeline.clear()
            if self._flowchart is not None:
                self._flowchart.clear()
            self.last_event_monotonic = 0.0
            self.selected_agent_id = None
            self._start_session_workers(chosen)
            self._update_footer()

        self.push_screen(SessionPathInputScreen(), _on_picked)

    def action_toggle_pane_layout(self) -> None:
        """Swap the Timeline/Flowchart arrangement between side-by-side
        (horizontal) and stacked (vertical). Applied via a CSS class on
        ``#main`` so the CSS file stays the source of truth for sizing.
        """
        try:
            main = self.query_one("#main")
        except Exception:
            return
        main.toggle_class("vpanes")
        self._update_footer()

    # --- flowchart scroll actions ---------------------------------------

    def _scroll_flowchart(self, method_name: str) -> None:
        if self._flowchart is None:
            return
        method = getattr(self._flowchart, method_name, None)
        if method is None:
            return
        try:
            method(animate=False)
        except TypeError:
            # Some scroll methods don't take animate kwarg.
            try:
                method()
            except Exception:
                pass
        except Exception:
            pass

    def action_flowchart_scroll_left(self) -> None:
        self._scroll_flowchart("scroll_left")

    def action_flowchart_scroll_right(self) -> None:
        self._scroll_flowchart("scroll_right")

    def action_flowchart_scroll_up(self) -> None:
        self._scroll_flowchart("scroll_page_up")

    def action_flowchart_scroll_down(self) -> None:
        self._scroll_flowchart("scroll_page_down")

    def action_flowchart_scroll_home(self) -> None:
        self._scroll_flowchart("scroll_home")

    def action_flowchart_scroll_end(self) -> None:
        self._scroll_flowchart("scroll_end")


def main() -> None:
    from .cli import main as _cli_main

    raise SystemExit(_cli_main())
