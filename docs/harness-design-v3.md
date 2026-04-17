# agentlens Harness Design v3

> 최종 설계서 | 2026-04-10

---

## 1. 개요

| 항목 | 값 |
|------|-----|
| 프로젝트 | agentlens (Claude Code 세션 라이브 TUI) |
| 기술 스택 | Python 3.11+, Textual, watchfiles, pytest, ruff |
| 설치 경로 | `harness-visual/.claude/` (프로젝트 로컬) |
| 실행 모드 | **에이전트 팀** (TeamCreate) |
| 모델 라우팅 | **복잡도 기반 혼합** (haiku / sonnet / opus) + 동적 승격 |
| 에이전트 | 7개 |
| 스킬 | 9개 (오케스트레이터 1개 포함) |
| 재시도 정책 | 검증-수정 루프 최대 3회 + 에스컬레이션 |

---

## 2. 모델 라우팅

### 2.1 등급 기준

| 등급 | 모델 | 기준 | 비용 비율 |
|------|------|------|----------|
| **Heavy** | `opus` | 다중 파일 연쇄 변경, 불변식 추론, 경계면 디버깅 | 1x |
| **Standard** | `sonnet` | 단일 모듈 구현, 패턴 기반 작업, 테스트 실행 | ~0.2x |
| **Light** | `haiku` | 읽기 전용 탐색, 문서 갱신, 단순 리포트 | ~0.05x |

### 2.2 에이전트별 기본 모델

| 에이전트 | 기본 모델 | 근거 |
|----------|----------|------|
| `jsonl-schema-analyst` | **haiku** | 읽기 전용 탐색, 필드 빈도 집계 |
| `tui-panel-engineer` | **sonnet** | 단일 모듈 패턴 작업 |
| `graph-model-engineer` | **opus** | 불변식 5개 동시 유지 + 중첩 트리 추론 |
| `watcher-locator-engineer` | **sonnet** | 패턴 기반 (race-free tail, fallback) |
| `fixture-replay-qa` | **sonnet** | shape 비교 + 경계면 판단 |
| `textual-test-engineer` | **sonnet** | 테스트 작성/실행 패턴 기반 |
| `release-doc-writer` | **haiku** | 문서 갱신, 창의적 추론 불필요 |

### 2.3 동적 승격 규칙

```
기본 모델로 시작
    ↓
승격 트리거 발생?
    ├─ yes → 상위 모델로 재호출
    └─ no  → 기본 모델 유지
```

| 승격 트리거 | 대상 에이전트 | 승격 |
|-------------|-------------|------|
| QA 실패 iter 2 이상 | 구현 에이전트 3인 | sonnet → **opus** |
| Phase 1.5 불일치 3개+ | `jsonl-schema-analyst` | haiku → **sonnet** |
| 다중 파일 동시 변경 (3파일+) | 해당 구현 에이전트 | sonnet → **opus** |
| 에스컬레이션 직전 (iter 3) | 해당 구현 에이전트 | **opus** 강제 |

### 2.4 비용 시뮬레이션

```
전원 opus (변경 전):    7 × 1.0  = 7.0x
혼합 (정상 흐름):       haiku(2) × 0.05 + sonnet(4) × 0.2 + opus(1) × 1.0 = 1.9x
혼합 (최악, 전원 승격): ≈ 4.0x

정상 흐름 절감: ~73%
최악 케이스 절감: ~43%
```

---

## 3. 팀 토폴로지

```
                          ┌─────────────────────────┐
                          │   agentlens-feature-    │
                          │   pipeline (리더)       │
                          │   오케스트레이터 스킬    │
                          └────────────┬────────────┘
                                       │
     ┌──────────────┬─────────────────┼─────────────────┬──────────────┐
     │              │                 │                  │              │
[Phase 1]     [Phase 1.5]      [Phase 2]          [Phase 3]      [Phase 4]
  조사          교차 검증        구현 (병렬)        QA 루프          문서화
     │              │                 │                  │              │
 ┌───▼───┐    ┌─────▼──────┐   ┌─────▼─────────┐  ┌────▼─────────┐  ┌▼──────────┐
 │schema │    │fixture-    │   │panel-engineer │  │fixture-      │  │release-   │
 │analyst│───►│replay-qa   │   │  [sonnet]     │  │replay-qa     │  │doc-writer │
 │[haiku]│    │[sonnet]    │   │graph-engineer │◄►│  [sonnet]    │  │ [haiku]   │
 └───────┘    └────────────┘   │  [opus]       │  │textual-test  │  └───────────┘
                               │watcher-eng.   │  │  [sonnet]    │
                               │  [sonnet]     │  └──────────────┘
                               └───────────────┘
                                       ▲                  │
                                       │   SendMessage    │
                                       │  (수정 요청)     │
                                       └──────────────────┘
                                      실패 시 iter ≤ 3
                                   iter 2+ → opus 승격
```

