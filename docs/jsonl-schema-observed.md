# Claude Code JSONL Schema — Observed

**Source:** `~/.claude/projects/-Users-limdk-Documents-workspace-harness-visual/b0709256-eb61-4ccb-9b57-49aaca263c33.jsonl`
**Captured:** 2026-04-08
**Line count:** 108 (full file sampled)

This document is the source of truth for `src/agentlens/parser.py`.
It is NOT a public Claude Code contract; the parser is schema-tolerant and
falls back to `EventType.unknown` for any shape not listed here.

## Fallback note

The plan's primary path reads from
`~/.claude/projects/{slug-of-cwd}/*.jsonl`. If that directory is missing or
empty, `SessionLocator.find_active()` falls back to the globally newest
`*.jsonl` under `~/.claude/projects/*/`. In this spike, the slugged dir
was present and contained the single observed session file above.

## Top-level `type` values observed

| type                    | count | notes                                                       |
|-------------------------|-------|-------------------------------------------------------------|
| `assistant`             | 70    | Has `message.content[]` with `text` / `thinking` / `tool_use` |
| `user`                  | 33    | Has `message.content[]` with `text` / `tool_result`         |
| `file-history-snapshot` | 2     | Tracks file edit snapshots; not user-visible in UI          |
| `attachment`            | 2     | Attached file metadata                                      |
| `permission-mode`       | 1     | `{type, permissionMode, sessionId}` — one-shot              |

## Common top-level keys (present on most rows)

```
type, sessionId, parentUuid, isSidechain, uuid, timestamp, userType,
entrypoint, cwd, version, gitBranch, message, requestId (assistant only),
slug, toolUseResult, sourceToolAssistantUUID, sourceToolUseID, isMeta
```

## `message.content[]` block types observed

| content type | count | shape                                                                  |
|--------------|-------|------------------------------------------------------------------------|
| `tool_use`   | 29    | `{type, id (toolu_...), name, input: dict, caller: {type: "direct"}}`  |
| `tool_result`| 28    | `{type, tool_use_id, content: str, is_error: bool}`                    |
| `text`       | 25    | `{type, text: str}`                                                    |
| `thinking`   | 20    | `{type, thinking: str, signature: str}`                                |

### Example payloads (verbatim field shapes)

**tool_use** (found on `assistant` rows):
```json
{"type": "tool_use", "id": "toolu_01DwNMGFMJbcAKh3G5AFjuKd", "name": "Bash",
 "input": {"command": "...", "description": "..."},
 "caller": {"type": "direct"}}
```

**tool_result** (found on `user` rows):
```json
{"tool_use_id": "toolu_01DwNMGFMJbcAKh3G5AFjuKd", "type": "tool_result",
 "content": "total 0\n...", "is_error": false}
```

## Tool names observed (`tool_use.name` values)

| tool name                            | role for flowchart       | input fields used                        |
|--------------------------------------|--------------------------|------------------------------------------|
| `Agent`                              | **subagent spawn** (primary) | `subagent_type`, `description`, `prompt` |
| `Task`                               | **subagent spawn** (legacy)  | `subagent_type`, `description`, `prompt` |
| `Skill`                              | **skill invocation**         | `skill`, `args`                          |
| `Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob` | file/shell ops — ignored by flowchart | n/a |
| `AskUserQuestion`, `ToolSearch`      | meta tools — ignored         | n/a                                      |
| `mcp__plugin_*`                      | MCP tools — ignored          | n/a                                      |

**Important:** The live Claude Code harness currently uses `name: "Agent"` for
the subagent-spawn tool (not `"Task"` as older docs suggest). `CallGraph`
in `graph_model.py` accepts both `"Agent"` and `"Task"` as subagent spawns;
both map to the same `agent:<subagent_type>` node id.

Example `Agent` tool_use:
```json
{"type": "tool_use", "id": "toolu_...", "name": "Agent",
 "input": {"subagent_type": "oh-my-claudecode:planner",
           "description": "Planner: harness-visual TUI plan",
           "prompt": "..."}}
```

Example `Skill` tool_use:
```json
{"type": "tool_use", "id": "toolu_...", "name": "Skill",
 "input": {"skill": "oh-my-claudecode:plan",
           "args": "--consensus --direct path/to/spec.md"}}
```

**text** (on either assistant or user):
```json
{"type": "text", "text": "Base directory for this skill: /..."}
```

**thinking** (assistant only):
```json
{"type": "thinking", "thinking": "...", "signature": "EucDClgIDBgC..."}
```

## Parent / caller correlation

- `tool_use.caller.type == "direct"` — no nested parent tool use observed.
- `parent_tool_use_id` at the content-block level: **not observed** in this
  session (always absent). The UI should still look for it for forward
  compatibility, but missing is the norm.
- Top-level `parentUuid` links a row to its predecessor conversation turn.
- Top-level `sourceToolAssistantUUID` on `user` rows links a `tool_result`
  back to the assistant row that emitted the `tool_use`.

## Agent identity

No explicit `agent_id` field is present in the stream. The UI uses the
following heuristic (see `parser.py::_agent_id_from`):

1. `obj.get("sessionId")` is the primary agent identifier for main-thread
   events.
2. For sub-agent / Task spawns, `obj.get("isSidechain")` is `True`; the
   parent `uuid` chain can be walked to construct a tree, or the tool
   input of a `Task` `tool_use` carries a `subagent_type` hint.

## Fields the UI consumes

| UI column / panel | Source field(s)                                                        |
|-------------------|------------------------------------------------------------------------|
| Timeline ts       | `obj.timestamp`                                                        |
| Timeline tool     | `content.name` (tool_use) or `"result"` (tool_result) or `content.type`|
| Timeline agent    | `obj.sessionId` + sidechain heuristic                                  |
| Timeline status   | `content.is_error` (tool_result) → ok / err; else `running`            |
| Timeline dur_ms   | monotonic delta between matching tool_use / tool_result by `tool_use_id`|
| AgentTree label   | `obj.sessionId[:8]` or tool input `subagent_type`                      |

## `SUPPORTED_TYPES`

Derived directly from the observed counts above:

```python
SUPPORTED_TYPES = {
    "assistant", "user", "file-history-snapshot",
    "attachment", "permission-mode",
}
SUPPORTED_CONTENT_TYPES = {
    "text", "thinking", "tool_use", "tool_result",
}
```

Any other value maps to `EventType.unknown`, is logged at DEBUG, and the
UI continues without interruption (AC10).
