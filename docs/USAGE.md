# agentlens 사용 가이드 (v0.2.0)

Claude Code 세션 JSONL 을 실시간 tail 해서 **Timeline + 라이브 Flowchart** 두 패널로
보여주는 Python + Textual TUI. 서브에이전트 호출, 스킬 호출, 병렬 spawn 까지 그래프로
시각화합니다.

---

## 1. 설치

Python 3.11+ 필요 (macOS 시스템 Python 3.9 는 불가, Homebrew Python 3.12 권장):

```bash
cd /Users/limdk/Documents/workspace/harness-visual

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

설치 후 venv 가 활성화된 상태에서 `agentlens` 명령이 PATH 에 등록됩니다.

---

## 2. 실행

### 기본 사용
```bash
agentlens
```
- 현재 작업 디렉토리(`pwd`)를 slug 로 변환해 `~/.claude/projects/-Users-limdk-.../*.jsonl` 탐색.
- 세션이 **2개 이상**이면 picker 모달이 떠서 mtime/크기/파일명을 보고 선택.
- 세션이 1개면 자동 attach. slug 디렉토리가 없으면 전체 projects 에서 **가장 최근 JSONL** 로 fallback.

### 주요 옵션

| 플래그 | 설명 |
|---|---|
| `--latest` | Picker 건너뛰고 가장 최근 세션 자동 선택 |
| `--session PATH` | 특정 JSONL 파일 직접 지정 |
| `--project-root PATH` | 다른 프로젝트 기준으로 slug 계산 |
| `--state-dir PATH` | OMC `.omc/state` 디렉토리 오버라이드 |
| `--no-attach` | 파일 attach 없이 빈 화면으로 시작 |
| `--self-test` | 한 프레임 렌더 후 exit 0 (CI 스모크 테스트) |
| `-v` / `--verbose` | DEBUG 로그 출력 |

---

## 3. 화면 구성

```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│ Timeline                             │ Flowchart                            │
│ ───────────────────────────────────  │ ──────────────────────────────────   │
│ ts        tool      agent  status    │        ┌──────┐                      │
│ 14:02:01  Task      main   ✓  1205   │        │ main │                      │
│ 14:02:03  Task      main   ✓  4708   │        └───┬──┘                      │
│ 14:02:10  Read      exec   ✓    12   │            │                         │
│ 14:02:11  Edit      exec   ✓    45   │   ┌────────┼──────────┐              │
│ ...                                  │   ▼        ▼          ▼              │
│                                      │ ┌─────┐ ┌──────┐  ┌────────┐         │
│                                      │ │plan │ │ exec │  │ critic │         │
│                                      │ │(x3) │ │[Rd4] │  │        │         │
│                                      │ │Rd12 │ └──────┘  └────────┘         │
│                                      │ └─────┘                              │
├──────────────────────────────────────┴──────────────────────────────────────┤
│ session: b0709256-...jsonl [slug]  nodes: 5 edges: 4  [all/LR/H]           │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **Timeline (왼쪽)**: DataTable 에 tool_use / tool_result 이벤트를 시간순 표시. 선택된 row 는 flowchart 의 해당 agent 노드를 cross-highlight.
- **Flowchart (오른쪽)**: 실시간 호출 그래프. `main` → agent → nested child 계층. 박스 안에 label + (xN) 카운터 + tool-breakdown 뱃지 (e.g. `Rd12 Ed5`) + 상태 색상.
- **Footer**: 세션 파일명, slug 소스, nodes/edges 카운트, 현재 모드 태그 `[mode/orient/pane]`. 좁은 터미널에서는 자동 wrap.

### 상태 색상
- **초록 (bright_green)**: running 또는 current turn 에 속함 (sticky running)
- **회색 (dim)**: done (tool_result 완료)
- **빨강 (red)**: error (tool_result.is_error)
- **bold reverse**: 현재 선택된 노드

---

## 4. 키 바인딩

### 기본 조작

| Key | Action |
|---|---|
| `q` | 종료 |
| `j` / `↓` | Timeline 커서 아래로 |
| `k` / `↑` | Timeline 커서 위로 |
| `Enter` | 선택된 timeline row 의 상세 모달 (tool name / input / status / duration) |
| `d` | 선택된 flowchart agent 노드의 subagent drill-down 모달 |
| `s` | 세션 전환 picker — 같은 slug 디렉토리의 다른 JSONL 로 이동 |
| `Shift+S` | 경로/세션 ID 붙여넣기 모달 — 임의의 JSONL 파일 또는 session id prefix 로 전환 |

### Flowchart 모드 토글

| Key | Action | 효과 |
|---|---|---|
| `m` | Mode: **all ↔ running** | all = 누적 집계 뷰, running = 현재 턴만 (sticky) + 병렬 instance 분리 |
| `o` | Orientation: **LR ↔ TD** | LR = 왼→오 (팬아웃 최적), TD = 위→아래 (깊이 최적) |
| `p` | Panes: **H ↔ V** | Timeline/Flowchart 를 좌우 배치 / 상하 배치 |

### Flowchart 스크롤

| Key | Action |
|---|---|
| 마우스 휠 | 세로 스크롤 |
| Shift + 휠 | 가로 스크롤 |
| `Shift+H` / `Shift+L` | 가로 스크롤 한 칸씩 |
| `PgUp` / `PgDn` | 세로 페이지 스크롤 |
| `Home` / `End` | 양 끝으로 점프 |

---

## 5. 주요 개념

### 세션 전환 (`s` 키)

실행 중 `s` 키를 누르면 **같은 slug 디렉토리의 다른 JSONL** 로 재시작 없이 바로 전환할 수 있습니다.

- Picker 모달에 현재 slug 디렉토리의 모든 JSONL 파일 목록 (mtime desc)
- 현재 attach 된 세션은 row 끝에 `✓ (current)` 마커
- 다른 파일 선택 → 기존 watcher/subagent manager 정지 → Timeline + Flowchart 클리어 → 새 파일의 첫 줄부터 catch-up → live tail 시작
- 현재 파일 선택 → no-op
- `Esc` → 취소, 상태 변화 없음
- 후보가 0개면 picker 안 뜸

### 경로/ID 붙여넣기 전환 (`Shift+S` 키)

`s` 키의 picker 가 원하는 세션을 못 찾는 경우 (예: Windows / git-bash 에서 slug 규칙이 달라 매칭 실패) **`Shift+S`** 를 누르면 Input 모달이 뜹니다. 터미널에서 복사한 경로나 session id 를 붙여넣고 Enter 치면 바로 attach.

입력 형식 3가지:

| 형식 | 예시 | 처리 |
|---|---|---|
| 전체 경로 | `~/.claude/projects/xxx/abc.jsonl` | `~` expand, 따옴표 strip |
| Windows / MSYS 경로 | `/c/Users/limdk/.../abc.jsonl` | Path 가 자동 처리 |
| **세션 ID 또는 prefix** | `b0709256-eb61-4ccb-9b57-49aaca263c33` 또는 `b0709256` | `~/.claude/projects/*/<id>*.jsonl` glob → unique 매칭 시 attach |

검증 규칙:
- 존재하지 않는 파일 → 빨간 에러 ("File not found"), 모달 유지
- 디렉토리나 `.jsonl` 이 아닌 파일 → 에러
- Session id prefix 가 여러 개 매칭 → "N sessions matched, paste full path" 에러
- Session id prefix 가 0개 매칭 → "No session found for id/prefix" 에러
- `Esc` → 취소, 상태 변화 없음

전환 후 footer 의 `locator_reason` 이 `[path-input]` 으로 표시됩니다.

**Windows / git-bash 에서의 권장 사용:** 터미널에서 `resume` 명령 등으로 확인한 session UUID (`b0709256...`) 의 앞 8자만 복사해 `Shift+S` → paste → Enter. Slug 계산을 완전히 우회하므로 Windows 경로 포맷 이슈와 무관하게 작동합니다.

### Windows / git-bash 호환 (cwd-match fallback)

기본 slug 계산 (`str(cwd).replace("/", "-")`) 은 POSIX 경로에 맞춰져 있어서 Windows 의 backslash 경로나 git-bash 의 `/c/...` MSYS 경로에서는 slug 디렉토리 매칭에 실패할 수 있습니다.

이 경우 `SessionLocator` 는 자동으로 **cwd-field fallback** 을 실행합니다:

1. Slug 디렉토리가 없으면 `~/.claude/projects/` 전체 서브디렉토리를 스캔
2. 각 JSONL 의 **첫 row `cwd` 필드** 를 읽어 현재 `Path.cwd()` 와 normalize 후 비교
3. 매칭되는 파일들을 mtime desc 로 반환
4. `locator_reason` 은 `[cwd-match]` 로 표시

정규화 규칙 (`_norm()`):
- Backslash → forward slash (`C:\` → `C:/`)
- Trailing separator strip
- Windows 드라이브 경로 (`C:` 시작) 는 case-fold (`C:/Users` ≡ `c:/users`)

POSIX 사용자는 slug fast path 에서 곧바로 매칭되므로 이 fallback 을 거치지 않고 오버헤드 0 입니다.

전환 후 footer 의 `locator_reason` 이 `[switched]` 로 표시되어 전환된 세션임을 구분할 수 있습니다.

**주의**: `s` 는 현재 slug 디렉토리 내부의 세션만 보여줍니다. 다른 프로젝트의 세션을 보려면 `q` 로 종료 후 `--project-root` 나 `--session` 으로 재시작해야 합니다.

### Sticky Running
Agent 가 완료(`tool_result` 도착)되어도, **다음 사용자 프롬프트가 올 때까지** 노드는 초록(running) 으로 유지됩니다. 빠른 agent 가 바로 회색으로 바뀌어 놓치는 것을 방지합니다.

플러시 필터는 시스템 주입 메시지를 걸러냅니다:
- `<task-notification>` (백그라운드 작업 완료 알림)
- `<system-reminder>` (hook 주입)
- `Base directory for this skill:` (skill 확장 프리앰블)
- `isMeta=True` 행
- 서브에이전트 파일 자체의 user row (서브에이전트가 받은 초기 프롬프트)

**진짜 사용자 입력만** 턴 경계를 만듭니다.

### Mode: All vs Running

| Mode | 보이는 것 | 용도 |
|---|---|---|
| **all** | 이 세션의 모든 agent/skill 노드 (aggregated 카운터 `(xN)` + 집계 breakdown) | "이 세션에서 뭐가 돌았나" 전체 지도 |
| **running** | 현재 턴에 touch 된 노드만. 병렬 spawn 은 **각각 독립된 virtual node** 로 분리 표시 | "지금 돌고 있는 것" 실시간 모니터 |

병렬로 같은 agent 를 2번 호출하면:
- `[all]`: `executor (x13) [Bs65 Ed49 +3]` — 하나의 박스, 누적 집계
- `[running]`: `executor [Rd3]` + `executor [Bs3]` — 두 개의 박스, 각자 자기 breakdown

### Instance Drill-down

병렬 instance 에서 특정 instance 의 서브에이전트 파일만 보고 싶을 때:

1. `m` 으로 running 모드 전환
2. 원하는 virtual instance 박스를 **마우스 클릭** (그 박스만 강조됨)
3. `d` 누르기
4. 모달 title 에 `executor (instance 1 of 2)` 같이 표시되며, 그 인스턴스의 tool 호출만 나열
5. `Esc` 로 닫기

Single-spawn 이거나 clicked instance 정보가 없으면 node 레벨의 가장 최근 subagent 파일로 fallback.

### Cross-highlight

- Timeline row 선택 → flowchart 의 같은 base id 를 가진 모든 instance 가 함께 강조 (병렬 instance 모두)
- Flowchart virtual instance 클릭 → 해당 instance 만 강조, timeline 은 base id 매칭으로 여전히 작동

### Tool Breakdown 뱃지

각 agent 노드 안에 서브에이전트가 내부에서 호출한 tool 집계가 dim 색으로 표시됩니다:

| 약어 | Tool |
|---|---|
| `Rd` | Read |
| `Ed` | Edit |
| `Bs` | Bash |
| `Gp` | Grep |
| `Gl` | Glob |
| `Wr` | Write |
| 나머지 | 이름의 앞 2자 |

공간 부족 시 `+N` 으로 나머지 개수만 표시.

Running 모드의 instance 는 **자기 인스턴스의 breakdown** 만 표시 (병렬 2개가 서로 다른 작업을 보여줌).

---

## 6. 환경 변수

| 변수 | 효과 |
|---|---|
| `AGENTLENS_BACKEND=polling` | `watchfiles` 대신 stdlib polling 강제 (오프라인/특수 환경) |

---

## 7. 아키텍처 요약

```
~/.claude/projects/{slug}/{sessionId}.jsonl         ← 메인 세션
~/.claude/projects/{slug}/{sessionId}/subagents/    ← 서브에이전트 파일들
    ├── agent-<id1>.jsonl
    ├── agent-<id2>.jsonl
    └── ...
