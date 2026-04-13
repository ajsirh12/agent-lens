# agentlens — Project Instructions

## 하네스 라우팅

agentlens 코드 변경(기능 추가, 버그 수정, 리팩토링) 시 `.claude/skills/agentlens-feature-pipeline`을 우선 사용한다. OMC의 executor/autopilot/ralph/team으로 직접 위임하지 않는다.

개별 모듈 작업 시에는 해당 프로젝트 스킬(`jsonl-schema-probe`, `graph-model-invariants`, `watcher-portability`, `replay-fixture-harness`, `textual-panel-patterns`, `textual-async-testing`, `defensive-parsing`, `agentlens-release-notes`)을 우선 사용한다.

OMC 범용 스킬(autopilot, ralph, ultrawork, team 등)은 위 프로젝트 스킬이 해당하지 않는 작업에서만 사용한다.

## 환경

- Python 3.11+ | Textual ≥ 0.60 | watchfiles ≥ 0.21
- 테스트: `pytest tests/` (asyncio_mode = auto)
- 린트: `ruff check src/ tests/`
- 설치: `pip install -e ".[dev]"`

## 핵심 규칙

- `parser.py`는 어떤 입력에도 예외를 던지지 않는다 (never-raise, AC10)
- 캡 상수(`MAX_NODES`, `MAX_BUFFER_BYTES`, `MAX_RAW_LINE`, `MAX_PENDING`)를 임의 변경하지 않는다
- `docs/jsonl-schema-observed.md`가 JSONL 스키마의 유일한 진실 원천이다
