---
name: graph-model-invariants
description: "agentlens 그래프 모델(graph_model.py, flowchart_layout.py) 수정 시 사용. 중첩 서브에이전트 depth≤5, sticky-running, 인스턴스 집계, MAX_NODES/BUFFER 캡, never-raise 파싱 원칙 등 불변식 체크리스트. 그래프 로직, 노드/엣지 모델, CallGraph, 레이아웃 변경 시 반드시 이 스킬을 사용할 것."
---

# Graph Model Invariants

`graph_model.py` 및 `flowchart_layout.py` 변경 시 반드시 유지해야 하는 불변식과 변경 전 체크리스트.

## 불변식 목록

### 1. MAX_NODES = 500
- 루트 노드(`main`)는 카운트에서 제외
- 초과 시 새 노드를 드롭하고 로그 경고
- 이 값을 올리면 adversarial JSONL에서 메모리 폭주 가능

### 2. MAX_NESTED_DEPTH = 5
- 루트는 level 0, 직접 자식은 level 1
- 이 depth를 초과하는 스폰은 드롭
- 변경 시 `flowchart_layout.py`의 인덱싱/들여쓰기 로직도 함께 수정해야 함

### 3. MAX_LABEL_LEN = 64
- `_sanitize_label()` 함수가 적용
- 비인쇄 문자, ANSI escape, `\r\n\t` 제거
- `oh-my-claudecode:` 등 공통 접두사는 라벨에서 제거 (`_LABEL_PREFIX_STRIPS`)

### 4. never-raise
- `graph_model.py`는 파서 출력의 어떤 shape에도 예외를 던지지 않는다
- 미지 이벤트 타입 → 무시 (로그만)
- 잘못된 payload 구조 → 기본값 사용 또는 무시

### 5. sticky-running
- 노드가 done 상태가 되어도 다음 **실제 user 프롬프트** 전까지 running(green) 표시 유지
- background task notifications, hook reminders, 서브에이전트 user rows는 flush 트리거에서 제외
- 이 로직 변경 시 `test_idle_footer.py`, `test_instance_view.py` 회귀 확인 필수

### 6. 모드별 인스턴스 뷰
- `[running]` 모드: 같은 타입의 병렬 인스턴스를 개별 박스로 분리 렌더링 (per-instance tool counts)
- `[all]` 모드: 단일 박스 + `(xN)` 카운터 + 합산 breakdown
- 이 로직 변경 시 `test_instance_view.py`, `test_subagent_aggregation.py` 회귀 확인 필수

## 변경 전 체크리스트

변경을 시작하기 전에 아래를 확인하라:

- [ ] 변경이 위 6개 불변식 중 어느 것에 영향을 주는가?
- [ ] 영향받는 불변식의 관련 테스트를 식별했는가?
- [ ] `Agent`와 `Task` 두 tool name 모두 서브에이전트 스폰으로 처리하는가?
- [ ] `Skill` tool name의 처리가 유지되는가?
- [ ] 일반 도구(Read/Edit/Bash)는 노드가 아닌 breakdown 카운터에만 반영하는가?
- [ ] 순환 엣지가 생성되지 않는가?

## 위반 시 깨지는 테스트

| 불변식 | 관련 테스트 |
|--------|-----------|
| MAX_NODES | `test_graph_model.py` |
| MAX_NESTED_DEPTH | `test_nested_spawn.py` |
| sticky-running | `test_idle_footer.py`, `test_instance_view.py` |
| 인스턴스 뷰 | `test_instance_view.py`, `test_subagent_aggregation.py` |
| never-raise | `test_parser.py`, `test_replay_real_slice.py` |
