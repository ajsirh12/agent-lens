# agentlens

[한국어](docs/README.ko.md)

Live-tail TUI for Claude Code sessions. Shows a Timeline of tool calls
alongside a real-time Flowchart of agent and skill spawns, including
nested subagent trees and parallel-instance views.

```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│ Timeline                             │ Flowchart                            │
│ ───────────────────────────────────  │ ──────────────────────────────────   │
│ ts        Turn  prompt    tools  dur │        ┌──────┐                      │
│ 14:02:01  1     "Fix b…"  ✓  8  1.2s│        │ main │                      │
│ 14:02:30  2     "Add f…"  ✓ 12  4.7s│        └───┬──┘                      │
│ 14:08:55  3     "Now r…"  ▶  3    - │            │                         │
│ ...                                  │   ┌────────┼──────────┐              │
│                                      │   ▼        ▼          ▼              │
│                                      │ ┌─────┐ ┌──────┐  ┌────────┐         │
│                                      │ │plan │ │ exec │  │ critic │         │
│                                      │ │(x3) │ │[Rd4] │  │        │         │
│                                      │ │Rd12 │ └──────┘  └────────┘         │
│                                      │ └─────┘                              │
├──────────────────────────────────────┴──────────────────────────────────────┤
│ session: b0709256-...jsonl [slug]  nodes: 5 edges: 4  [all/LR/H]           │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Features

- **Live tail** of the main Claude Code session JSONL via `watchfiles`
  (with stdlib polling fallback) — new events appear within ~1 second.
- **Timeline panel**: scrollable DataTable of turn markers (ts /
  Turn N / prompt preview / tool count / duration). Press Enter on
  any row to open TurnSummaryScreen with the individual tool call
  breakdown for that turn.
- **Flowchart panel**: live directed graph of Agent/Task/Skill calls,
  with parent/child edges, (xN) duplicate counters, per-subagent
  tool breakdown badges (e.g. `Rd12 Ed5`), and color-coded status
  (running / done / error).
- **True nested subagent tree** up to depth 5: if a subagent spawns
  another subagent via Skill, the nested spawn shows up as a child
  node in the flowchart instead of collapsing onto `main`.
- **Mode-dependent instance view**: in `[running]` mode, parallel
  spawns of the same agent type render as distinct boxes with
  per-instance tool counts; in `[all]` mode they aggregate into a
  single box with a `(xN)` counter and summed breakdown.
- **Sticky running**: a node stays visually green until the next real
  user prompt, so fast agents don't flicker into "done" before you
  notice them. Background task notifications, hook reminders, and
  subagents' own user rows are filtered out of the flush logic.
- **Per-instance drill-down** (`d` key): open a modal listing the
  specific tool history of the clicked parallel instance — each
  instance opens its own subagent JSONL file.
- **Three orthogonal toggles**:
  - `m` — mode (all ↔ running only)
  - `o` — orientation (top-down ↔ left-right)
  - `p` — panes (horizontal ↔ vertical)
- **Scrollable flowchart** with mouse wheel + keyboard (PgUp/PgDn,
  Shift+H/L, Home/End).
- **Session picker** at launch when multiple JSONLs exist in the
  same slug directory. `--latest` bypasses it. Press `s` during
  runtime to switch to a different session in the same directory
  without restarting — Timeline and Flowchart rebuild for the new
  session automatically.
- **Paste-a-path escape hatch via `Shift+S`.** Opens an Input
  modal that accepts either a full JSONL path or a bare session
  id / prefix (`b0709256`). Glob-resolves the id across every
  project subdir and attaches the match, so you can recover the
  right session even when the slug-based picker fails (e.g. on
  Windows / git-bash where the path format differs).
- **Windows / git-bash compatibility**: the locator falls back to
  matching each JSONL's recorded `cwd` field when the slug
  directory lookup misses (backslash paths, MSYS2 `/c/…` paths,
  case differences — all normalised automatically). Sessions still
  resolve on Windows even when the slug-based picker fails. See the
  [Windows notes](#windows--git-bash) section below.
- **Subagent watcher** automatically discovers and tails new
  `agent-*.jsonl` files as they're created under the session's
  `subagents/` directory. Team agents spawned via
  `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` are resolved by OMC name
  (e.g. `verifier-a`, `executor`) from `.meta.json` sidecars.
- **Turn Summary — Token Usage breakdown** (v0.8.1): Turn Summary modal
  shows per-skill and per-agent token consumption in a hierarchy. Skill
  spans list their sub-agents with 4-space indentation; standalone agents
  appear under a separate `agents (N)` section. Double-counting is
  prevented — `Total` is the leaf sum only.
- **TurnSummaryScreen fixed header** (v0.9.8): the Turn / Prompt /
  Duration·Agents·Skills·Errors / token summary line (`Tokens: Xk in /
  Yk out / ...`) are pinned to the top of the modal. The body is split
  into four independent scroll sections: **Token Usage** / **Agents·Skills** /
  **Tool Usage+MCP+Hooks** / **Tool Calls DataTable** — each section gets
  `height: 1fr` so no section crowds out the others. All items are fully
  visible via per-section scroll; display caps (`+N more`) removed.
- **Panel focus-based key routing** — clicking the Flowchart panel routes
  `↑`/`↓`/`j`/`k` to canvas scroll; clicking the Timeline panel routes them to
  DataTable cursor movement. Default at startup is Flowchart. Active panel is
  highlighted with a green border.
- **Activity Sparkline** in the status footer: a rolling 8-bar histogram of
  events/sec over the last 60 seconds using block characters (▁▂▃▄▅▆▇█),
  with a `peak: N/s` label. Auto-suppressed on narrow terminals.
- **Defensive**: schema-tolerant parser (never raises on unknown
  fields), MAX_NODES / MAX_BUFFER_BYTES / MAX_RAW_LINE caps against
  adversarial input, ANSI escape sanitization for terminal safety.

## Install

Requires Python 3.11+.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

**Windows (git-bash / MSYS2)**

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -e '.[dev]'
```

