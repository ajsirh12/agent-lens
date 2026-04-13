# DataTable Patterns

## 기본 설정

```python
table.cursor_type = "row"
table.zebra_stripes = True
```

## 컬럼 구성 (Timeline)

```python
self._table.add_columns("ts", "tool", "agent", "status", "dur_ms")
```

## 셀 값 삽입

모든 셀 값은 `_sanitize_cell()`을 거친다:
```python
def _sanitize_cell(s: object) -> str:
    text = str(s)
    text = "".join(c for c in text if (c.isprintable() or c == "\t") and c not in "\x1b\r")
    return text[:500]
```

## Row 추가 패턴

```python
row_key = self._table.add_row(ts_str, tool_name, agent_label, status, dur_str)
self._row_agent[row_key] = agent_id
```

## Cross-highlight

Timeline에서 row 선택 → `post_message`로 agent_id 전달 → Flowchart에서 해당 노드 하이라이트:

1. Timeline: `on_data_table_row_highlighted` 이벤트 핸들러에서 `self.post_message(HighlightAgent(agent_id))`
2. App: 메시지 핸들러에서 Flowchart에 전달
3. Flowchart: 해당 노드에 `.highlighted` CSS 클래스 토글

## Scroll 관리

```python
# 한 프레임에 여러 이벤트 → 1회만 스크롤
self._scroll_pending = True
# Textual refresh tick에서:
if self._scroll_pending:
    self._table.move_cursor(row=self._row_count - 1)
    self._scroll_pending = False
```
