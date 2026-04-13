---
name: defensive-parsing
description: "스키마 관용 파싱, adversarial 입력 가드(MAX_BUFFER_BYTES, MAX_RAW_LINE), ANSI escape sanitize, never-raise 원칙. JSONL 파싱, 이벤트 처리 코드 수정, parser.py 변경, 입력 검증 로직 작업 시 반드시 이 스킬을 사용할 것."
---

# Defensive Parsing

agentlens의 스키마 관용 파싱 원칙과 adversarial 입력 방어.

## never-raise 원칙

`parser.py`는 **어떤 입력에도 예외를 던지지 않는다** (AC10). 모든 파싱 실패는 `EventType.unknown`으로 변환된다.

이유: agentlens는 실시간 TUI로, 예외 하나가 전체 UI를 멈추게 한다. Claude Code JSONL 스키마는 공식 계약이 아니므로 언제든 변할 수 있다. 파서가 새 필드/타입에 예외를 던지면 매 업데이트마다 깨진다.

```python
# 패턴: try/except로 감싸고 unknown 반환
try:
    row = json.loads(line)
    # ... 파싱 로직 ...
except Exception:
    yield HarnessEvent(type=EventType.unknown, ...)
```

## 캡 상수

| 상수 | 값 | 위치 | 목적 |
|------|-----|------|------|
| `MAX_RAW_LINE` | 8,192 bytes | `parser.py` | raw_line 저장 캡. 50MB 라인이 수천 row에 복제되면 multi-GB |
| `MAX_BUFFER_BYTES` | 1,048,576 (1 MiB) | `watcher.py` | 단일 라인 버퍼 캡. 초과 시 드롭 |
| `MAX_NODES` | 500 | `graph_model.py` | 노드 수 캡. adversarial JSONL 방어 |
| `MAX_LABEL_LEN` | 64 | `graph_model.py` | 라벨 길이 캡 |
| `MAX_PENDING` | 2,000 | `timeline.py` | pending tool_use 큐 캡 |

이 상수들은 메모리 폭주 방어용이다. 값을 올리거나 제거하면 adversarial 입력에 취약해진다. 변경 시 반드시 이유를 커밋 메시지에 남겨라.

## ANSI Sanitize

두 곳에서 독립적으로 수행:

### parser.py — `_truncate()`
```python
def _truncate(s: str) -> str:
    return s[:MAX_RAW_LINE]
```

### timeline.py — `_sanitize_cell()`
```python
def _sanitize_cell(s: object) -> str:
    text = str(s)
    text = "".join(c for c in text if (c.isprintable() or c == "\t") and c not in "\x1b\r")
    return text[:500]
```

### graph_model.py — `_sanitize_label()`
```python
def _sanitize_label(s: str) -> str:
    truncated = s[:MAX_LABEL_LEN]
    return "".join(c for c in truncated if c.isprintable() and c not in "\x1b\r\n\t")
```

세 함수 모두 ANSI escape(`\x1b`)를 제거한다. 새 출력 경로를 추가할 때 sanitize를 빠뜨리면 터미널 injection이 가능하다.

## 변경 시 체크리스트

- [ ] 새 파싱 경로에 try/except가 있는가?
- [ ] 실패 시 EventType.unknown을 반환하는가?
- [ ] 사용자 입력(label, path 등)에 sanitize가 적용되는가?
- [ ] 캡 상수가 유지되는가?
- [ ] `test_replay_real_slice.py`가 여전히 pass하는가?
