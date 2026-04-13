# Modal Patterns

## 모달 생성

Screen 기반 모달을 사용한다:

```python
from textual.screen import Screen

class DetailModal(Screen):
    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Container(...)

    def action_dismiss(self) -> None:
        self.app.pop_screen()
```

## 모달 호출

```python
# app.py 또는 패널에서:
self.app.push_screen(DetailModal(data=...))
```

## 패턴: Input 모달 (Shift+S)

`session_path_input.py` 참조:
- `Input` 위젯으로 경로/세션 ID 입력
- `on_input_submitted` 이벤트에서 값 처리
- glob 해석 로직은 모달 내부에서 처리

## 파일 규칙

- 모달 1개 = 파일 1개 (`panels/` 하위)
- 모달 CSS는 `app.tcss`에 위임 (인라인 CSS 비움)
