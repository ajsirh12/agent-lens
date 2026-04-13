---
name: impact-analysis
description: "agentlens 코드베이스에서 기능 변경의 영향 범위를 분석하는 스킬. 변경 대상 파일, 인터페이스 변경, 의존성 체인, 위험도 평가 작업 시 사용할 것."
---

# Impact Analysis

기능 변경이 agentlens 코드베이스에 미치는 영향을 체계적으로 분석한다.

## 모듈 의존성 맵

분석 시 이 의존성 방향을 기준으로 영향 전파를 추적한다:

```
events.py (이벤트 타입 정의)
    ↓
parser.py (JSONL → HarnessEvent)
    ↓
graph_model.py (CallGraph, 노드/엣지)
    ↓
flowchart_layout.py (그래프 → ASCII)
    ↓
panels/flowchart.py (시각화)

watcher.py ──→ parser.py
subagent_watcher.py ──→ parser.py
locator.py ──→ watcher.py
subagent_locator.py ──→ subagent_watcher.py

app.py ──→ panels/* (조합)
bus.py ──→ panels/* (이벤트 버스)
messages.py ──→ panels/* (Textual 메시지)
```

## 분석 프로세스

### Step 1: 진입점 식별

요구사항에서 직접 변경이 필요한 파일/함수를 식별한다. 코드를 실제로 읽고 확인한다 (추측 금지).

### Step 2: 전파 추적

진입점에서 의존성 맵을 따라 영향이 전파되는 경로를 추적한다:

- **상향 전파**: 이 파일을 import하는 파일들
- **하향 전파**: 이 파일이 import하는 파일들 중 인터페이스가 변경되는 것
- **횡단 전파**: bus.py/messages.py를 통한 이벤트 기반 연결

### Step 3: 변경 분류

각 영향받는 파일에 대해:

| 분류 | 설명 |
|------|------|
| 직접 수정 | 코드 변경이 필요한 파일 |
| 인터페이스 적응 | 시그니처/데이터 모델 변경에 맞춰 수정 필요 |
| 테스트 갱신 | 기존 테스트가 깨지거나 새 테스트 필요 |
| 확인만 | 영향 없음을 확인해야 하는 파일 |

### Step 4: 위험도 평가

| 위험도 | 기준 |
|--------|------|
| **높음** | 캡 상수/불변식 변경, parser.py never-raise 영향, 3+ 모듈 인터페이스 변경 |
| **중간** | 2개 모듈 변경, 기존 테스트 수정 필요, 새 이벤트 타입 추가 |
| **낮음** | 단일 파일 변경, 기존 인터페이스 유지, CSS만 수정 |

## 불변식 체크리스트

영향 분석 시 반드시 확인하는 항목:

- [ ] `parser.py` never-raise 원칙이 유지되는가
- [ ] `MAX_NODES`, `MAX_BUFFER_BYTES`, `MAX_RAW_LINE`, `MAX_PENDING` 변경이 필요한가
- [ ] 서브에이전트 depth≤5 제약에 영향이 있는가
- [ ] sticky-running 로직에 영향이 있는가
- [ ] `docs/jsonl-schema-observed.md` 갱신이 필요한가

## 산출물 템플릿

```markdown
# Impact Analysis: {기능 제목}

## 변경 대상

| 파일 | 변경 유형 | 위험도 | 사유 |
|------|----------|--------|------|
| {path} | 직접 수정 | 중간 | {사유} |

## 인터페이스 변경

### {파일명}
- Before: `def foo(a: str) -> Event`
- After: `def foo(a: str, b: int = 0) -> Event`
- 영향: {이 변경으로 수정 필요한 호출처}

## 의존성 전파 체인
{진입점} → {파일A} → {파일B}

## 불변식 영향
- never-raise: {영향 없음 / 주의 필요}
- 캡 상수: {변경 불필요 / 변경 필요 + 사유}

## 영향받는 테스트
- {test_file.py::test_name} — 사유: {왜 깨지는지}

## 위험도 요약
전체 위험도: {높/중/낮} — {한줄 사유}
```