```

데이터 흐름:
```
JSONL append
  ↓
SessionWatcher / SubagentWatcherManager (watchfiles 또는 polling)
  ↓
parser.py (JSONL → HarnessEvent)
  ↓
app.post_message(HarnessEventMessage)
  ↓
TimelinePanel.add_event / FlowchartPanel.add_event
  ↓
CallGraph.update_from_event (per-instance tracking, sticky running, nested routing)
  ↓
layout_topdown / layout_leftright
  ↓
_render_text → Rich Text on Static
```

### 주요 모듈

| 파일 | 역할 |
|---|---|
| `cli.py` | argparse 진입점 |
| `app.py` | Textual App, 패널 composition, worker 기동, 키 바인딩 |
| `parser.py` | JSONL line → HarnessEvent (schema-tolerant) |
| `watcher.py` | `PollingTailer` / `WatchfilesTailer` + rotation 감지 (inode/size/head fingerprint 3중) |
| `subagent_watcher.py` | 서브에이전트 dir polling → 개별 파일 tail |
| `subagent_locator.py` | 서브에이전트 파일 목록 |
| `locator.py` | 메인 세션 파일 탐색 (slug + newest-mtime fallback) |
| `graph_model.py` | `CallGraph` + `Node` + `Instance` + sticky running + nested spawn |
| `flowchart_layout.py` | BFS 기반 Sugiyama 레이아웃 (topdown/leftright) |
| `panels/timeline.py` | DataTable + cross-highlight |
| `panels/flowchart.py` | ASCII 캔버스 + 모드/오리엔테이션 토글 + instance view |
| `panels/detail_modal.py` | Timeline tool 상세 모달 |
| `panels/subagent_detail.py` | Drill-down 모달 (서브에이전트 tool history) |
| `panels/session_picker.py` | 세션 선택 모달 |
| `omc_state.py` | `.omc/state/` 스냅샷 diff (subagent-tracking.json) |

---

## 8. 데모 / 테스트용 fake session

```bash
# 터미널 A: fake session 을 attach 해서 띄우기
agentlens --session /tmp/fake.jsonl