---

## 4. Phase 구성

| Phase | 담당 | 모델 | 출력 | 게이트 조건 |
|-------|------|------|------|------------|
| 1. 조사 | `jsonl-schema-analyst` | haiku | `_workspace/01_schema_report.md` | — |
| 1.5 교차 검증 | `fixture-replay-qa` | sonnet | `_workspace/01_5_schema_review.md` | Phase 1 완료 |
| 2. 구현 (병렬) | panel / graph / watcher | sonnet/opus/sonnet | `_workspace/02_{agent}_changes.md` | Phase 1.5 완료 |
| 3. QA 루프 | fixture-replay-qa + textual-test | sonnet + sonnet | `_workspace/qa_iter_{n}.md` | Phase 2 전원 완료 |
| 4. 문서화 | `release-doc-writer` | haiku | `_workspace/04_docs_diff.md` | Phase 3 통과 |

---

## 5. 작업 의존성 DAG

```
task_01_schema_probe          (analyst, haiku)        → deps: []
task_01_5_schema_review       (fixture-qa, sonnet)    → deps: [task_01]
  ├─ tests/fixtures/ 실제 데이터와 01_schema_report.md 교차 비교
  ├─ 불일치 발견 → analyst에 SendMessage (수정 요청 1회)
  ├─ 불일치 3개+ → analyst를 haiku→sonnet 승격 후 재조사
  ├─ analyst 수정 응답 후 Phase 2 진행
  └─ 불일치 없으면 즉시 Phase 2 진행
task_02_panel_changes         (panel, sonnet)         → deps: [task_01_5]
task_03_graph_changes         (graph, opus)           → deps: [task_01_5]
task_04_watcher_changes       (watcher, sonnet)       → deps: [task_01_5]
task_05_replay_qa             (fixture-qa, sonnet)    → deps: [task_02, task_03, task_04]
task_06_pytest_ruff           (textual-test, sonnet)  → deps: [task_02, task_03, task_04]
task_07_qa_gate               (leader)                → deps: [task_05, task_06]
                                                        ↺ 실패 시 task_02..04 재개
                                                          iter 2+ → 실패 에이전트 opus 승격
task_08_docs                  (doc-writer, haiku)     → deps: [task_07 통과]
```

---

## 6. 통신 프로토콜

### 6.1 세 가지 채널

| 채널 | 도구 | 용도 | 메시지 크기 |
|------|------|------|------------|
| **메시지** | `SendMessage` | 빠른 요청/응답 | 짧게 (1~3줄 + 파일 참조) |
| **태스크** | `TaskCreate` / `TaskUpdate` | 진행상황, 의존성, iter 카운터 | 상태 값만 |
| **파일** | `_workspace/` 경로 규약 | 구조화된 산출물, diff, 리포트 | 무제한 |

### 6.2 채널 선택은 규칙 기반

각 에이전트 정의 파일의 `## 팀 통신 프로토콜` 섹션에 상황별 채널을 미리 명시한다. 에이전트는 이 규칙을 따라 자율적으로 판단한다. 오케스트레이터가 매번 지시하지 않는다.

### 6.3 통신 매트릭스

| 발신자 | 수신자 | 채널 | 상황 |
|--------|--------|------|------|
| analyst | panel/graph/watcher | SendMessage | 스키마 변경 영향도 전달 |
| fixture-replay-qa | analyst | SendMessage | Phase 1.5 불일치 수정 요청 |
| fixture-replay-qa | panel/graph/watcher | SendMessage + 파일 | Phase 3 실패 시 수정 요청 |
| panel/graph/watcher | fixture-replay-qa | SendMessage | 수정 완료 → recheck 요청 |
| 모든 에이전트 | 리더 | TaskUpdate | 작업 상태 변경 |
| 리더 | 모든 에이전트 | TaskCreate | 작업 할당 |
| fixture-replay-qa | 리더 | SendMessage + 파일 | 3회 실패 → 에스컬레이션 |
| 리더 | 구현 에이전트 | TaskUpdate (metadata) | 모델 승격 지시 |

### 6.4 파일 경로 규약

