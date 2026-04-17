---
name: feature-design-pipeline
description: "자연어 기능 요청을 구조화된 설계 문서로 변환하는 오케스트레이터. 요구사항 분석 → 영향 분석 → UX 설계를 3인 병렬로 실행한 뒤 종합하여 설계 문서를 생성한다. '설계해줘', '설계해봐', '설계하자', '기능 설계해줘', '설계 먼저 해줘', '어떻게 만들면 좋을지', '어떻게 구현하면 좋을지', '구현 전에 설계부터', '요구사항 정리해줘', '영향 분석해줘', '어떻게 만들까', 'X를 설계해봐' 등 구현 전 설계·분석 요청 시 반드시 이 스킬을 사용할 것. 구현 자체를 요청하는 경우('구현해줘', '코드 변경', '버그 수정')에는 이 스킬이 아닌 agentlens-feature-pipeline을 사용할 것."
---

# Feature Design Pipeline — Orchestrator

자연어 기능 요청을 받아 3인 병렬 분석 → 1인 종합으로 설계 문서를 생성한다.

## 오케스트레이터 규칙 (필독)

이 스킬을 읽은 Claude는 **분석·설계를 직접 수행하지 않는다**. 리더(오케스트레이터) 역할만 한다.

- 요구사항 분석 → requirements-analyst에 위임
- 영향 분석 → impact-analyst에 위임
- UX 설계 → ux-spec-writer에 위임
- 종합 → design-synthesizer에 위임
- 리뷰 → design-reviewer에 위임

스킬 로드 즉시 아래 Phase 0부터 실행한다. 직접 설계 문서를 작성하거나 분석 결과를 텍스트로 출력하는 것은 금지다.

## Phase 0: Slug 생성 + 재개 감지

파이프라인 시작 시 **먼저 재개 여부를 확인**한다. 이후 모든 산출물은 `_workspace/{slug}/` 하위에 저장한다.

### 재개 감지 (clear/compact 후 복구)

```
1. _workspace/ 하위 디렉토리 목록 확인
2. 각 디렉토리에서 pipeline_status.md 존재 여부 확인
3. pipeline_status.md가 있고 status가 "in_progress"인 경우:
   - 사용자에게 진행 중인 작업 목록 제시
   - "이어서 진행할까요?" 확인 후 해당 slug로 resume
4. 없거나 사용자가 새 작업 선택 → 아래 slug 생성 진행
```

**resume 시**: slug 생성 스킵, 팀 재구성 후 `pipeline_status.md`의 `next_phase`부터 실행.

### 새 작업 slug 규칙

- 기능 요청의 핵심 키워드를 kebab-case로 변환 (예: "Timeline 필터링" → `timeline-filter`)
- 영문 소문자 + 하이픈만 사용, 최대 30자
- 동일 slug가 이미 존재하고 status가 "completed"면 숫자 접미사 추가 (`timeline-filter-2`)
- slug 결정 후 즉시 `pipeline_status.md` 초기화 (아래 형식)

### pipeline_status.md 형식

```markdown
# Pipeline Status
slug: {slug}
pipeline: feature-design-pipeline
status: in_progress          # in_progress | completed | blocked
current_phase: phase_1
next_phase: phase_1_5
request: "{원문}"
updated_at: {ISO 8601}

## Completed Phases
- [ ] phase_1   — design_01~03_*.md
- [ ] phase_1_5 — feasibility gate
- [ ] phase_2   — design_spec.md
- [ ] phase_2_5 — design_review.md (PASS)
- [ ] phase_3   — 사용자 승인
```

체크박스는 각 Phase 완료 직후 `[x]`로 갱신하고 `current_phase` / `next_phase`를 업데이트한다.  
파이프라인 정상 종료 시 `status: completed`로 변경한다.

## 팀 구성

```
TeamCreate:
  team_name: agentlens-design-team
  members:
    - requirements-analyst      (general-purpose, sonnet)
    - impact-analyst            (general-purpose, sonnet)
    - ux-spec-writer            (general-purpose, sonnet)
    - design-synthesizer        (general-purpose, opus)
    - design-reviewer           (general-purpose, opus)
```

