---
name: design-review-checklist
description: "design_spec.md를 체크리스트 기반으로 검증하는 스킬. 완성도, 3인 산출물 간 일관성, 간과된 리스크를 기계적으로 판정하여 PASS/REJECT를 내린다. design-reviewer 에이전트가 사용."
---

# Design Review Checklist

`design_spec.md`를 기계적으로 검증하여 PASS/REJECT 판정을 내린다.

## 리뷰 프로세스

### Step 1: 완성도 체크리스트

`design_spec.md`에 다음 섹션이 모두 존재하고 비어있지 않은지 확인한다:

- [ ] 기능 요약 (1~2문장)
- [ ] 요구사항 (AC 포함)
- [ ] 영향 분석 요약 (변경 대상 파일 목록)
- [ ] UX 설계 (와이어프레임 또는 "해당 없음" 명시)
- [ ] 트레이드오프 (선택지별 장단점)
- [ ] 결정 필요 항목 (없으면 "없음" 명시)
- [ ] 구현 가이드 (순서 + 에이전트 매핑)
- [ ] 위험도 요약

각 섹션이 placeholder("TBD", "추후") 없이 채워져 있어야 한다.

### Step 2: AC 검증 가능성 체크

모든 AC가 검증 가능한 형태인지 확인한다:

- [ ] "Given/When/Then" 구조 또는 이와 동등한 검증 가능 형태
- [ ] "~할 수 있다" 같은 모호한 표현 없음
- [ ] 검증 주체가 명확 (어느 파일/테스트가 확인)

### Step 3: 3인 산출물 간 일관성

`design_01_requirements.md`, `design_02_impact.md`, `design_03_ux_spec.md`와 `design_spec.md` 간:

- [ ] requirements의 모든 REQ가 design_spec의 요구사항에 포함됨
- [ ] impact의 변경 파일이 design_spec의 구현 가이드와 일치
- [ ] ux_spec의 키바인딩이 design_spec의 UX 섹션에 반영됨
- [ ] 요구사항에서 언급된 모호성이 "결정 필요" 항목 또는 AC로 해소됨

### Step 4: 간과된 리스크 체크 (agentlens 특화)

- [ ] `parser.py` 수정이 포함되면 never-raise 원칙 언급이 있는가
- [ ] 캡 상수(MAX_NODES, MAX_BUFFER_BYTES, MAX_RAW_LINE, MAX_PENDING) 변경이 필요하면 사유 명시됨
- [ ] 서브에이전트 depth≤5 제약 영향 검토됨 (해당되면)
- [ ] 키바인딩 추가가 있으면 충돌 확인 명시됨
- [ ] 새 이벤트 타입 추가가 있으면 `docs/jsonl-schema-observed.md` 갱신 계획 있음

### Step 5: 실행 가능성 체크

- [ ] 구현 가이드가 구현 에이전트가 바로 작업할 수 있을 정도로 구체적
- [ ] 영향 분석의 위험도와 구현 순서가 일관됨 (높음 위험 먼저 또는 격리)
- [ ] 상충하는 권고사항은 "결정 필요"로 표시됨 (임의 삭제 금지)

## 판정 규칙

| 조건 | 판정 |
|------|------|
| 모든 체크 통과 | PASS |
| 1개 이상 fail | REJECT |
| 정보 부족으로 판정 불가능 | REJECT + "판정 불가" 사유 |

## 산출물 포맷

`_workspace/{slug}/design_review.md`:

```markdown
# Design Review: {slug}

## 판정
- [x] PASS / [ ] REJECT
- iter: {N}

## 체크리스트
### 완성도
- [x] 기능 요약
- [x] 요구사항 (AC 포함)
- [ ] 영향 분석 ← **fail**: 변경 파일 목록 없음

### AC 검증 가능성
- [x] Given/When/Then 구조

### 일관성
- [x] requirements ↔ design_spec

### 리스크
- [x] never-raise 원칙

### 실행 가능성
- [x] 구현 가이드 구체성

## REJECT 사유 (REJECT 시에만)
1. **영향 분석 누락**
   - 항목: "영향 분석 요약"
   - 문제: 변경 대상 파일 목록이 "TBD"로 남아있음
   - 수정 요청: design-synthesizer가 design_02_impact.md를 읽고 파일 목록을 채울 것

## 수정 요청 (REJECT 시에만)
- to: `design-synthesizer`
- 재작업 범위: 영향 분석 섹션만
```

## 일반화 원칙

- 특정 기능(예: "타임라인 필터링")에만 맞는 규칙을 만들지 않는다
- agentlens 코어 불변식(never-raise, 캡 상수, depth)에 한정해서 검증한다
- 스타일·취향은 리뷰 대상이 아니다
