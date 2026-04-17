---
name: agentlens-feature-pipeline
description: "agentlens에 새 기능 추가, 버그 수정, 리팩토링 작업 시 사용. schema-analyst 조사 → 교차 검증 → 구현 3인 병렬 → QA 2인 → 최대 3회 수정 루프(iter 2+ opus 승격) → 문서 갱신까지 전체 에이전트 팀을 조율하는 오케스트레이터. agentlens 코드 변경, 기능 구현, 버그 픽스, 리팩토링 작업 시 반드시 이 스킬을 사용할 것."
---

# agentlens Feature Pipeline — Orchestrator

agentlens 에이전트 팀을 조율하여 조사→구현→검증→문서화 파이프라인을 실행한다.

## Phase 0: Slug 생성 + 재개 감지

파이프라인 시작 시 **먼저 재개 여부를 확인**한다. 이후 모든 산출물은 `_workspace/{slug}/` 하위에 저장한다.

### 재개 감지 (clear/compact 후 복구)

```
1. _workspace/ 하위 디렉토리 목록 확인
2. 각 디렉토리에서 pipeline_status.md 존재 여부 확인
3. pipeline_status.md가 있고 pipeline이 "agentlens-feature-pipeline"이며
   status가 "in_progress"인 경우:
   - 사용자에게 진행 중인 작업 목록 제시
   - "이어서 진행할까요?" 확인 후 해당 slug로 resume
4. 없거나 사용자가 새 작업 선택 → 아래 slug 생성 진행
```

**resume 시**: slug 생성 스킵, 팀 재구성 후 `pipeline_status.md`의 `next_phase`부터 실행.

### 새 작업 slug 규칙

- 작업 요청의 핵심 키워드를 kebab-case로 변환 (예: "Timeline cross-highlight" → `timeline-cross-highlight`)
- 영문 소문자 + 하이픈만 사용, 최대 30자
- 동일 slug가 이미 존재하고 status가 "completed"면 숫자 접미사 추가 (`timeline-cross-highlight-2`)
- design-pipeline에서 핸드오프된 경우 동일 slug를 이어받는다 (pipeline_status.md 이어쓰기)
- slug 결정 후 즉시 `pipeline_status.md` 초기화 또는 갱신 (아래 형식)

### pipeline_status.md 형식

```markdown
# Pipeline Status
slug: {slug}
pipeline: agentlens-feature-pipeline
status: in_progress          # in_progress | completed | blocked
current_phase: phase_1
next_phase: phase_1_5
request: "{원문}"
updated_at: {ISO 8601}

## Completed Phases
- [ ] phase_1   — 01_schema_report.md
- [ ] phase_1_5 — 01_5_schema_review.md
- [ ] phase_2   — 02_*_changes.md
- [ ] phase_3   — QA pass (qa_iter_{n}.md)
- [ ] phase_3_5 — code_review.md (PASS)
- [ ] phase_4   — 04_docs_diff.md
```

체크박스는 각 Phase 완료 직후 `[x]`로 갱신하고 `current_phase` / `next_phase`를 업데이트한다.  
파이프라인 정상 종료 시 `status: completed`로 변경한다.

## 팀 구성

```
TeamCreate:
  team_name: agentlens-feature-team
  members:
    - jsonl-schema-analyst         (Explore, haiku)
    - tui-panel-engineer           (general-purpose, sonnet)
    - graph-model-engineer         (general-purpose, opus)
    - watcher-locator-engineer     (general-purpose, sonnet)
    - fixture-replay-qa            (general-purpose, sonnet)
    - textual-test-engineer        (general-purpose, sonnet)
    - code-reviewer                (general-purpose, opus)
    - release-doc-writer           (general-purpose, haiku)
```

## Phase 흐름

### Phase 1: 조사 (jsonl-schema-analyst, haiku)

스키마 탐색 → `_workspace/{slug}/01_schema_report.md` 생성.

**스킵 조건:**
- UI만 수정 (CSS, 키바인딩): Phase 1 + 1.5 스킵
- 기존 기능 버그 수정: 리더 판단

Phase 1 완료 → `pipeline_status.md` 갱신: `current_phase: phase_1_5`, phase_1 체크박스 `[x]`.

### Phase 1.5: 교차 검증 (fixture-replay-qa, sonnet)