## Phase 흐름

### Phase 1: 팬아웃 — 3인 병렬 분석

3인에게 동시에 TaskCreate한다. 각자 **독립적으로** 분석하며 에이전트 간 직접 통신은 없다.

```
TaskCreate:
  - task: "요구사항 분석"
    assignee: requirements-analyst
    description: |
      사용자 요청: "{원문}"
      _workspace/{slug}/design_01_requirements.md 생성.
      스킬 requirements-extraction 참조.
      완료 후 파일만 남긴다. 다른 에이전트와 통신하지 않는다.
    deps: []

  - task: "영향 분석"
    assignee: impact-analyst
    description: |
      사용자 요청: "{원문}"
      _workspace/{slug}/design_02_impact.md 생성.
      스킬 impact-analysis 참조.
      완료 후 파일만 남긴다. 다른 에이전트와 통신하지 않는다.
    deps: []

  - task: "UX 설계"
    assignee: ux-spec-writer
    description: |
      사용자 요청: "{원문}"
      _workspace/{slug}/design_03_ux_spec.md 생성.
      스킬 tui-ux-spec 참조.
      완료 후 파일만 남긴다. 다른 에이전트와 통신하지 않는다.
    deps: []
```

**격리 원칙**: 3인은 서로의 산출물을 볼 수 없다. 상충·보완은 design-synthesizer가 파일을 읽어 종합할 때 처리한다.

3인 모두 완료 후 → `pipeline_status.md` 갱신: `current_phase: phase_1_5`, phase_1 체크박스 `[x]`.

### Phase 1.5: Feasibility 게이트 (오케스트레이터)

3인 산출물의 `feasibility` 필드를 확인한다. synthesizer 실행 전에 반드시 이 체크를 수행한다.

```
각 파일 최상단의 feasibility 값 확인:
  design_01_requirements.md → feasibility: ?
  design_02_impact.md       → feasibility: ?
  design_03_ux_spec.md      → feasibility: ?

1개라도 "impossible":
  - _workspace/{slug}/feasibility_block.md 작성
    - 불가 판정 에이전트 목록
    - 각 불가 사유
    - 제안된 대안 종합
  - 사용자에게 불가 사유와 대안을 직접 제시
  - 파이프라인 종료 (synthesizer 실행 안 함)

전부 "possible" 또는 "uncertain":
  - Phase 2(synthesizer)로 진행
  - "uncertain" 항목은 synthesizer에게 "결정 필요" 태그로 전달
```

Feasibility gate 통과 후 → `pipeline_status.md` 갱신: `current_phase: phase_2`, phase_1_5 체크박스 `[x]`.

### Phase 2: 팬인 — 종합

3인 완료 후 design-synthesizer가 종합한다.

```
TaskCreate:
  - task: "설계 종합"
    assignee: design-synthesizer
    description: |
      _workspace/{slug}/design_01_requirements.md
      _workspace/{slug}/design_02_impact.md
      _workspace/{slug}/design_03_ux_spec.md
      세 파일을 종합하여 _workspace/{slug}/design_spec.md 생성.
    deps: [task_01, task_02, task_03]
```

### Phase 2.5: 설계 리뷰 (design-reviewer)

synthesizer 완료 후 design-reviewer가 체크리스트 기반으로 `design_spec.md`를 검증한다.

- 입력: `design_01~03_*.md` + `design_spec.md`
- 출력: `_workspace/{slug}/design_review.md` (PASS / REJECT)
- 사용 스킬: `design-review-checklist`
- **작성자-검증자 분리**: design-synthesizer와 다른 인스턴스로 실행

**재시도 루프:**

```
iter 1 REJECT:
  - review에 명시된 수정 요청을 해당 에이전트에 SendMessage
  - 해당 에이전트(analyst/impact/ux/synthesizer)가 수정
  - design-reviewer 재실행 (iter 2)

iter 2 REJECT:
  - 리더가 사용자에게 에스컬레이션
  - _workspace/{slug}/design_review.md에 전체 iter 기록
```

PASS 시에만 Phase 3(사용자 승인)로 진행한다.

