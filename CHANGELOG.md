# Changelog

All notable changes to agentlens are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is roughly semver for a personal tool: MINOR bumps ship
user-visible behavior changes, PATCH bumps ship fixes only.

---

## [0.9.1] - 2026-04-15

Fix: flow mode P1 — parallel/sequential spawns from same turn now render as fork (main→{A,B,C}) instead of vertical chain (A→B→C). Temporal predecessor inference removed; all top-level agents are now ROOT children in flow mode. 273 tests passing.

### Fixed

- **Flow mode parallel spawn heuristic** — `_flow_subgraph()` no longer infers temporal precedence from completion times. All agents spawned from ROOT (main) in the same turn now connect directly as fork children instead of forming a vertical chain when the earliest spawn completes before the next starts.
  - Root cause: The heuristic had no `spawn_ts` reference and treated any completed predecessor as a sequential link. This broke the fork topology for parallel spawns.
  - Solution: All FlowRecords now connect to ROOT as direct children in flow mode (fork layout). Sequential spawns are still ordered by start time within the `_flow_history` list, preserving logical causality at a higher level.
- **4 regression tests rewritten** (v0.9.0 chain assertions → v0.9.1 fork assertions) + 4 new tests added for parallel/sequential spawn correctness. Test count: 269 → **273 passing**.

---

## [0.9.0] - 2026-04-14

Activity Sparkline in status footer. The footer now shows a rolling 8-bar
histogram of events/sec over the last 60 seconds using Unicode block
characters (▁▂▃▄▅▆▇█) plus a `peak: N/s` label. The sparkline is suppressed
automatically on narrow terminals and resets when switching sessions. 269
tests passing.

### Added

- **Activity Sparkline** in the status footer: a rolling 8-bar histogram of
  events/sec over the last 60 seconds, displayed using Unicode block
  characters (▁▂▃▄▅▆▇█). Includes a `peak: N/s` label showing the peak event
  rate (capped at `99+/s`). Auto-suppressed on narrow terminals to preserve
  layout. Resets when switching sessions.

---

## [Unreleased]

---

## [0.8.1] - 2026-04-14

Subagent token breakdown in Turn Summary, OMC team agent attribution fix,
and teammate-message turn suppression. Turn Summary now shows per-skill and
per-agent token consumption in a hierarchy. Team agents (native
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) now correctly appear by OMC name
in the flowchart instead of being unlinked. 235 tests passing.

### Added

- **Turn Summary modal — Token Usage section enhanced with subagent/skill hierarchy**
  - Skill-spawned subagents display under their parent skill with 4-space indentation
  - Skill span label format: `[skill] name` with sub-aggregate `agents` sub-list
  - Standalone agents (spawned outside skill context) display under a separate
    `agents (N)` section
  - Each agent attribution captured via `_agent_to_skill` snapshot map at spawn time,
    enabling async token attribution even when subagent messages arrive across turn
    boundaries
  - `Total` row calculated as leaf sum only (no double-counting): `token_main` +
    standalone agents + all skill sub-agents = `token_total`
- **`subagent_meta_link` event type** — new parser event that maps hex agent IDs to
  OMC names via `.meta.json` sidecars. `SubagentWatcherManager` emits this event on
  file discovery so team agent nodes are created with their `input.name` label.

### Changed

- **`TurnRecord` dataclass** — two new fields:
  - `token_skill_tree: dict` — skill node ID → skill label + total tokens + agents sub-list
  - `token_agents_standalone: dict` — standalone agent ID → agent label + tokens
- **`CallGraph` dataclass** — one new field:
  - `_agent_to_skill: dict[str, str]` — snapshot map of agent_node_id → skill_node_id,
    captured at spawn time. Session-scoped, unaffected by turn boundaries.
- **`get_turn_summary()` return dict** (additive):
  - Now includes `token_skill_tree` and `token_agents_standalone` alongside existing
    `token_total`, `token_main`, `token_nodes` (backward compatible).
  - Legacy fallback: if skill tree is empty, existing `token_nodes` list is returned
    (no change for existing panel code).
- **`_SYSTEM_USER_PREFIXES`** — `<teammate-message` prefix added, preventing team
  execution turns from splitting into spurious turn boundaries (265, 266, ...).

