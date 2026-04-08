# harness-visual 사용방법

## 1. 설치

Python 3.11+ 필요 (macOS 시스템 Python은 3.9이므로 Homebrew Python 3.12 권장):

```bash
cd /Users/limdk/Documents/workspace/harness-visual

# 가상환경 생성 + 의존성 설치
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

설치 후 `harness-visual` 명령이 PATH에 등록됩니다 (venv 활성화 상태).

---

## 2. 실행

### 기본 사용 (자동 attach)
```bash
harness-visual
```
- 현재 작업 디렉토리(`pwd`)에 해당하는 Claude Code 세션을 자동 탐색.
- slug 규칙: `/Users/limdk/foo` → `~/.claude/projects/-Users-limdk-foo/*.jsonl`
- 같은 프로젝트에 **세션이 2개 이상**이면 시작 시 picker 모달이 떠서 mtime/크기/파일명을 보고 ↑/↓ + Enter 로 선택.
- 세션이 1개면 자동 attach. slug 디렉토리가 없으면 **모든 프로젝트에서 가장 최근에 수정된 JSONL** 로 fallback.

### Picker 건너뛰고 가장 최근 세션 자동 선택
```bash
harness-visual --latest
```

### 특정 세션 파일 지정
```bash
harness-visual --session ~/.claude/projects/-Users-limdk-Documents-workspace-harness-visual/b0709256-eb61-4ccb-9b57-49aaca263c33.jsonl
```

### 다른 프로젝트 기준으로 slug 계산
```bash
harness-visual --project-root /Users/limdk/Documents/workspace/other-project
```

### attach 없이 빈 화면으로 시작
```bash
harness-visual --no-attach
```

### CI smoke test (한 번 렌더 후 exit 0)
```bash
harness-visual --self-test
```

### OMC state 디렉토리 변경
```bash
harness-visual --state-dir /path/to/.omc/state
```

### Verbose
```bash
harness-visual -v
```

---

## 3. 키 바인딩

| Key | Action |
|---|---|
| `q` | 종료 |
| `j` / `↓` | Timeline 커서 아래로 |
| `k` / `↑` | Timeline 커서 위로 |
| `Enter` | 선택한 tool call의 상세 모달 열기 (4 필드: tool name / status / duration / input 요약) |

Timeline에서 행을 선택하면 → AgentTree의 해당 agent 노드가 자동 하이라이트.
AgentTree에서 노드를 선택하면 → Timeline의 해당 agent 가장 최근 row로 자동 스크롤.
(양방향 cross-highlight, `_updating` 가드로 무한 루프 방지)

---

## 4. 화면 구성

```
┌──────────────────────────────────────────────────────────────────┐
│ Timeline                       │ Agent Tree                      │
│ ─────────────────────────────  │ ──────────────────────────────  │
│ ts       tool      agent       │ ▼ main                          │
│          status  dur(ms)       │   ├─ executor (running)         │
│ 14:02:01 Read     main         │   ├─ explorer (done)            │
│          ✓       12            │   └─ critic (done)              │
│ 14:02:03 Bash     main         │                                 │
│          ✓       340           │                                 │
│ 14:02:05 Edit     executor     │                                 │
│          ✓       45            │                                 │
│                                │                                 │
├──────────────────────────────────────────────────────────────────┤
│ session: ...b0709256-...jsonl  | events: 47 | last: 2s ago       │
└──────────────────────────────────────────────────────────────────┘
```

footer는 1Hz로 갱신되며 30초 이상 이벤트가 없으면 `session idle` 표시.

---

## 5. 환경변수

| 변수 | 효과 |
|---|---|
| `HARNESS_VISUAL_BACKEND=polling` | `watchfiles` 대신 stdlib polling 강제 (오프라인/특수 환경) |

---

## 6. 동작 원리 요약

1. **`SessionLocator`** — `~/.claude/projects/{slug}/*.jsonl` 탐색 (실패 시 newest mtime fallback)
2. **`SessionWatcher`** — `watchfiles.awatch` 또는 polling으로 파일 tail
   - 회전 감지: inode 변경 + 파일 크기 축소 + head 256-byte fingerprint 비교 (in-place rewrite도 감지)
3. **`parser.py`** — JSONL line → 정규화된 `HarnessEvent` (schema-tolerant, unknown type은 `EventType.unknown`로 떨어뜨리고 raise하지 않음)
4. **`app.post_message()`** — watcher가 직접 Textual 메시지를 보냄 (forwarding consumer task 없음)
5. **`HarnessVisualApp`** — `selected_agent_id` reactive를 단일 진실원천으로 양 패널 동기화
6. **`OmcStateReader`** — `.omc/state/subagent-tracking.json`, `mission-state.json` (SHA-1 hash-cached) 읽어 AgentTree 갱신

---

## 7. 데모 / 테스트용 fake session 생성

라이브 Claude Code 세션 없이 화면을 시험하고 싶을 때:

```bash
# 터미널 A
harness-visual --session /tmp/fake.jsonl

# 터미널 B
python scripts/fake_session.py --target /tmp/fake.jsonl --count 200 --rate 10
# 회전(rotation) 동작 확인:
python scripts/fake_session.py --target /tmp/fake.jsonl --count 200 --rate 10 --rotate-at 50
```

---

## 8. 테스트 실행

```bash
pytest -q                    # 전체 (20 tests)
pytest -q tests/test_parser.py
pytest -q tests/test_cross_highlight.py
pytest -q -k responsiveness
```

---

## 9. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `command not found: harness-visual` | venv 비활성화. `source .venv/bin/activate` |
| `ImportError: textual` | `pip install -e '.[dev]'` 재실행 |
| 빈 화면만 보임 | `~/.claude/projects/`에 해당 cwd의 slug 디렉토리가 없음 → `--session`으로 명시 |
| 새 이벤트가 안 보임 | `HARNESS_VISUAL_BACKEND=polling` 강제, 또는 `--session`으로 정확한 파일 지정 |
| Python 3.9에서 실패 | 3.11+ 필요. Homebrew `python@3.12` 설치 |
| `watchfiles` 설치 실패 | 자동으로 polling fallback. 무시 가능 |
| 권한 오류 (`mission-state.json`) | omc_state가 PermissionError를 swallow하므로 동작에는 지장 없음. 필요시 `chmod +r` |

---

## 10. 파일 위치 참고

| 항목 | 경로 |
|---|---|
| 패키지 소스 | `src/harness_visual/` |
| 테스트 | `tests/` |
| 실 세션 fixture | `tests/fixtures/real_session_slice.jsonl` |
| JSONL 스키마 문서 | `docs/jsonl-schema-observed.md` |
| 매뉴얼 검증 기록 | `README.md` `## Manual Verification` |
| Plan / Spec | `.omc/plans/harness-visual-tui-plan.md`, `.omc/specs/deep-interview-harness-visual-tui.md` |