design_spec.md 생성 완료 → `pipeline_status.md` 갱신: `current_phase: phase_2_5`, phase_2 체크박스 `[x]`.  
design_review.md PASS → `pipeline_status.md` 갱신: `current_phase: phase_3`, phase_2_5 체크박스 `[x]`.

### Phase 3: 사용자 승인

리더가 리뷰 통과된 `design_spec.md`를 사용자에게 제시한다.

- **"결정 필요" 항목**이 있으면 사용자에게 판단을 요청한다
- 승인 시 → `pipeline_status.md`를 `status: completed`, phase_3 `[x]`로 갱신 → `agentlens-feature-pipeline`로 핸드오프 가능
- 수정 요청 시 → 해당 에이전트에 TaskCreate로 재작업 지시 → Phase 2.5부터 재실행

## 작업 의존성 DAG

```
task_01_requirements  (requirements-analyst)  → deps: []
task_02_impact        (impact-analyst)         → deps: []
task_03_ux_spec       (ux-spec-writer)         → deps: []
task_04_synthesis     (design-synthesizer)     → deps: [01, 02, 03]
task_05_review        (design-reviewer)        → deps: [04]
                                                 ↺ REJECT 시 task_01~04 해당 에이전트 재개
task_06_user_review   (leader)                 → deps: [05 PASS]
```

## 데이터 전달

| 채널 | 용도 |
|------|------|
| SendMessage | 리더 ↔ 에이전트 에스컬레이션 전용 (에이전트 간 직접 통신 금지) |
| TaskCreate/TaskUpdate | 진행상황, 의존성 관리 |
| _workspace/{slug}/ 파일 | 에이전트 간 유일한 데이터 교환 채널 |

파일 경로 규약:
```
_workspace/{slug}/
├── pipeline_status.md          (오케스트레이터, Phase 0 생성 → 각 Phase 완료 시 갱신)
├── design_01_requirements.md   (requirements-analyst)
├── design_02_impact.md         (impact-analyst)
├── design_03_ux_spec.md        (ux-spec-writer)
├── design_spec.md              (design-synthesizer)
└── design_review.md            (design-reviewer, 최종 게이트)
```

## 기존 파이프라인과의 연결

설계 완료 후 `agentlens-feature-pipeline`으로 핸드오프한다:

1. `design_spec.md`를 `_workspace/{slug}/`에 보존한다
2. feature-pipeline의 Phase 1(스키마 조사)은 design_spec.md의 영향 분석을 참조하여 조사 범위를 좁힌다
3. Phase 2(구현)의 각 에이전트는 design_spec.md의 구현 가이드를 따른다

## 스킵 조건

다음 경우에는 이 파이프라인을 스킵하고 바로 feature-pipeline을 실행한다:
- 단순 버그 수정 (원인과 해결책이 명확한 경우)
- CSS만 수정
- 문서만 갱신

## 에러 핸들링

| 에러 유형 | 전략 |
|-----------|------|
| 팀원 1인 실행 실패 | 1회 재시도, 재실패 시 나머지 산출물로 부분 종합 + 누락 명시 |
| 상충 데이터 | design-synthesizer가 양쪽 병기 + "결정 필요" 태그 |
| 사용자 요청 모호 | requirements-analyst가 모호성 목록 작성 → 리더가 사용자에 질문 |
| 구현 불가 판정 | 대안 접근법 제시 + 사용자에 에스컬레이션 |

## 테스트 시나리오

### 정상 흐름
> "Timeline에 이벤트 타입별 필터링 추가해줘"
1. requirements-analyst: AC 5개 도출, 모호성 1개 (지속성)
2. impact-analyst: timeline.py + app.py 수정, 위험도 "낮음"
3. ux-spec-writer: `f` 키바인딩, Input 컴포넌트, 와이어프레임
4. design-synthesizer: 종합 + 구현 순서 제안
5. 사용자 승인 → feature-pipeline 핸드오프

### 모호성 해소 필요
> "서브에이전트를 더 잘 보여줘"
1. requirements-analyst: 모호성 3개 ("더 잘"의 의미, 어떤 패널, 어떤 정보)
2. 리더가 사용자에 질문 → 응답 반영 → Phase 1 재실행
