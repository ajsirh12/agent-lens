# agentlens

Live-tail TUI for Claude Code sessions. Shows a Timeline of tool calls
alongside a real-time Flowchart of agent and skill spawns, including
nested subagent trees and parallel-instance views.

```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│ Timeline                             │ Flowchart                            │
│ ───────────────────────────────────  │ ──────────────────────────────────   │
│ ts        tool      agent  status    │        ┌──────┐                      │
│ 14:02:01  Task      main   ✓  1205   │        │ main │                      │
│ 14:02:03  Task      main   ✓  4708   │        └───┬──┘                      │
│ 14:02:10  Read      exec   ✓    12   │            │                         │
│ 14:02:11  Edit      exec   ✓    45   │   ┌────────┼──────────┐              │
│ ...                                  │   ▼        ▼          ▼              │
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
- **Timeline panel**: scrollable DataTable of every tool_use /
  tool_result event in the session, with cross-highlight to the
  flowchart.
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
- **Subagent watcher** automatically discovers and tails new
  `agent-*.jsonl` files as they're created under the session's
  `subagents/` directory.
- **Defensive**: schema-tolerant parser (never raises on unknown
  fields), MAX_NODES / MAX_BUFFER_BYTES / MAX_RAW_LINE caps against
  adversarial input, ANSI escape sanitization for terminal safety.

## Install

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Run

```bash
agentlens                            # auto-pick newest session in cwd's slug dir
agentlens --latest                   # skip picker, take newest
agentlens --session PATH             # attach a specific JSONL
agentlens --project-root PATH        # compute slug from a different cwd
agentlens --self-test                # render one frame, exit 0 (CI smoke)
agentlens -v                         # verbose logging
```

If `watchfiles` can't be installed, set `AGENTLENS_BACKEND=polling`
to force the stdlib polling tailer:

```bash
AGENTLENS_BACKEND=polling agentlens
```

See [`docs/USAGE.md`](docs/USAGE.md) for the full usage guide,
including key bindings, mode semantics, drill-down flow, and
architecture notes.

## Tests

```bash
pytest -q           # 133 tests
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

v0.3.0 — renamed from harness-visual → agentlens, with mid-session
switch via `s`, and the v0.2.0 feature set (live-tail Flowchart,
nested trees, instance view, drill-down, rendering toggles)
preserved. 133 tests passing.

See [CHANGELOG.md](CHANGELOG.md) for the full release history.