`01_schema_report.md`를 `tests/fixtures/` 실제 데이터와 교차 비교.

- **1회 리뷰, 루프 없음**
- 불일치 발견 → `01_5_schema_review.md`에 상세 기록 → 리더가 판단 후 analyst 재조사 여부 결정
- 불일치 3개+ → 리더에 analyst 승격 요청 (haiku → sonnet)
- 출력: `_workspace/{slug}/01_5_schema_review.md`
- Phase 1 실행 시에만 동작

Phase 1.5 완료 → `pipeline_status.md` 갱신: `current_phase: phase_2`, phase_1_5 체크박스 `[x]`.

### Phase 2: 구현 (병렬, sonnet/opus/sonnet)

3인 병렬 실행. 각자 `_workspace/{slug}/02_{agent}_changes.md` 작성.

| 에이전트 | 기본 모델 | 담당 |
|----------|----------|------|
| tui-panel-engineer | sonnet | panels/, app.py, app.tcss |
| graph-model-engineer | opus | graph_model.py, flowchart_layout.py, events.py |
| watcher-locator-engineer | sonnet | watcher.py, locator.py, subagent_*.py |

작업 범위에 해당하지 않는 에이전트는 task를 즉시 completed 처리한다.

Phase 2 완료 → `pipeline_status.md` 갱신: `current_phase: phase_3`, phase_2 체크박스 `[x]`.

### Phase 3: QA 루프 (sonnet + sonnet)

2인 병렬 검증. `fixture-replay-qa` + `textual-test-engineer`.

**성공 기준 (전부 만족):**
1. fixture-replay-qa 경계면 assertion 전부 통과
2. pytest 전체 green
3. ruff check clean

**실패 시 재시도 (리더가 파일 읽고 라우팅):**

```
iter 1 (전체 검증):
  - QA 에이전트: 경계면 4곳 전체 + pytest 전체 + ruff check
  - qa_iter_1.md 작성 (실패 경계면, 실패 테스트, 기대값, 실제값, 의심 원인, 수정 필요 파일)
  - 리더: qa_iter_1.md 읽기 → 해당 구현 에이전트에 TaskCreate (기본 모델 유지)

iter 2 (2단계 검증):
  - 1단계: 실패했던 경계면/테스트만 타겟 재검증 (빠른 피드백)
    - fail → 즉시 qa_iter_2.md 작성, 구현 에이전트에 반환 (2단계 스킵)
    - pass ↓
  - 2단계: 나머지 경계면 + pytest 전체 (회귀 확인)
    - fail → 새 실패를 qa_iter_2.md에 기록
    - pass → QA 통과
  - 리더: qa_iter_2.md 읽기 → 실패한 구현 에이전트를 opus로 승격
  - 리더가 TaskUpdate(metadata: {model: "opus"}) 기록

iter 3 (2단계 검증):
  - 동일 2단계 프로토콜 (타겟 → 회귀)
  - opus 강제, 마지막 시도

iter > 3:
  - QA 에이전트: _workspace/{slug}/escalation.md 작성
    - 3회 전체 실패 diff 요약
    - 수정 시도 내역
    - 의심 가설
  - 리더: 사람 개입 요청
```

**금지:**
- QA 에이전트의 코드 수정 (작성자-검증자 분리)
- fixture 파일 수정으로 테스트 통과시키기 (즉시 에스컬레이션)

### Phase 3.5: 코드 리뷰 (code-reviewer, opus)

QA 통과 후 code-reviewer가 체크리스트 기반으로 구현을 검증한다.

- 입력: `design_spec.md` (있으면), `02_*_changes.md`, git diff
- 출력: `_workspace/{slug}/code_review.md` (PASS / REJECT)
- 사용 스킬: `code-review-checklist`
- **작성자-검증자 분리**: 구현 에이전트와 다른 인스턴스

**재시도 루프:**

```
iter 1 REJECT:
  - review에 명시된 수정 요청을 해당 구현 에이전트에 SendMessage
  - 해당 에이전트가 수정 → QA 재실행 → code-reviewer 재실행 (iter 2)

iter 2 REJECT:
  - 리더가 사용자에 에스컬레이션
  - _workspace/{slug}/code_review.md에 전체 iter 기록
```