> **Unicode rendering**: the UI uses block characters (▁▂▃▄▅▆▇█) and
> box-drawing glyphs. Use **Windows Terminal** or a font such as
> Cascadia Code / Fira Code for correct display. The legacy `conhost.exe`
> console may render these as boxes or question marks.

## Run

```bash
agentlens                            # auto-pick newest session in cwd's slug dir
agentlens --latest                   # skip picker, take newest
agentlens --session PATH             # attach a specific JSONL
agentlens --project-root PATH        # compute slug from a different cwd
agentlens --self-test                # render one frame, exit 0 (CI smoke)
agentlens -v                         # verbose logging
```

If `watchfiles` can't be installed (common on Windows where the C
extension may fail to build), fall back to the stdlib polling tailer:

```bash
# macOS / Linux / git-bash
AGENTLENS_BACKEND=polling agentlens

# Windows PowerShell
$env:AGENTLENS_BACKEND="polling"; agentlens

# Windows CMD
set AGENTLENS_BACKEND=polling && agentlens
```

See [`docs/USAGE.md`](docs/USAGE.md) for the full usage guide,
including key bindings, mode semantics, drill-down flow, and
architecture notes.

## Windows / git-bash

The slug directory that Claude Code creates for a project is derived
from the working directory path. On Windows the path format differs
(`C:\Users\…` or git-bash's `/c/Users/…`), which can cause the default
slug lookup to miss.

**Automatic fallback** — `SessionLocator` detects the miss and rescans
`~/.claude/projects/` by comparing the `cwd` field recorded in each
JSONL against the current directory (normalising backslashes, MSYS
drive prefixes, and case). The footer shows `[cwd-match]` when the
fallback fired.

**Manual escape hatch** (`Shift+S`) — if the automatic fallback still
doesn't find the right session, press `Shift+S` to open a path-input
modal and paste either:

- a full path to the `.jsonl` file, or
- the first 8+ characters of the session UUID (e.g. `b0709256`).

The UUID approach bypasses slug resolution entirely and works
regardless of path format.

**`--project-root`** — if you always run agentlens from a different
directory than the Claude Code project, pass `--project-root PATH` to
tell agentlens which directory to compute the slug from.

## Tests

```bash
pytest -q           # 313 tests
```

## Manual Verification

The Definition of Done from the original spec tracked two manual
checks beyond the automated suite.

### M-AC8-idle (footer shows `— session idle` after >30s)

**Status:** PASSED — covered by automated tests in
`tests/test_idle_footer.py` (4 tests, all green). The tests
monkeypatch `time.monotonic` and exercise `_refresh_idle_footer`
directly, covering the positive case, the < 30s negative case,
the fresh-session (no event yet) edge case, and the exact
boundary at 30.000 vs 30.001 seconds.

### M-AC11 (idle CPU ≤ 2%)

**Status:** PASSED — measured 2026-04-09.

Measurement procedure: `agentlens` spawned via `pty.fork()`
inside a Python harness, attached to an empty session file with
`AGENTLENS_BACKEND=polling`, sampled via `ps -o pcpu=` once per
second for 10 seconds after a 3-second mount delay.

Results:

| Metric | Value | Target |
|--------|-------|--------|
| Idle CPU average (10s window) | **0.16 %** | ≤ 2 % |
| Idle CPU max (10s window) | **0.30 %** | ≤ 2 % |
| RSS | **40.7 MB** | — |

Well under the target with headroom to spare. Re-measure if the
polling loop or set_interval rate is ever changed.

## Status

Unreleased — TurnSummaryScreen independent scroll sections (Token Usage /
Agents·Skills / Tool Usage+MCP+Hooks / Tool Calls DataTable, each `1fr`). Display
caps removed. Timeline auto-scroll to latest turn on startup. Flowchart layout
coalescing for fast startup. 313 tests passing.

See [CHANGELOG.md](CHANGELOG.md) for the full release history.
