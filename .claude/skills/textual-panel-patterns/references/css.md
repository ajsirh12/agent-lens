# CSS Patterns

## 파일 규칙

- **단일 파일**: `src/agentlens/app.tcss`
- 위젯의 `DEFAULT_CSS = ""`는 비워둔다 (인라인 CSS 지양)

## Scope 셀렉터

```css
/* 위젯별 scope */
TimelinePanel > DataTable {
    height: 1fr;
}

/* 클래스 기반 상태 */
FlowchartPanel .node-running {
    color: green;
}

FlowchartPanel .node-done {
    color: white;
}
```

## 클래스 토글

```python
# 인라인 style 금지. 클래스 토글 사용:
node_widget.add_class("node-running")
node_widget.remove_class("node-done")
```

## 레이아웃

- Horizontal/Vertical 토글: `p` 키로 전환
- `app.tcss`에서 `.horizontal-layout` / `.vertical-layout` 클래스로 분기
