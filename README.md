# agentlens

[한국어](docs/README.ko.md)

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

Live-tail TUI for Claude Code sessions. Watch every tool call, agent spawn,
and subagent tree unfold in real time — without touching your Claude Code
workflow.

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

- **Live tail** — new events from the Claude Code session JSONL appear within
  ~1 second via `watchfiles` (stdlib polling fallback included).
- **Timeline panel** — scrollable table of turns with prompt preview, tool
  count, and duration. Press `Enter` to open a turn detail modal with full
  prompt, token usage, and per-tool breakdown.
- **Flowchart panel** — directed graph of Agent/Task/Skill calls with
  parent/child edges, `(xN)` duplicate counters, per-subagent tool badges
  (e.g. `Rd12 Ed5`), and color-coded running/done/error status.
- **Nested subagent tree** up to depth 5 — subagents that spawn further
  subagents via Skill appear as proper child nodes, not collapsed onto `main`.
- **Parallel-instance view** — in `[running]` mode each parallel spawn is a
  distinct box; in `[all]` mode they aggregate with a `(xN)` counter. Press
  `d` to drill into a specific instance's tool history.
- **Session switching** — press `s` to switch sessions without restarting;
  `Shift+S` to paste a path or UUID directly. Windows / git-bash path formats
  are normalised automatically.

See [`docs/USAGE.md`](docs/USAGE.md) for the full feature list, key bindings,
mode semantics, and architecture notes.

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
pytest -q
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full release history.
