# Changelog

All notable changes to harness-visual are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is roughly semver for a personal tool: MINOR bumps ship
user-visible behavior changes, PATCH bumps ship fixes only.

---

## [0.2.0] - 2026-04-09

Feature-complete release. Adds nested subagent trees, per-instance
visualization, drill-down, three rendering toggles, security fixes,
and a docs refresh. 123 tests, clean working tree.

### Added

- **Nested subagent tree (up to depth 5).** When a subagent spawns
  another agent or skill, the nested spawn is rendered as a child
  node under its true parent instead of collapsing onto `main`.
  Depth-capped at 5 to bound node count.
- **Mode-dependent instance view.** In `[running/*]` mode, parallel
  spawns of the same agent type render as distinct virtual nodes
  with unique ids like `agent:executor#<tid suffix>`. `[all/*]`
  mode keeps the compact aggregated view with a `(xN)` counter.
- **Per-instance tool_breakdown.** Each parallel instance tracks
  its own tool counts, so the running-mode badges accurately
  reflect what each instance actually did (e.g. one box shows
  `Rd3`, the sibling shows `Bs3`). The node-level aggregate
  persists across flushes for the all-mode session view.
- **Per-instance drill-down.** `d` on a clicked virtual instance
  opens the subagent file for THAT specific instance, not the
  node-level latest. Modal title gains `(instance N of M)` for
  disambiguation. Graceful fallback to node-level if no virtual
  was clicked or the turn was flushed.
- **Three rendering toggles.**
  - `m` — Mode: all ↔ running (+ instance expansion)
  - `o` — Orientation: top-down ↔ left-right
  - `p` — Panes: horizontal ↔ vertical (Timeline/Flowchart layout)
- **Sticky running.** Agents that complete during a turn stay
  visually green until the next real user prompt. A filter on
  `user_message` events skips system-injected text
  (`<task-notification>`, `<system-reminder>`, skill preambles,
  `isMeta=True` rows, subagent file user rows) so the flush only
  fires on actual user input.
- **Session picker.** When multiple JSONL files exist in the slug
  directory, a modal lets you choose by mtime/size/filename.
  `--latest` skips it and takes the newest.
- **Subagent watcher** (`SubagentWatcherManager`) periodically
  scans `{main_session}/subagents/` and attaches per-file
  `PollingTailer` tasks to stream events with `subagent_uuid`
  stamps. New files are picked up within a second of creation.
- **Flowchart scroll.** Mouse wheel + Shift+H/L, PgUp/PgDn,
  Home/End. `ScrollableContainer` base handles overflow.
- **Footer auto-wrap.** `#status-footer` now has `height: auto`
  and `max-height: 3`, and the session path is compacted to just
  the filename, so narrow terminals still show the full state.
- **Defensive input caps** against adversarial / malformed JSONL:
  - `MAX_RAW_LINE = 8192` on `HarnessEvent.raw_line`
  - `MAX_BUFFER_BYTES = 1_048_576` on the watcher's in-flight
    unterminated-line buffer (drops + debug-logs oversized lines)
  - `MAX_NODES = 500` on graph node count
  - `MAX_NESTED_DEPTH = 5` on nested spawn depth
  - `MAX_BREAKDOWN_TOOLS = 20` per node and per instance
  - `MAX_PENDING = 2000` on Timeline's pending-dict maps
- **ANSI escape sanitization.** Timeline rows and detail modal
  fields now strip `\x1b`, `\r`, and non-printable characters
  before rendering, so malicious session content can't corrupt
  the terminal layout.
- **CHANGELOG.md and expanded USAGE.md** covering every key
  binding, mode, and architectural component.

### Changed

- `panels/flowchart.py` replaces the earlier `AgentTreePanel` as
  the right-hand panel. The legacy tree view is removed.
- Virtual instance node ids use `tid[-8:]` as the suffix instead
  of `tid[:6]` — Claude Code tool_use_ids share the `toolu_`
  prefix so slicing from the front caused dict-key collisions.
- Clicked virtual instance highlight: only the exact clicked box
  now renders as `bold reverse` when `_selected_tool_use_id` is
  set. Timeline-driven selection (no tid recorded) still
  cross-highlights every sibling of the same base id.
- CLI exit code: `cli.main()` now propagates the int returned by
  `HarnessVisualApp.run()` instead of always returning 0.
