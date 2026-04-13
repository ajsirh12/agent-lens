---
name: textual-async-testing
description: "Textual 앱 테스트(App.run_test(), pilot.press(), pilot.click(), reactive 대기, 스냅샷 비교) 작성·실행 시 사용. agentlens의 tests/test_*.py 중 async 테스트를 쓰거나, pytest-asyncio 테스트를 수정하거나, UI 반응성/키바인딩 검증을 할 때 반드시 이 스킬을 사용할 것."
---

# Textual Async Testing

agentlens의 pytest-asyncio 기반 Textual UI 테스트 패턴.

## 기본 테스트 구조

```python
@pytest.mark.asyncio
async def test_something(tmp_path: Path) -> None:
    app = AgentlensApp(
        session_override=tmp_path / "empty.jsonl",
        state_dir_override=tmp_path / "state-absent",
    )
    (tmp_path / "empty.jsonl").write_text("")
    async with app.run_test() as pilot:
        await pilot.pause()
        # ... assertions ...
```

핵심: `session_override`와 `state_dir_override`로 격리된 환경을 만든다. 실제 `~/.claude/`에 접근하지 않는다.

## pilot API

### press (키 바인딩 테스트)
```python
await pilot.press("m")   # mode 토글
await pilot.press("o")   # orientation 토글
await pilot.press("S")   # Shift+S (path input modal)
await pilot.pause()       # UI 갱신 대기
```

### click (위젯 클릭)
```python
await pilot.click("#timeline-table")
await pilot.pause()
```

### pause (flush 대기)
Textual은 비동기 렌더링이므로, 상태 변경 후 assertion 전에 반드시 `await pilot.pause()`를 호출한다. 이를 빠뜨리면 아직 갱신되지 않은 상태를 읽게 된다.

## 비동기 대기 패턴

### 단순 대기
```python
await pilot.pause()  # 1 프레임 대기
```

### 여러 이벤트 처리 대기
```python
# 여러 이벤트를 연속 투입 후 한 번만 pause
for ev in events:
    timeline.add_event(ev)
await pilot.pause()
```

### 타임아웃 방어
테스트가 10분 이상 hang하면 환경 문제다. pytest 자체 timeout 또는 `asyncio.wait_for()`를 사용한다.

## 이벤트 생성 헬퍼

테스트에서 자주 사용하는 `_make_event()` 패턴:

```python
def _make_event(
    tool_name: str = "Bash",
    tool_use_id: str = "tid1",
    agent_id: str | None = None,
    inp: object = None,
) -> HarnessEvent:
    payload: dict = {"tool_name": tool_name, "tool_use_id": tool_use_id}
    if inp is not None:
        payload["input"] = inp
    return HarnessEvent(
        type=EventType.tool_use,
        ts=datetime.now(timezone.utc),
        agent_id=agent_id,
        payload=payload,
    )
```

## 실행 명령

```bash
# 전체 테스트
pytest tests/

# 특정 테스트
pytest tests/test_timeline.py -v

# 린트
ruff check src/ tests/
```

## 성공 기준

1. `pytest` 전체 green (0 failures, 0 errors)
2. `ruff check` clean (0 violations)

## 주의사항

- `asyncio_mode = "auto"` 설정이 `pyproject.toml`에 있다 — `@pytest.mark.asyncio`만 달면 된다
- `filterwarnings = ["ignore::DeprecationWarning"]` — DeprecationWarning은 무시된다
- `tmp_path` fixture로 격리 — 테스트 간 상태 공유 없음
