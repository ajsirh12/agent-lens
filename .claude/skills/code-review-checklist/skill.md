---
name: code-review-checklist
description: "구현 diff를 design_spec.md와 대조하고 agentlens 코드 품질 기준(never-raise, 불변식, Textual 패턴, CLAUDE.md 규칙)을 체크리스트로 검증하는 스킬. code-reviewer 에이전트가 사용."
---

# Code Review Checklist

구현 diff가 `design_spec.md`를 따르고 agentlens 품질 기준을 만족하는지 기계적으로 검증한다.

## 리뷰 프로세스

### Step 1: AC 커버리지 매핑

`design_spec.md`의 각 AC를 읽고, 구현된 파일/라인이 그 AC를 어디서 충족하는지 매핑한다:

```
AC1 → src/agentlens/panels/timeline.py:L120-L135 (필터 입력 처리)
AC2 → src/agentlens/app.py:L45 (키바인딩 `f`)
AC3 → (미구현) ← **fail**
```

AC 1개라도 미구현이면 REJECT.

### Step 2: agentlens 불변식 체크

변경된 파일에 따라 해당 불변식을 검증한다:

**parser.py 수정 시:**
- [ ] `raise` 추가되지 않음 (never-raise 원칙)
- [ ] 예외 처리는 `try/except` + 로깅으로 흡수
- [ ] `MAX_BUFFER_BYTES`, `MAX_RAW_LINE` 가드 유지

**graph_model.py / flowchart_layout.py 수정 시:**
- [ ] `MAX_NODES` 캡 유지
- [ ] 서브에이전트 depth 제약 (≤5) 유지 또는 사유 명시된 변경
- [ ] sticky-running 로직 깨지지 않음

**panels/* 수정 시:**
- [ ] `_sanitize_cell()` 우회 없음
- [ ] `MAX_PENDING = 2000` 유지
- [ ] app.tcss 단일 CSS 파일 원칙 유지 (인라인 CSS 금지)
- [ ] DataTable cross-highlight 패턴 일관성

**watcher.py / subagent_watcher.py 수정 시:**
- [ ] watchfiles + polling fallback 유지
- [ ] Windows/git-bash 경로 호환 깨지지 않음

### Step 3: CLAUDE.md 규칙 준수

- [ ] 캡 상수(`MAX_NODES`, `MAX_BUFFER_BYTES`, `MAX_RAW_LINE`, `MAX_PENDING`) 임의 변경 없음
- [ ] `docs/jsonl-schema-observed.md`가 변경되었다면 파서 변경과 동기화되어 있음
- [ ] parser.py never-raise 원칙 준수

### Step 4: 코드 품질 체크 (일반)

- [ ] **SRP**: 한 함수가 여러 책임을 갖지 않음
- [ ] **중복**: 3회 이상 반복된 로직은 추출됨 (2회까지는 OK)
- [ ] **네이밍**: 함수/변수명이 의도를 반영
- [ ] **불필요한 추상화 없음**: 1회만 쓰는 헬퍼·유틸 없음
- [ ] **에러 핸들링**: 시스템 경계(외부 입력, 파일 I/O)에서만 validation, 내부 호출에는 과잉 방어 없음
- [ ] **comment 품질**: 자명한 내용이 아닌, "왜"를 설명하는 주석

### Step 5: 테스트 동기화 체크

- [ ] 신규 기능에 대응하는 테스트 파일이 `tests/` 또는 `tests/fixtures/`에 존재
- [ ] 기존 테스트 수정 시 사유 명확
- [ ] fixture 자체가 수정되었으면 REJECT (fixture 수정 금지 원칙)

## 판정 규칙

| 조건 | 판정 |
|------|------|
| AC 전부 커버 + 모든 불변식 통과 + CLAUDE.md 준수 | PASS |
| AC 1개 이상 미구현 | REJECT |
| 불변식 위반 | REJECT (사유 명시) |
| 코드 품질 이슈만 | REJECT (경미하면 경고 + PASS 가능) |

## 산출물 포맷

`_workspace/{slug}/code_review.md`:

```markdown
# Code Review: {slug}

## 판정
- [x] PASS / [ ] REJECT
- iter: {N}

## AC 커버리지
| AC | 구현 위치 | 상태 |
|----|----------|------|
| AC1 | timeline.py:L120 | 완료 |
| AC2 | app.py:L45 | 완료 |
| AC3 | — | **미구현** |

## 불변식 체크
- [x] never-raise (parser.py)
- [x] MAX_NODES 유지
- [ ] MAX_PENDING ← **fail**: 2000 → 5000 변경됨 (사유 불명)

## CLAUDE.md 준수
- [x] 캡 상수 변경 없음 (해당 없음)

## 코드 품질
- [x] SRP
- [ ] 중복 ← **warn**: timeline.py에 유사 로직 3회

## REJECT 사유 (REJECT 시에만)
1. **AC3 미구현**
   - 파일: (없음)
   - 수정 요청: tui-panel-engineer가 필터 해제(`Escape`) 핸들러 추가

2. **MAX_PENDING 임의 변경**
   - 파일: timeline.py:L22
   - 수정 요청: 원복 또는 사유를 02_panel_changes.md에 명시

## 수정 요청
- to: `tui-panel-engineer`
- 재작업 범위: AC3 구현 + MAX_PENDING 원복
```

## 일반화 원칙

- agentlens 특화 규칙(never-raise, 캡 상수, Textual 패턴)에 집중
- 일반 코드 스타일 취향(따옴표, 들여쓰기 선호 등)은 리뷰 대상 아님 (ruff가 처리)
- "이렇게 짜면 더 예쁠 텐데" 같은 의견 금지 — 체크리스트에 있는 것만 체크