```
_workspace/
├── 01_schema_report.md
├── 01_5_schema_review.md
├── 02_panel_changes.md
├── 02_graph_changes.md
├── 02_watcher_changes.md
├── qa_iter_1.md
├── qa_iter_2.md
├── qa_iter_3.md
├── escalation.md
└── 04_docs_diff.md
```

---

## 7. 재시도 정책

```yaml
max_iterations: 3

success_criteria:
  - fixture-replay-qa 경계면 assertion 전부 통과
  - pytest 전체 green
  - ruff check clean

on_failure:
  iter_1 (전체 검증):
    - 경계면 4곳 전체 + pytest 전체 + ruff check
    - QA가 _workspace/qa_iter_1.md 에 실패 diff + 의심 원인 기록
    - 해당 구현 에이전트에 SendMessage (기본 모델 유지)
  iter_2 (2단계 검증):
    - 1단계: 실패했던 경계면/테스트만 타겟 재검증
      - fail → 즉시 qa_iter_2.md 작성, 2단계 스킵
      - pass → 2단계 진행
    - 2단계: 나머지 경계면 + pytest 전체 (회귀 확인)
    - 실패한 구현 에이전트를 opus로 승격
  iter_3 (2단계 검증):
    - 동일 2단계 프로토콜 (타겟 → 회귀)
    - opus 강제, 마지막 시도
  iter_gt_3:
    - _workspace/escalation.md (3회 전체 이력 + 가설)
    - 리더 → 사람 개입 요청

forbidden:
  - QA 에이전트의 코드 수정 (작성자-검증자 분리)
  - fixture 파일 수정으로 테스트 통과시키기 (즉시 에스컬레이션)
```

---

## 8. 에이전트 정의 요약

### 8.1 jsonl-schema-analyst

| 항목 | 값 |
|------|-----|
| 타입 | `Explore` |
| 기본 모델 | **haiku** |
| 승격 | 불일치 3개+ → **sonnet** |
| 스킬 | `jsonl-schema-probe`, `defensive-parsing` |

JSONL 스키마 변종/신규 필드 탐지, `docs/jsonl-schema-observed.md` 유지.

### 8.2 tui-panel-engineer

| 항목 | 값 |
|------|-----|
| 타입 | `general-purpose` |
| 기본 모델 | **sonnet** |
| 승격 | QA iter 2+ 또는 3파일+ → **opus** |
| 스킬 | `textual-panel-patterns` |

Textual 패널, reactive 바인딩, CSS, Modal 구현·수정.

### 8.3 graph-model-engineer

| 항목 | 값 |
|------|-----|
| 타입 | `general-purpose` |
| 기본 모델 | **opus** (고정) |
| 스킬 | `graph-model-invariants`, `defensive-parsing` |

노드/엣지 모델, 중첩 서브에이전트 트리, sticky-running, 인스턴스 집계.

### 8.4 watcher-locator-engineer

| 항목 | 값 |
|------|-----|
| 타입 | `general-purpose` |
| 기본 모델 | **sonnet** |
| 승격 | QA iter 2+ 또는 3파일+ → **opus** |
| 스킬 | `watcher-portability` |

라이브 tail, Windows/git-bash 호환, 서브에이전트 JSONL 자동 발견.

### 8.5 fixture-replay-qa

| 항목 | 값 |
|------|-----|
| 타입 | `general-purpose` |
| 기본 모델 | **sonnet** (고정) |
| 스킬 | `replay-fixture-harness` |

Phase 1.5 스키마 교차 검증 + Phase 3 경계면 QA. 코드·fixture 수정 금지.

### 8.6 textual-test-engineer

| 항목 | 값 |
|------|-----|
| 타입 | `general-purpose` |
| 기본 모델 | **sonnet** (고정) |
| 스킬 | `textual-async-testing` |

Textual async 테스트, pytest/ruff 실행. 코드 수정 금지.

### 8.7 release-doc-writer

| 항목 | 값 |
|------|-----|
| 타입 | `general-purpose` |
| 기본 모델 | **haiku** (고정) |
| 스킬 | `agentlens-release-notes` |

CHANGELOG, README, USAGE, ROADMAP 갱신. Phase 4에서만 동작.

---

## 9. 스킬 목록