PASS 시에만 Phase 4(문서화)로 진행한다.

QA pass → `pipeline_status.md` 갱신: `current_phase: phase_3_5`, phase_3 체크박스 `[x]`.  
code_review PASS → `pipeline_status.md` 갱신: `current_phase: phase_4`, phase_3_5 체크박스 `[x]`.

### Phase 4: 문서화 (release-doc-writer, haiku)

코드 리뷰 통과 후 실행. `_workspace/{slug}/02_*_changes.md` 참조하여 문서 4종 갱신.

## 작업 의존성 DAG

```
task_01_schema_probe          (analyst)       → deps: []
task_01_5_schema_review       (fixture-qa)    → deps: [task_01]
task_02_panel_changes         (panel)         → deps: [task_01_5]
task_03_graph_changes         (graph)         → deps: [task_01_5]
task_04_watcher_changes       (watcher)       → deps: [task_01_5]
task_05_replay_qa             (fixture-qa)    → deps: [task_02, task_03, task_04]
task_06_pytest_ruff           (textual-test)  → deps: [task_02, task_03, task_04]
task_07_qa_gate               (leader)        → deps: [task_05, task_06]
                                                ↺ 실패 시 task_02..04 재개
task_08_code_review           (code-reviewer) → deps: [task_07 통과]
                                                ↺ REJECT 시 task_02..04 재개
task_09_docs                  (doc-writer)    → deps: [task_08 PASS]
```

## 모델 승격 프로토콜

1. QA가 리더에게 SendMessage("agent X iter 2 실패, opus 승격 요청")
2. 리더가 TaskUpdate(metadata: {model: "opus"})로 기록
3. 리더가 해당 에이전트에 SendMessage("opus로 승격. 재시도: {컨텍스트}")
4. Agent 도구 재호출 시 model 파라미터를 opus로 변경

## 데이터 전달

| 채널 | 용도 |
|------|------|
| SendMessage | 리더 ↔ 에이전트 에스컬레이션 전용 (에이전트 간 직접 통신 금지) |
| TaskCreate/TaskUpdate | 진행상황, 의존성, iter 카운터 |
| _workspace/{slug}/ 파일 | 에이전트 간 유일한 데이터 교환 채널 |

파일 경로 규약:
```
_workspace/{slug}/
├── pipeline_status.md          (오케스트레이터, Phase 0 생성 → 각 Phase 완료 시 갱신)
├── 01_schema_report.md
├── 01_5_schema_review.md
├── 02_panel_changes.md
├── 02_graph_changes.md
├── 02_watcher_changes.md
├── qa_iter_{n}.md
├── code_review.md
├── escalation.md
└── 04_docs_diff.md
```

Phase 4 완료 → `pipeline_status.md`를 `status: completed`, phase_4 `[x]`로 갱신.

## 에러 핸들링

| 에러 유형 | 전략 |
|-----------|------|
| 에이전트 1회 실행 실패 | 1회 재시도, 재실패 시 결과 없이 진행 + 누락 명시 |
| 상충 데이터 | 출처 병기, 리더 판정 |
| fixture 자체 오류 의심 | 즉시 에스컬레이션 (fixture 수정 금지) |
| QA 3회 초과 | escalation.md + 사람 개입 |
| Phase 1.5 불일치 | 01_5_schema_review.md에 기록 → 리더 판단 후 analyst 재조사, 3개+ 시 승격 |

## 테스트 시나리오

### 정상 흐름
> "Timeline에 cross-highlight 추가"
1. analyst(haiku): 스키마 변경 없음 → report
2. fixture-qa(sonnet): 교차 검증 pass
3. panel-engineer(sonnet): cross-highlight 구현
4. QA: pass
5. doc-writer(haiku): README + CHANGELOG

### 재시도 + 승격
> "서브에이전트 depth 6 지원"
1. iter 1: graph-engineer(opus) 수정 → QA 실패
2. iter 2: panel-engineer sonnet→opus 승격 → QA 실패
3. iter 3: panel-engineer(opus) 재수정 → QA pass → 문서화

### 에스컬레이션
> "새 이벤트 tool_use_partial 표시"
1. iter 1~3 실패 (fixture 부족)
2. escalation.md: "실제 세션 수집 필요"