### Fixed

- **OMC team agent token attribution** — native team agents send `agent_id: name@team`
  in `tool_result` rather than a hex hash. The new `subagent_meta_link` / `.meta.json`
  pipeline resolves hex→name at file-discovery time so flowchart nodes display the
  agent's actual OMC name (`verifier-a`, `executor`, etc.) instead of being unlinked.
  Lazy resolve handles race conditions where the meta file arrives after the first event.
- **Subagent token attribution in hierarchical context** — tokens for agents spawned
  within skill spans now correctly route to the skill's sub-agent bucket instead of
  top-level standings, preventing skill context loss.
- **Teammate-message turns** — `<teammate-message` prefixed user rows no longer
  create spurious turn boundaries during OMC team execution.

---

## [0.8.0] - 2026-04-14

Turn Summary modal now shows Token Usage breakdown. Parser defensively
extracts LLM token consumption per message, accumulates six token-tracking
fields per turn, and gracefully hides the Token Usage section for legacy
sessions without usage data. 235 tests passing (+12).

### Added

- **Turn Summary modal — Token Usage section** shows LLM token consumption
  per turn, broken down across three views: total tokens for the turn,
  main-session tokens, and aggregated agent/skill subtokens. Visible only
  when token data is present; silently hidden for legacy sessions (v<0.8.0).
- **`_extract_usage()` defensive extractor** in `parser.py` reads
  `message.usage` dict fields (`input_tokens`, `output_tokens`,
  `cache_creation_input_tokens`, `cache_read_input_tokens`) from assistant
  rows. Row-level: one extraction per `assistant` row, attached to the
  first `assistant_message` event only. Graceful fallback to zero on missing
  or malformed fields (AC10, never-raise).
- **`TurnRecord` fields extended** — six token-tracking fields:
  `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens` (per-turn cumulative sums), `token_main`
  (main-session only), `token_nodes` (sum of agent/skill subtokens).
- **`get_turn_summary()` return dict extended** (additive, AC-11):
  - `token_total` — total tokens consumed across all subagents
  - `token_main` — tokens consumed by main-session assistant
  - `token_nodes` — sum of token consumption across all agent/skill spawns
- **Schema-tolerant usage extraction** — missing `message.usage` dict, null
  fields, and malformed JSON all degrade gracefully to zero. Incompatible
  row shapes do not emit exceptions or debug logs (v0.8.0 silent-drop mode).

### Fixed

- **Subagent token attribution** — each agent spawn or skill invocation
  opens a separate JSONL (subagent file) with its own usage tracking. The
  parser now correctly routes each subagent's token sum to `token_nodes`
  instead of double-counting against `token_main`.

### Tests

- Full suite: 223 → **235 passing** (+12). New tests cover token extraction
  with missing fields, cache fields, legacy-session graceful hiding,
  main/node attribution, and Turn Summary modal rendering with Token Usage
  section.

---

## [0.7.0] - 2026-04-14

Turn Summary modal now shows Tool Usage, MCP tools, and Hooks breakdown.
Parser gains support for `type:"system"` top-level events to collect hook
summary data. 223 tests passing (+22).

### Added

- **Turn Summary modal — three new conditional sections** (Tool Usage, MCP,
  Hooks) displayed after Agents/Skills. Shown only when data is present.
- **`EventType.hook_summary`** — new event type emitted by parser on
  `type:"system"` with `subtype:"stop_hook_summary"`.
- **`_parse_hook_infos()` and `_coerce_hook_errors()`** — defensive
  multi-format parsers for hook infos JSON (list / repr string / raw JSON)
  and hook error counts (int / list / bool / str), with empty fallback.
- **`MAX_HOOK_INFOS = 50`** — cap on raw hook summary snapshots per turn.
- **`TurnRecord` fields extended** — `tool_calls`, `mcp_calls`, `hook_events`,
  `hook_errors`, `hook_by_command`, `hook_infos_raw` for per-turn summaries.
  Overflow tracking via `tool_overflow_names`, `mcp_overflow_names`,
  `hook_overflow_names`.
