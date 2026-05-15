# agentlens

[English](../README.md)

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

Claude Code 세션을 실시간으로 모니터링하는 TUI 도구입니다. 도구 호출, 에이전트 스폰, 서브에이전트 트리가 펼쳐지는 과정을 — Claude Code 워크플로우를 건드리지 않고 — 실시간으로 확인할 수 있습니다.

```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│ Timeline                             │ Flowchart                            │
│ ───────────────────────────────────  │ ──────────────────────────────────   │
│ ts        Turn  prompt    tools  dur │        ┌──────┐                      │
│ 14:02:01  1     "Fix b…"  ✓  8  1.2s│        │ main │                      │
│ 14:02:30  2     "Add f…"  ✓ 12  4.7s│        └───┬──┘                      │
│ 14:08:55  3     "Now r…"  ▶  3    - │            │                         │
│ ...                                  │   ┌────────┼──────────┐              │
│                                      │   ▼        ▼          ▼              │
│                                      │ ┌─────┐ ┌──────┐  ┌────────┐         │
│                                      │ │plan │ │ exec │  │ critic │         │
│                                      │ │(x3) │ │[Rd4] │  │        │         │
│                                      │ │Rd12 │ └──────┘  └────────┘         │
│                                      │ └─────┘                              │
├──────────────────────────────────────┴──────────────────────────────────────┤
│ session: b0709256-...jsonl [slug]  nodes: 5 edges: 4  [all/LR/H]           │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 주요 기능

- **실시간 테일링** — `watchfiles`를 통해 Claude Code 세션 JSONL을 라이브 테일링 (표준 라이브러리 폴링 폴백 지원). 새 이벤트가 ~1초 내에 표시됩니다.
- **타임라인 패널** — 프롬프트 미리보기, 도구 수, 소요 시간을 포함한 턴 목록. `Enter`로 전체 프롬프트·토큰 사용량·도구 내역을 모달로 확인합니다.
- **플로우차트 패널** — Agent/Task/Skill 호출의 실시간 방향 그래프. 부모/자식 엣지, `(xN)` 중복 카운터, 서브에이전트별 도구 뱃지(예: `Rd12 Ed5`), 상태별 색상 코딩(실행 중 / 완료 / 오류).
- **최대 깊이 5의 중첩 서브에이전트 트리** — 서브에이전트가 Skill을 통해 또 다른 서브에이전트를 스폰하면 `main`에 합쳐지지 않고 자식 노드로 표시됩니다.
- **병렬 인스턴스 뷰** — `[running]` 모드에서는 병렬 스폰이 각각 별도 박스로, `[all]` 모드에서는 `(xN)` 카운터로 집계됩니다. `d` 키로 특정 인스턴스의 도구 히스토리를 드릴다운합니다.
- **세션 전환** — `s`로 재시작 없이 세션 전환, `Shift+S`로 경로·UUID 직접 입력. Windows / git-bash 경로 형식은 자동으로 정규화됩니다.

전체 기능 목록, 키 바인딩, 모드 설명, 아키텍처 노트는 [`USAGE.md`](USAGE.md)를 참조하세요.

## 설치

Python 3.11+ 필요.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

**Windows (git-bash / MSYS2)**

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -e '.[dev]'
```

> **유니코드 렌더링**: UI는 블록 문자(▁▂▃▄▅▆▇█)와 박스 드로잉 글리프를 사용합니다.
> **Windows Terminal** 또는 Cascadia Code / Fira Code 같은 폰트를 사용하세요.
> 레거시 `conhost.exe` 콘솔에서는 깨진 문자로 표시될 수 있습니다.

## 실행

```bash
agentlens                            # cwd의 slug 디렉터리에서 가장 최신 세션 자동 선택
agentlens --latest                   # 피커 건너뛰고 가장 최신 세션 선택
agentlens --session PATH             # 특정 JSONL 파일 직접 연결
agentlens --project-root PATH        # 다른 cwd로부터 slug 계산
agentlens --self-test                # 한 프레임 렌더링 후 종료 코드 0으로 종료 (CI 스모크 테스트)
agentlens -v                         # 상세 로깅
```

`watchfiles`를 설치할 수 없는 경우(Windows에서 C 확장 빌드 실패 시 흔히 발생), 표준 라이브러리 폴링 테일러를 강제 사용합니다:

```bash
# macOS / Linux / git-bash
AGENTLENS_BACKEND=polling agentlens

# Windows PowerShell
$env:AGENTLENS_BACKEND="polling"; agentlens

# Windows CMD
set AGENTLENS_BACKEND=polling && agentlens
```


## Windows / git-bash

Claude Code가 프로젝트에 생성하는 slug 디렉터리는 작업 디렉터리 경로에서 파생됩니다. Windows에서는 경로 형식이 달라(`C:\Users\…` 또는 git-bash의 `/c/Users/…`) 기본 slug 조회가 실패할 수 있습니다.

**자동 폴백** — `SessionLocator`가 실패를 감지하고 `~/.claude/projects/` 전체를 스캔하여 각 JSONL에 기록된 `cwd` 필드를 현재 디렉터리와 비교합니다(백슬래시, MSYS 드라이브 접두사, 대소문자 정규화 자동 처리). 폴백이 동작하면 푸터에 `[cwd-match]`가 표시됩니다.

**수동 우회 방법** (`Shift+S`) — 자동 폴백으로도 세션을 찾지 못할 경우 `Shift+S`로 경로 입력 모달을 열고 다음 중 하나를 붙여넣으세요:

- `.jsonl` 파일의 전체 경로
- 세션 UUID 앞 8자 이상 (예: `b0709256`) — slug 해석을 완전히 우회하므로 경로 형식과 무관하게 동작

**`--project-root`** — agentlens를 Claude Code 프로젝트와 다른 디렉터리에서 실행하는 경우 `--project-root PATH`로 slug 계산 기준 디렉터리를 지정할 수 있습니다.

## 테스트

```bash
pytest -q
```

## 변경 이력

전체 릴리스 히스토리는 [CHANGELOG.md](../CHANGELOG.md)를 참조하세요.
