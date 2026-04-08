# harness-visual

Live-tail TUI for Claude Code sessions + OMC team state.

Two synchronized panels on one screen:

- **Timeline** (`DataTable`): tool_use / tool_result events from the attached
  JSONL session.
- **Agent Tree** (`Tree`): sub-agents from `.omc/state/subagent-tracking.json`
  and `mission-state.json`.

Both panels cross-highlight bidirectionally through a single reactive
`selected_agent_id` on the app.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

If `watchfiles` cannot be installed (offline, exotic platform), the TUI
auto-falls back to a stdlib polling tailer. You can also force it:

```bash
HARNESS_VISUAL_BACKEND=polling harness-visual
```

## Run

```bash
harness-visual                       # auto-pick newest session under ~/.claude/projects/
harness-visual --session PATH        # attach to a specific JSONL
harness-visual --project-root PATH   # use PATH to compute the session slug
harness-visual --self-test           # render once, then exit 0 (CI smoke)
```

`q` quits, `j/k` or arrows move the Timeline cursor, `Enter` opens the tool
detail modal.

## Tests

```bash
pytest -q
```

## Acceptance criteria — verification map

| AC   | Test / procedure                                                           |
|------|----------------------------------------------------------------------------|
| AC1  | `tests/test_smoke.py::test_launches_and_renders_empty`                     |
| AC2  | `tests/test_locator.py` (primary + fallback)                               |
| AC3  | `tests/test_smoke.py` mounts both panels                                   |
| AC4  | `tests/test_smoke.py::test_live_tail_latency_under_one_second`             |
| AC5  | `tests/test_omc_state.py::test_subagent_tracking_diff_emits_spawn`         |
| AC6  | `tests/test_cross_highlight.py` (forward + reverse)                        |
| AC7  | `tests/test_smoke.py::test_enter_opens_detail_modal`                       |
| AC8  | `tests/test_responsiveness.py::test_keypress_repaint_under_200ms` + M-AC8  |
| AC9  | `tests/test_smoke.py::test_launches_and_renders_empty`                     |
| AC10 | `tests/test_parser.py` + `tests/test_replay_real_slice.py`                 |
| AC11 | Manual procedure **M-AC11** below                                          |

## Manual Verification

### M-AC8-idle

1. `harness-visual` attached to a JSONL.
2. Stop appending; wait 35 s.
3. Expected: footer reads `session idle`. `j` / `k` still responsive.
4. Record timestamp + pass/fail here.

_Recorded: [pending]_

### M-AC11 (idle CPU ≤ 2%)

1. `harness-visual` + `top -pid $(pgrep -f harness_visual) -stats cpu`.
2. Observe ≤ 2% over 30 s against an idle JSONL.
3. Record timestamp + pass/fail here.

_Recorded: [pending]_

### M-live (live Claude Code session)

1. Terminal A: `harness-visual`.
2. Terminal B: `python scripts/fake_session.py --count 200 --rate 10 --target /tmp/fake.jsonl`.
3. Confirm Timeline fills and AgentTree grows. Then `--rotate-at 50`; confirm
   watcher recovers.

_Recorded: [pending]_

## Architecture

`SessionWatcher` (watchfiles or polling) → `app.post_message()` → panels.
`bus.py` (≤30 LOC) is a test-only seam: an `asyncio.Queue` the watcher also
publishes to so headless tests can drain events without mounting the UI.

See `docs/jsonl-schema-observed.md` for the observed Claude Code JSONL shape
driving `parser.py`.