- **`get_turn_summary()` return dict extended** (additive, AC-11):
  - `tool_usage: list[{name, count}]` — top-8 tools sorted by count desc, name asc
  - `mcp_usage: list[{server, tool, full_name, count}]` — MCP tools (mcp__server__short split on first `__` after prefix)
  - `hook_usage: list[{event, script, command, count, error_count, total_ms}]` — top-5 hooks sorted by count desc, total_ms desc (tiebreaker)
  - `hooks_configured: bool` — best-effort probe of `.claude/settings.json` (v1: False fixed, graceful fallback on error)
  - Totals: `tool_total`, `mcp_total`, `hook_total`, `hook_runs`, `hook_errors_total`, `hook_duration_ms`
  - Overflow counts: `tool_overflow`, `mcp_overflow`, `hook_overflow`
- **Timestamp-based turn attribution** — hook summary events routed to turns
  via `_find_turn_by_timestamp()` (reverse linear scan, -1 pre-first-turn).
- **MCP full-name preservation** — `is_mcp` flag + `mcp__server__tool` pattern
  with explicit server/tool split (spec §5.4).

### Fixed

- **Parser now routes `type:"system"` events** instead of dropping them as
  unknown. Supports `subtype:"stop_hook_summary"` with full payload; other
  subtypes (turn_duration, compactMetadata, retry) silently dropped as unknown
  (v1 no-op).

### Tests

- Full suite: 201 → **223 passing** (+22). New tests cover hook summary
  parsing, turn attribution, overflow caps, MCP split logic, and Turn Summary
  modal rendering with Tool Usage / MCP / Hooks sections.

---

## [0.6.0] - 2026-04-13

Background agent completion tracking, parallel fork/join accuracy, and
turn-based navigation. The Flowchart panel now correctly visualizes
parallel (background) agent spawns as forks and joins them to the
correct predecessor on completion. 196 tests passing.

### Added

