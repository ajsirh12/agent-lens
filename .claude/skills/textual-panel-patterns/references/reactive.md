# Reactive Patterns

## 기본 사용

```python
from textual.reactive import reactive

class MyWidget(Widget):
    mode: reactive[str] = reactive("all")

    def watch_mode(self, value: str) -> None:
        """mode 변경 시 자동 호출."""
        self._rebuild()
```

## 주의사항

- `reactive`는 `__init__` 이전에 선언한다 (클래스 변수)
- `watch_*` 메서드는 mount 이후에만 호출된다 — compose 중에는 동작하지 않음
- 여러 reactive가 동시에 바뀌면 각각의 watch가 개별 호출된다

## agentlens에서의 사용

- `app.py`의 mode/orientation/panes 토글이 reactive
- 패널은 app의 reactive 변경을 감지하여 자체 리빌드