- Display labels strip `oh-my-claudecode:` and `omc:` prefixes so
  `planner`, `executor`, `code-reviewer`, etc. fit inside the
  14-char node boxes. Full names are preserved in node ids.
- BFS depth computation lives exclusively in
  `CallGraph.compute_depths()` now, with `collections.deque`.
  `flowchart_layout.py` just calls it.
- `Horizontal` container in `app.py` → plain `Container` so
  CSS-driven `layout: vertical` can actually override when the
  `vpanes` class is toggled.

### Fixed

- **Watcher rotation fingerprint stale.** `PollingTailer` now
  clears `self._head_fingerprint` at every rotation reset site
  (inode change, size shrink, fingerprint mismatch) so the next
  read captures a fresh fingerprint instead of re-firing rotation.
- **WatchfilesTailer → PollingTailer fallback** now copies all
  five relevant fields (`_offset`, `_inode`, `_mtime_ns`,
  `_buffer`, `_head_fingerprint`) instead of just `_offset`, so
  mid-session fallback can no longer replay already-processed
  lines as duplicates.
- **Timeline `_pending_use` / `_tool_use_row` / `_row_input`
  leak.** All three now cap at 2000 entries with FIFO eviction
  of the oldest entry. Long sessions with orphaned tool_use
  events can no longer OOM.
- **`ToolDetailScreen.input_summary` always empty.** Timeline
  now stores an input preview per row and exposes it via
  `get_selected_input_summary()`, so the modal's "Input:" line
  is finally non-empty.
- **Private `_timeline._table` access from `app.py`.** Replaced
  with three public methods on `TimelinePanel`
  (`move_cursor`, `get_selected_row_cells`,
  `get_selected_input_summary`), removing a coupling that would
  break if the panel's internal widget layout changed.
- **ChosenReason Literal** in `locator.py` was missing the
  `"override"` and `"picker"` values the code was already
  assigning. Extended so mypy stops lying.
- **`omc_state.py` dead `sessions/` probe.** Removed the
  no-op iteration block left over from earlier scaffolding.
- **Orphan `panels/agent_tree.py`** deleted — nothing imported
  it since FlowchartPanel replaced it.

### Removed

- `panels/agent_tree.py` (~140 lines)
- `flowchart_layout._bfs_depths` private helper (deduped into
  `CallGraph.compute_depths`)
- `omc_state.py` dead sessions probe block

### Tests

- Grew from ~91 to **123 tests** across the release.
- New coverage: nested spawn, depth cap, label sanitization,
  instance lifecycle, mode/orientation/pane toggles, scroll
  actions, drill-down routing, instance-specific highlight,
  sticky running filters, watcher rotation + buffer caps,
  raw_line truncation, CLI exit codes, ANSI sanitization,
  pending-dict eviction.

---

## [0.1.0] - 2026-04-08

Initial feature-complete release of the TUI after the
deep-interview → ralplan → autopilot pipeline. Provides a live-tail
Timeline + AgentTree dual-pane view of Claude Code sessions.

### Added

- `parser.py` — schema-tolerant JSONL parser emitting normalized
  `HarnessEvent` instances, with graceful fallbacks on every
  unknown or malformed row.
- `watcher.py` — `PollingTailer` + `WatchfilesTailer` with
  3-way rotation detection (inode, size shrink, head fingerprint).
- `locator.py` — `SessionLocator` with slug-first then
  newest-mtime fallback across all projects, and a
  `find_candidates` helper for the picker.
- `graph_model.py` — `CallGraph` + `Node` + `Edge` + status
  transitions + duplicate dedup + sanitization.
- `flowchart_layout.py` — Sugiyama-ish BFS layout with
  `layout_topdown` and `layout_leftright` variants.
- Textual UI (`app.py` + `panels/`) wiring it all together:
  Timeline DataTable, AgentTree Tree, session picker modal,
  tool detail modal, cross-highlight, live-tail latency under
  one second.
- Comprehensive test suite built during the pipeline:
  `test_parser.py`, `test_replay_real_slice.py`,
  `test_locator.py`, `test_watcher.py`, `test_omc_state.py`,
  `test_smoke.py`, `test_cross_highlight.py`,
  `test_flowchart_layout.py`, `test_graph_model.py`,
  `test_session_picker.py`, `test_flowchart_panel.py`,
  `test_responsiveness.py`, and more.
- `docs/USAGE.md`, `docs/jsonl-schema-observed.md`,
  `scripts/fake_session.py`.
