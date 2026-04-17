---
name: replay-fixture-harness
description: "tests/fixtures/의 실제 JSONL 슬라이스를 사용해 parser→graph_model→panel 전체를 replay하여 경계면 shape을 비교. agentlens QA, 회귀 테스트 작성, 경계면 검증, fixture 기반 테스트 작업 시 반드시 이 스킬을 사용할 것."
---

# Replay Fixture Harness

`tests/fixtures/` 실제 JSONL 슬라이스를 사용한 경계면 shape 비교 QA 방법론.

## 경계면 3곳

```
[JSONL fixture]
      │ parse_line()
      ▼
[HarnessEvent]  ←── 경계면 1: parser ↔ graph_model
      │ CallGraph.ingest()
      ▼
[Node/Edge]     ←── 경계면 2: graph_model ↔ flowchart_layout
      │ layout()
      ▼
[Position/Size] ←── 경계면 3: graph_model ↔ timeline (DataTable row shape)
```

## replay 스크립트 사용법

```bash
python .claude/skills/replay-fixture-harness/scripts/replay.py <fixture_path>
```

출력: 각 경계면의 shape dump (JSON 형태)

## 경계면별 Shape Assertion 템플릿

### 경계면 1: parser → graph_model

```python
events = [ev for ln in lines for ev in parse_line(ln)]
# 검증할 shape:
for ev in events:
    assert isinstance(ev, HarnessEvent)
    assert ev.type in EventType.__members__.values()
    assert ev.ts is not None
    if ev.type == EventType.tool_use:
        assert "tool_name" in ev.payload
        assert "tool_use_id" in ev.payload
```

### 경계면 2: graph_model → flowchart_layout

```python
graph = CallGraph()
for ev in events:
    graph.update_from_event(ev)
# 검증할 shape:
assert ROOT_ID in graph.nodes
for node_id, node in graph.nodes.items():
    assert node.type in ("root", "agent", "skill")
    assert node.status in ("running", "done", "error")
    assert len(node.label) <= MAX_LABEL_LEN
assert len(graph.nodes) <= MAX_NODES + 1  # +1 for root
```

### 경계면 3: graph_model → timeline

```python
# DataTable row가 가져야 할 컬럼: ts, tool, agent, status, dur_ms
for ev in events:
    if ev.type in (EventType.tool_use, EventType.tool_result):
        assert "tool_name" in ev.payload or ev.type == EventType.tool_result
```

## 실패 diff 리포트 포맷

`_workspace/{slug}/qa_iter_{n}.md`:

```markdown
# QA Iteration {n}

## 실패 경계면
경계면 1: parser ↔ graph_model

## 기대값
tool_use 이벤트에 `tool_use_id` 필드 존재

## 실제값
`tool_use_id` 누락 (3건 / 전체 29건)

## 관련 파일
- `src/agentlens/parser.py:45` — _extract_linked_subagent_uuid
- `tests/fixtures/real_session_slice.jsonl:17`

## 의심 원인
parser가 새 content block 구조를 처리하지 못함

## 누적 컨텍스트
(iter 1 요약이 있으면 여기에)
```

## 타겟 검증 모드 (iter 2+ 재검증 시)

재검증 시에는 전체 경계면을 처음부터 돌리지 않는다. 2단계로 나눈다:

**1단계 — 타겟 검증:**
- `qa_iter_{n-1}.md`의 `## 실패 경계면` 섹션을 읽는다
- 실패했던 경계면만 replay한다 (예: 경계면 1만 실패했으면 경계면 1만)
- fail → 즉시 리포트, 2단계 스킵 (불필요한 전체 검증 방지)
- pass → 2단계 진행

**2단계 — 회귀 검증:**
- 나머지 경계면 전부 replay
- 수정이 다른 경계면을 깨뜨리지 않았는지 확인
- fail → 새 실패 경계면을 리포트에 추가
- pass → QA 통과

이 방식으로 1줄 수정에 전체 4경계면 replay를 피하고, 빠른 피드백 루프를 유지한다.

## 기존 테스트 패턴 참조

`test_replay_real_slice.py`가 이 접근의 원형:
- fixture 로드 → parse_line() → unknown ratio < 5% assertion
- 이 패턴을 경계면 2, 3으로 확장하는 것이 이 스킬의 핵심