- **Turn-based navigation** — `[` (previous), `]` (next), `\` (LIVE)
  keys filter both Flowchart and Timeline to a specific turn. All
  three modes (all/running/flow) respect the active turn filter.
  Border turns yellow when filtered; title shows "Turn N/M".
- **`queue-operation` / task-notification parsing** — the parser now
  extracts `tool_use_id`, `status`, and `duration_ms` from Claude
  Code's `queue-operation` (enqueue) JSONL rows. Background agents'
  FlowRecords are updated with their real completion time instead of
  the instant ack (~0.005s). This enables accurate fork/join
  detection for parallel agents.
- **Pending task-notification handling** — when a `queue-operation`
  row arrives before the corresponding `tool_use` (JSONL write-order
  race condition), the notification is stashed and applied
  retroactively when the FlowRecord is created.
- **Background agent grouping in fork detection** — FlowRecords now
  carry an `is_background` flag (from `run_in_background` input).
  Background agents only consider foreground completions as
  predecessors, so parallel background spawns share the same parent
  instead of chaining through each other.
- **`subagent_type` fallback** — Agent tool_use events without a
  `subagent_type` field now default to `"general-purpose"` instead
  of being silently dropped. Fixes flow mode not showing agents
  spawned without an explicit type.
- **`EventType.task_notification`** — new event type for background
  agent completion signals.
- **Flow LIVE shows current turn only** — `_active_turn == None`
  now renders only the current turn's flow records, not the entire
  session history.

### Fixed

- **Fork detection ignored instant acks** — tool_result events with
  duration < 0.5s (background agent "Async agent launched" acks)
  are excluded from the completed-predecessor list, preventing
  false linear chains where forks should appear.
- **Turn markers scrolled to top** — `[`/`]` keys now call
  `scroll_to_turn()` to jump the Timeline to the selected marker.
- **False turn markers from subagent prompts** — subagent initial
  prompt text that passed `_is_real_user_prompt` no longer creates
  spurious turn boundaries.
- **Timeline subagent event exclusion** — events with
  `subagent_uuid` are skipped in the Timeline for clean time
  ordering.

### Tests

- Full suite: 177 → **196 passing** (+19). New tests cover fork
  detection with instant acks, real completion chains, turn
  filtering, turn navigation keys, border color changes, and
  footer count accuracy.

---

## [0.5.0] - 2026-04-13

Flow mode, timeline start/end markers, and description-based labels.
The Flowchart panel now has three modes (all / running / flow) and
flow mode persists across turns for full session orchestration
visibility. 177 tests passing.

### Added

- **Flowchart `[flow]` mode** — third mode via `m` key cycle
  (`all → running → flow → all`). Each Agent/Skill invocation is
  an individual node (no dedup) connected by temporal edges in
  execution order. Parallel spawns fork from the same predecessor;
  sequential calls chain linearly. Shows the entire session's
  orchestration sequence as a DAG.
- **FlowRecord** — session-persistent record of each invocation.
  Unlike Instance (turn-scoped, cleared on flush), FlowRecord
  survives across user prompts for the entire session. Only cleared
  on session switch. Capped at MAX_NODES.
- **Parallel fork detection** in flow mode: each node connects to
  the most recently *completed* predecessor (latest `ended_ts <=
  started_ts`). Agents spawned before their predecessor finishes
  naturally fork from the same parent. Visible as multi-row
  branching in LR layout and multi-column branching in TD layout.
- **Description-based labels** in flow mode: each node displays the
  Agent call's `description` field ("Schema probe", "Critic round
  1 REJECT") instead of the generic type name. Falls back to type
  when no description is provided. Works in all environments
  (OMC, non-OMC, harness).
- **Timeline `▶`/`✓` start/end markers**: tool_use rows now show
  `▶ toolname` and tool_result events add a NEW completion row
  `✓ toolname` at the result's timestamp. Reveals temporal
  ordering of completions (who finished first) without needing
  flow mode.
- **Per-node selection in flow mode**: clicking a flow node
  highlights only that exact node (via `_selected_flow_vid`),
  not every node sharing the same base agent type.
- **Drill-down failure notifications**: `action_drill_down` now
  surfaces toast messages when selection fails (no agent selected,
  node not found, not an agent type) instead of silently no-opping.
- **Scroll-offset click fix**: flowchart node selection via mouse
  now accounts for `scroll_x`/`scroll_y` so clicking after
  scrolling hits the intended node.

### Fixed

- **Footer mode tag** showed "all" for flow mode due to a binary
  conditional that predated the three-mode cycle. Now uses the mode
  string directly.
- **Flowchart click coordinates** were viewport-relative but layout
  positions were canvas-absolute. Added scroll offset compensation
  so selection works after scrolling.

### Tests

- Full suite: 161 → 177 passing (+16). New test files:
  `tests/test_flow_mode.py` (16 tests covering mode cycle,
  subgraph topology, description labels, parallel fork, sequential
  join, history persistence, single-node selection).

---

## [0.4.0] - 2026-04-09

Windows / git-bash compatibility and UX escape hatches. Adds a
paste-a-path modal, session-id / prefix lookup, a cwd-field
fallback in the locator, and a tail -f style auto-follow on the
Timeline panel. 157 tests passing.

### Added

- **Timeline auto-follows the bottom row on new events.** When
  the DataTable cursor is on the last row (or the table is
  empty), incoming events pull the cursor — and therefore the
  viewport — to the new last row. Classic `tail -f` behavior.
  When the user has scrolled up to inspect an older event the
  cursor is no longer at the bottom, so the detection returns
  False and incoming events leave the viewport alone. Scrolling
  back to the last row resumes auto-follow. Bulk ingestion
  (tests, startup catch-up, bursty subagent output) is coalesced
  via `call_after_refresh` + a `_scroll_pending` guard so N
  add_events in one frame result in a single cursor move, not N.
- **Paste-a-path modal via `Shift+S`** (`panels/session_path_input.py`).
  Pushes a modal with a single Input field. Paste a JSONL path (or a
  bare session id / prefix), press Enter, and the app swaps the
  active session without restarting. Validation is inline: existing
  file, is-a-file, `.jsonl` suffix, otherwise a red error stays
  visible and the modal keeps focus. `Esc` cancels; submitting the
  currently-attached file is a no-op. Intended as an escape hatch
  when the regular `s` picker cannot surface the intended session —
  notably on Windows / git-bash where the slug convention may not
  match.
- **Session id / prefix lookup in the paste-path modal.** If the
  input contains no path separators and no `.jsonl` suffix it is
  treated as a bare session id. The modal globs
  `~/.claude/projects/*/<id>*.jsonl` across every project subdir.
  Exactly one match → attach. Multiple matches → inline count with
  "paste the full path instead". Zero matches → inline "not found".
  Full UUIDs and short prefixes both work as long as the prefix is
  unique, so users can paste `b0709256` instead of the full path.
- **Path-independent cwd-field fallback in `SessionLocator`.** When
  the slug-based lookup misses (no directory under
  `~/.claude/projects/<slug>/`), `find_candidates()` and
  `find_active()` now scan every project subdir, peek at the first
  row of each JSONL, and match on the recorded `cwd` field. New
  `chosen_reason` value: `"cwd-match"`. Robust to unknown / future
  Claude Code slug conventions — the `cwd` field is present on
  every session row, so matching on it is OS-independent and
  survives slug-naming changes. POSIX users still hit the slug
  fast path and pay zero overhead.
- `_norm()` helper in `locator.py` that normalizes path separators,
  strips trailing slashes, and case-folds Windows drive-letter
  paths (`C:\Users\limdk` ≡ `c:/users/limdk`). Used by the cwd
  fallback so Windows-style and POSIX-style paths compare equal.

### Fixed

- **`Shift+S` binding now fires in a real terminal.** The binding
  was registered as `"shift+s"` only, which matched
  `pilot.press("shift+s")` in tests but failed in a live TTY
  because terminals send the literal uppercase character `S` on
  Shift+s and Textual routes that to a binding named `"S"` — not
  `"shift+s"`. Registering both forms under the same action makes
  the key work everywhere. Covered by
  `test_uppercase_s_also_pushes_path_input_modal`.

### Changed

- `ChosenReason` Literal in `locator.py` extended with
  `"cwd-match"`, `"switched"`, and `"path-input"`. The first two
  were already in use at runtime but had been missing from the
  declared type; the third is new with the `Shift+S` modal.

### Tests

- Full suite: 133 → 157 passing (+24). New files:
  `tests/test_session_path_input.py` (17 tests covering validation,
  uppercase-S binding, session id lookup, and cancel paths) and
  expanded `tests/test_locator.py` (+7 Windows / cwd-match tests).

---

## [0.3.0] - 2026-04-09

Rename + mid-session switch + Definition-of-Done closeout. 133 tests
passing. The repository directory on disk is unchanged — the
distinction is purely at the installable identity layer.

### Added

- **Mid-session switch** via the new `s` key. Opens the session
  picker in-place, marks the currently-attached file with
  `✓ (current)`, and on a different pick stops the running
  watcher + subagent manager, clears Timeline and Flowchart
  state, and catches up the new JSONL from its first line.
  Picking the current file or cancelling with Esc is a no-op.
  Cross-project switching is out of scope — use `--project-root`
  or `--session` at launch for that. The OMC state reader keeps
  running across switches since `.omc/state/` is cwd-relative.
- **M-AC8-idle automated coverage** (`tests/test_idle_footer.py`,
  4 tests). The "footer shows `— session idle` after >30s"
  acceptance criterion — originally listed as a manual procedure
  in the spec's Definition of Done — is now exercised in the
  test suite by monkeypatching `time.monotonic`. Positive case,
  sub-30s negative case, fresh-session edge, and the boundary at
  30.000 vs 30.001 seconds are all covered.
- **M-AC11 measurement recorded in README**. Idle CPU measured
  via `pty.fork` + `ps -o pcpu=` sampling over a 10-second
  window: **0.16 % average**, **0.30 % peak**, **40.7 MB RSS** —
  well under the ≤ 2 % target. Documented in `README.md` ->
  `## Manual Verification`.

### Changed

- Renamed Python package, CLI entry point, and environment
  variable from harness-visual / HARNESS_VISUAL_BACKEND to
  agentlens / AGENTLENS_BACKEND. The repository directory on
  disk is unchanged — only the installable package name moved.
- Renamed `HarnessVisualApp` → `AgentlensApp`. Domain vocabulary
  classes (`HarnessEvent`, `HarnessEventMessage`) kept — they
  describe "events from a session harness" and are independent
  of the installable identity.

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