# 터미널 B: 가짜 JSONL 라인 생성
python scripts/fake_session.py --target /tmp/fake.jsonl --count 200 --rate 10

# 파일 회전 동작 확인:
python scripts/fake_session.py --target /tmp/fake.jsonl --count 200 --rate 10 --rotate-at 50
```

---

## 9. 테스트 실행

```bash
pytest -q                                # 전체 (123 tests)
pytest -q tests/test_parser.py
pytest -q tests/test_instance_view.py
pytest -q tests/test_flowchart_panel.py
pytest -q -k graph_model
```

---

## 10. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `command not found: agentlens` | venv 비활성화. `source .venv/bin/activate` |
| `ImportError: textual` | `pip install -e '.[dev]'` 재실행 |
| 빈 화면만 보임 | `~/.claude/projects/` 에 해당 cwd 의 slug 디렉토리가 없음 → `--session` 명시 |
| 새 이벤트가 안 보임 | `AGENTLENS_BACKEND=polling` 강제, 또는 `--session` 으로 정확한 파일 지정 |
| Python 3.9 에서 실패 | 3.11+ 필요. Homebrew `python@3.12` 설치 |
| `watchfiles` 설치 실패 | 자동으로 polling fallback. 무시 가능 |
| 권한 오류 (`mission-state.json`) | omc_state 가 PermissionError 를 swallow. 필요시 `chmod +r` |
| 코드 수정 후 반영 안 됨 | TUI 프로세스 재시작 필요. 필요 시 `__pycache__` 삭제: `find src -name __pycache__ -exec rm -rf {} +` |
| 좁은 터미널에서 footer 안 보임 | Footer 는 height auto / max 3 줄로 wrap. 내용이 너무 길면 파일명만 표시 |

---

## 11. 다른 디렉토리에서 실행

`agentlens` 은 소스 디렉토리에 묶여 있지 않습니다. **현재 작업 디렉토리(`pwd`)** 기준으로 세션을 찾습니다:

```bash
# shell alias (~/.zshrc)
alias hv='/Users/limdk/Documents/workspace/harness-visual/.venv/bin/agentlens'

# 아무 프로젝트에서
cd ~/Documents/workspace/some-other-project
hv                              # 그 프로젝트의 Claude Code 세션 자동 attach
hv --latest                     # picker 건너뛰기
hv --project-root ~/foo         # 다른 cwd 로 slug 계산
```

---

## 12. 파일 위치 참고

| 항목 | 경로 |
|---|---|
| 패키지 소스 | `src/agentlens/` |
| 테스트 | `tests/` |
| 실 세션 fixture | `tests/fixtures/real_session_slice.jsonl` |
| JSONL 스키마 문서 | `docs/jsonl-schema-observed.md` |
| 이 문서 | `docs/USAGE.md` |
| CHANGELOG | `CHANGELOG.md` |
| Plan / Spec | `.omc/plans/harness-visual-tui-plan.md`, `.omc/specs/deep-interview-harness-visual-tui.md` |
