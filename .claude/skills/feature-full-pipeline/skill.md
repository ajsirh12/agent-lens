---
name: feature-full-pipeline
description: "설계부터 구현까지 한 번에 실행하는 통합 오케스트레이터. feature-design-pipeline(설계+리뷰)→agentlens-feature-pipeline(구현+리뷰)을 사용자 승인 없이 순차 실행한다. '처음부터 끝까지 해줘', '설계하고 바로 구현까지', 'end-to-end', '풀 파이프라인', '전부 자동으로 해줘' 등 설계+구현 통합 요청 시 반드시 이 스킬을 사용할 것. 설계만 요청('설계해줘') 또는 구현만 요청('구현해줘')에는 각각의 전용 파이프라인을 사용할 것."
---

# Feature Full Pipeline — End-to-End Orchestrator

설계와 구현을 한 번에 실행한다. 두 기존 파이프라인을 순차로 호출한다. 사용자 승인 게이트 없이 설계 완료 즉시 구현으로 진행한다.

## Phase 0: Slug 생성

사용자 요청에서 slug를 생성한다. 이후 모든 산출물은 `_workspace/{slug}/` 하위에 저장된다. 두 파이프라인이 같은 slug를 공유한다.

**slug 규칙:**
- kebab-case, 영문 소문자 + 하이픈, 최대 30자
- 동일 slug 존재 시 숫자 접미사 (`-2`, `-3`)

## Phase 1: 설계 파이프라인 실행

`feature-design-pipeline` 스킬을 호출한다. 해당 파이프라인 내부에서:
- 3인 병렬 분석 (requirements / impact / ux)
- design-synthesizer 종합
- design-reviewer 리뷰 (PASS까지 최대 2회 iter)

완료 시 `_workspace/{slug}/design_spec.md` + `design_review.md (verdict: pass)`가 존재한다.

## Phase 2: 설계 → 구현 자동 전환

`design_spec.md`가 생성되고 `design_review.md`가 PASS이면 즉시 Phase 3으로 진행한다. 사용자 확인 없음.

**단, 다음 경우에만 일시 중단하여 사용자에게 질문한다:**
- `design_spec.md`에 "결정 필요" 항목이 있는 경우 (임의 선택 금지)

## Phase 3: 구현 파이프라인 실행

`agentlens-feature-pipeline` 스킬을 호출한다. 해당 파이프라인 내부에서:
- 스키마 조사 → 교차 검증
- 3인 병렬 구현
- QA 2인 (fixture-replay-qa + textual-test-engineer)
- code-reviewer 리뷰 (PASS까지 최대 2회 iter)
- 문서화 (release-doc-writer)

**중요:** feature-pipeline의 Phase 1(스키마 조사)는 `design_spec.md`의 영향 분석을 참조하여 조사 범위를 좁힌다. 구현 에이전트는 `design_spec.md`를 반드시 읽고 작업한다.

## Phase 4: 최종 요약

완료 후 사용자에게 다음을 제시한다:
- 설계 요약 (`design_spec.md` 링크)
- 구현 diff 요약
- QA 결과 (pytest/ruff pass)
- 코드 리뷰 결과 (`code_review.md`)
- 갱신된 문서 (CHANGELOG 등)

## 작업 의존성 DAG

```
task_00_slug           (leader)                      → deps: []
task_01_design         (feature-design-pipeline)     → deps: [task_00]
task_02_auto_gate      (leader)                      → deps: [task_01] — "결정 필요" 있으면 일시 중단, 없으면 즉시 통과
task_03_implement      (agentlens-feature-pipeline)  → deps: [task_02]
task_04_summary        (leader)                      → deps: [task_03]
```

## 에러 핸들링

| 상황 | 전략 |
|------|------|
| 설계 리뷰 iter 2회 초과 | 설계 파이프라인에서 에스컬레이션 → 사용자 개입 |
| 사용자 rejection | 종료, `user_rejection.md` 기록, Phase 3 실행 안 함 |
| 구현 리뷰 iter 2회 초과 | 구현 파이프라인에서 에스컬레이션 → 사용자 개입 |
| 설계 통과 후 구현 중 설계 결함 발견 | 즉시 중단, `design_defect.md` 기록, Phase 1로 복귀 (재설계) |

## 스킵 조건

다음 경우 해당 Phase 스킵:
- **Phase 1 설계 스킵**: 단순 버그 수정(원인 명확), CSS만 수정 → 바로 Phase 3
- **Phase 2 게이트 스킵**: 사용자가 "자동으로" 명시 + "결정 필요" 항목 없음

## 테스트 시나리오

### 정상 흐름
> "Timeline에 필터링 기능 추가해서 끝까지 완성해줘"
1. slug: `timeline-filter`
2. Phase 1: 설계 3인 → synthesizer → design-reviewer PASS
3. Phase 2: 사용자 승인 (결정 필요 1개 질문 후 approve)
4. Phase 3: tui-panel-engineer 구현 → QA PASS → code-reviewer PASS
5. Phase 4: doc-writer가 CHANGELOG/README 갱신 → 요약 제시

### 설계 rejection
> "이거 처음부터 끝까지 해줘: 서브에이전트 depth 10 지원"
1. Phase 1: design-reviewer가 depth≤5 불변식 위반 탐지 → REJECT iter 1 → REJECT iter 2 → 에스컬레이션
2. 사용자에 "depth 제약을 변경할지" 질의
3. 사용자 응답 반영 후 재개

### 자동 모드
> "CSS만 살짝 고쳐서 끝까지 --auto"
1. Phase 1 설계 스킵 (CSS만 수정)
2. Phase 2 게이트 스킵 (auto + 결정 필요 없음)
3. Phase 3: panel-engineer가 CSS 수정 → QA → code-reviewer PASS
4. Phase 4: 요약

## 데이터 전달

| 채널 | 용도 |
|------|------|
| 파일 (`_workspace/{slug}/`) | 설계→구현 간 유일한 공유 채널 |
| 사용자 게이트 | Phase 2의 직접 소통 |

두 파이프라인은 `slug`를 공유함으로써 연결된다. 내부 팀 구성은 각 파이프라인에서 독립적으로 관리한다 (팀 해체 후 재구성).
