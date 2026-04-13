---
name: feature-design-pipeline
description: "자연어 기능 요청을 구조화된 설계 문서로 변환하는 오케스트레이터. 요구사항 분석 → 영향 분석 → UX 설계를 3인 병렬로 실행한 뒤 종합하여 설계 문서를 생성한다. '기능 설계해줘', '설계 먼저 해줘', '어떻게 만들면 좋을지 분석해줘', '구현 전에 설계부터', '요구사항 정리해줘', '영향 분석해줘' 등 구현 전 설계·분석 요청 시 반드시 이 스킬을 사용할 것. 구현 자체를 요청하는 경우('구현해줘', '코드 변경', '버그 수정')에는 이 스킬이 아닌 agentlens-feature-pipeline을 사용할 것."
---

# Feature Design Pipeline — Orchestrator

자연어 기능 요청을 받아 3인 병렬 분석 → 1인 종합으로 설계 문서를 생성한다.

## 팀 구성

```
TeamCreate:
  team_name: agentlens-design-team
  members:
    - requirements-analyst      (general-purpose, opus)
    - impact-analyst            (general-purpose, opus)
    - ux-spec-writer            (general-purpose, opus)
    - design-synthesizer        (general-purpose, opus)
```

## Phase 흐름

### Phase 1: 팬아웃 — 3인 병렬 분석

3인에게 동시에 TaskCreate한다. 각자 독립적으로 분석하되 SendMessage로 힌트를 교환한다.

```
TaskCreate:
  - task: "요구사항 분석"
    assignee: requirements-analyst
    description: |
      사용자 요청: "{원문}"
      _workspace/design_01_requirements.md 생성.
      스킬 requirements-extraction 참조.
    deps: []

  - task: "영향 분석"
    assignee: impact-analyst
    description: |
      사용자 요청: "{원문}"
      _workspace/design_02_impact.md 생성.
      스킬 impact-analysis 참조.
    deps: []

  - task: "UX 설계"
    assignee: ux-spec-writer
    description: |
      사용자 요청: "{원문}"
      _workspace/design_03_ux_spec.md 생성.
      스킬 tui-ux-spec 참조.
    deps: []
```

**팀원 간 통신 (자율):**
- requirements-analyst → impact-analyst: 특정 모듈 영향 예상 힌트
- requirements-analyst → ux-spec-writer: UX 관련 요구사항 강조
- impact-analyst → ux-spec-writer: 패널/위젯 영향 분석 결과
- impact-analyst → requirements-analyst: 기술 제약으로 요구사항 조정 필요 시
- ux-spec-writer → requirements-analyst: UX 관점 요구사항 충돌 발견 시

### Phase 2: 팬인 — 종합

3인 완료 후 design-synthesizer가 종합한다.

```
TaskCreate:
  - task: "설계 종합"
    assignee: design-synthesizer
    description: |
      _workspace/design_01_requirements.md
      _workspace/design_02_impact.md
      _workspace/design_03_ux_spec.md
      세 파일을 종합하여 _workspace/design_spec.md 생성.
    deps: [task_01, task_02, task_03]
```

### Phase 3: 사용자 승인

리더가 `design_spec.md`를 사용자에게 제시한다.

- **"결정 필요" 항목**이 있으면 사용자에게 판단을 요청한다
- 승인 시 → `agentlens-feature-pipeline`로 핸드오프 가능
- 수정 요청 시 → 해당 에이전트에 TaskCreate로 재작업 지시

## 작업 의존성 DAG

```
task_01_requirements  (requirements-analyst)  → deps: []
task_02_impact        (impact-analyst)         → deps: []
task_03_ux_spec       (ux-spec-writer)         → deps: []
task_04_synthesis     (design-synthesizer)     → deps: [01, 02, 03]
task_05_user_review   (leader)                → deps: [04]
```

## 데이터 전달

| 채널 | 용도 |
|------|------|
| SendMessage | 팀원 간 힌트 교환 (실시간, 1~3줄) |
| TaskCreate/TaskUpdate | 진행상황, 의존성 관리 |
| _workspace/ 파일 | 구조화된 산출물 |

파일 경로 규약:
```
.claude/_workspace/
├── design_01_requirements.md   (requirements-analyst)
├── design_02_impact.md         (impact-analyst)
├── design_03_ux_spec.md        (ux-spec-writer)
└── design_spec.md              (design-synthesizer, 최종)
```

## 기존 파이프라인과의 연결

설계 완료 후 `agentlens-feature-pipeline`으로 핸드오프한다:

1. `design_spec.md`를 `_workspace/`에 보존한다
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