| # | 스킬 | 사용 에이전트 | 번들 |
|---|------|---------------|------|
| 1 | `jsonl-schema-probe` | schema-analyst | `scripts/probe.py` |
| 2 | `textual-panel-patterns` | panel-engineer | `references/{datatable,modal,css,reactive}.md` |
| 3 | `graph-model-invariants` | graph-engineer | — |
| 4 | `watcher-portability` | watcher-engineer | — |
| 5 | `replay-fixture-harness` | fixture-replay-qa | `scripts/replay.py` |
| 6 | `textual-async-testing` | textual-test-engineer | — |
| 7 | `defensive-parsing` | schema-analyst, graph-engineer | — |
| 8 | `agentlens-release-notes` | release-doc-writer | — |
| 9 | `agentlens-feature-pipeline` | 오케스트레이터 | — |

---

## 10. 오케스트레이터 상세

### 10.1 팀 구성

```yaml
team_name: agentlens-feature-team
members:
  - jsonl-schema-analyst         (Explore, haiku)
  - tui-panel-engineer           (general-purpose, sonnet)
  - graph-model-engineer         (general-purpose, opus)
  - watcher-locator-engineer     (general-purpose, sonnet)
  - fixture-replay-qa            (general-purpose, sonnet)
  - textual-test-engineer        (general-purpose, sonnet)
  - release-doc-writer           (general-purpose, haiku)
```

### 10.2 모델 승격 프로토콜

1. QA가 리더에게 승격 요청 SendMessage
2. 리더가 TaskUpdate(metadata: {model: "opus"})로 기록
3. 리더가 해당 에이전트에 승격 + 재시도 요청 SendMessage
4. 에이전트는 승격된 모델로 재호출

### 10.3 Phase 1 스킵 조건

| 작업 유형 | Phase 1 | Phase 1.5 |
|-----------|---------|-----------|
| 새 이벤트 타입 지원 | 필수 | 필수 |
| JSONL 파서 수정 | 필수 | 필수 |
| UI만 수정 (CSS, 키바인딩) | 스킵 | 스킵 |
| 기존 기능 버그 수정 | 리더 판단 | Phase 1 실행 시만 |

### 10.4 에러 핸들링

| 에러 유형 | 전략 |
|-----------|------|
| 에이전트 1회 실행 실패 | 1회 재시도, 재실패 시 결과 없이 진행 |
| 상충 데이터 | 출처 병기, 리더 판정 |
| fixture 자체 오류 의심 | 즉시 에스컬레이션 |
| QA 3회 초과 | escalation.md + 사람 개입 |
| Phase 1.5 불일치 | analyst 수정 1회, 3개+ 시 승격 |

### 10.5 테스트 시나리오

**정상:** "cross-highlight 추가" → analyst(haiku) → QA pass → panel(sonnet) → QA pass → docs(haiku)

**재시도+승격:** "depth 6 지원" → iter1 실패 → iter2 panel opus 승격 → iter3 pass

**에스컬레이션:** "tool_use_partial 표시" → iter1~3 실패 (fixture 부족) → escalation.md

---

## 11. 산출물 트리

```
.claude/
├── agents/
│   ├── jsonl-schema-analyst.md          [haiku]
│   ├── tui-panel-engineer.md            [sonnet → opus]
│   ├── graph-model-engineer.md          [opus]
│   ├── watcher-locator-engineer.md      [sonnet → opus]
│   ├── fixture-replay-qa.md             [sonnet]
│   ├── textual-test-engineer.md         [sonnet]
│   └── release-doc-writer.md            [haiku]
├── skills/
│   ├── jsonl-schema-probe/
│   │   ├── skill.md
│   │   └── scripts/probe.py
│   ├── textual-panel-patterns/
│   │   ├── skill.md
│   │   └── references/{datatable,modal,css,reactive}.md
│   ├── graph-model-invariants/skill.md
│   ├── watcher-portability/skill.md
│   ├── replay-fixture-harness/
│   │   ├── skill.md
│   │   └── scripts/replay.py
│   ├── textual-async-testing/skill.md
│   ├── defensive-parsing/skill.md
│   ├── agentlens-release-notes/skill.md
│   └── agentlens-feature-pipeline/skill.md
└── _workspace/   (런타임, .gitignore)
```

---

## 12. 검증 체크리스트

- [ ] 에이전트 7개 + 팀 통신 프로토콜 섹션
- [ ] 에이전트별 기본 모델 + 승격 조건 명시
- [ ] 스킬 9개 pushy description
- [ ] `.claude/commands/` 비생성
- [ ] 오케스트레이터: 재시도·에러·승격·스킵·시나리오
- [ ] Phase 1.5 교차 검증 (1회, 루프 없음, 3개+ 승격)
- [ ] QA 수정 금지 명시
- [ ] `_workspace/` .gitignore
- [ ] skill.md 500줄 이내
