---
name: textual-panel-patterns
description: "Textual 패널 구현·수정 시 사용. DataTable cross-highlight, reactive 바인딩, Modal 등록/해제, CSS 스코프, 키 바인딩 패턴. agentlens의 panels/, app.py, app.tcss를 작업하거나, Textual 위젯/UI 관련 변경을 할 때 반드시 이 스킬을 사용할 것."
---

# Textual Panel Patterns

agentlens TUI의 패널 구현에 사용하는 Textual 패턴 가이드.

## 파일 구조 원칙

- 패널: `src/agentlens/panels/` 하위, 파일 1개 = 위젯 1개
- CSS: `src/agentlens/app.tcss` 단일 파일. 인라인 `DEFAULT_CSS = ""` 비움 패턴 유지
- 앱 루트: `src/agentlens/app.py` — compose, key bindings, 패널 조합

## 핵심 패턴

### DataTable
- `cursor_type = "row"` + `zebra_stripes = True`가 agentlens 기본
- 셀 값은 반드시 `_sanitize_cell()`을 거쳐 ANSI escape/비인쇄 문자 제거
- `MAX_PENDING` 캡으로 pending 큐 크기 제한
- 상세 패턴은 `references/datatable.md` 참조

### Cross-highlight
- Timeline ↔ Flowchart 양방향 이벤트: 한쪽 선택 시 다른쪽 하이라이트
- Textual message 시스템 사용 (`post_message`)
- 상세 패턴은 `references/datatable.md`의 cross-highlight 섹션 참조

### Modal
- `Screen` 기반 모달: `app.push_screen()` / `app.pop_screen()`
- 모달은 `panels/` 하위 개별 파일 (예: `detail_modal.py`, `session_path_input.py`)
- 상세 패턴은 `references/modal.md` 참조

### Reactive
- `reactive()` 디스크립터로 상태 바인딩
- `watch_*` 메서드로 변경 시 UI 갱신
- 상세 패턴은 `references/reactive.md` 참조

### CSS
- 위젯별 scope: `TimelinePanel > DataTable { ... }` 형태
- 클래스 토글: `.add_class()` / `.remove_class()` (인라인 style 금지)
- 상세 패턴은 `references/css.md` 참조

### Key Bindings
- `app.py`의 `BINDINGS` 리스트에 등록
- 토글 키: `m`(mode), `o`(orientation), `p`(panes)
- 모달 키: `d`(detail), `s`(session switch), `S`(path input)
- 새 키 추가 시 기존 키와 충돌 확인 필수

## 방어적 코딩

- 위젯 미초기화: `assert self._table is not None` 또는 `if self._table is None: return`
- scroll_pending 플래그: 여러 add_event가 한 프레임에 들어와도 1회만 스크롤
- `_updating` 플래그: 재진입 방지
