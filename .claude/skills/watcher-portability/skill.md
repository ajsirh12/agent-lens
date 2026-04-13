---
name: watcher-portability
description: "파일 tail, Windows/git-bash 경로 호환, watchfiles+polling fallback, cwd 필드 기반 slug 폴백, 서브에이전트 JSONL 자동 발견 작업 시 사용. watcher.py, locator.py, subagent_watcher.py, subagent_locator.py 수정 시 반드시 이 스킬을 사용할 것."
---

# Watcher Portability

파일 tail과 세션 로케이터의 크로스 플랫폼 호환성 가이드.

## 경로 정규화

`locator.py`의 `_norm()` 함수가 핵심:

```python
def _norm(p: str) -> str:
    s = p.replace("\\", "/").rstrip("/")
    if len(s) >= 2 and s[1] == ":":  # Windows drive letter
        s = s.lower()
    return s
```

- 백슬래시 → 슬래시 변환
- trailing separator 제거
- 드라이브 레터 있으면 소문자화 (Windows case-insensitive FS)

## 세션 탐색 우선순위

`SessionLocator.find_active()` 폴백 체인:

1. **slug 디렉토리**: `~/.claude/projects/{slug-of-cwd}/` 하위 최신 JSONL
2. **cwd 필드 매칭**: slug 미스 시, 전체 JSONL의 `cwd` 필드를 읽어 현재 작업 디렉토리와 비교
3. **전역 최신**: 위 모두 실패 시, `~/.claude/projects/*/` 하위 가장 최신 JSONL

cwd 필드 폴백을 제거하면 Windows/git-bash에서 세션을 찾지 못한다. 이 경로는 보존해야 한다.

## ChosenReason 타입

세션 선택 근거 추적용 리터럴:

```python
ChosenReason = Literal[
    "slug", "fallback", "cwd-match", "none",
    "override", "picker", "switched", "path-input",
]
```

새 reason 추가 시 이 타입에 반드시 등록하라. 타입 체커가 누락을 잡아준다.

## 와처 아키텍처

### WatchfilesTailer (기본)
- `watchfiles` 라이브러리 기반, 파일 변경 이벤트 구독
- import 실패 시 자동으로 PollingTailer 폴백

### PollingTailer (폴백)
- stdlib만 사용, 주기적 stat + read
- watchfiles 없는 환경 (일부 CI, 최소 Docker) 지원

### 공통 방어
- `MAX_BUFFER_BYTES = 1_048_576`: 1 MiB 초과 라인 드롭
- inode 변경 감지: logrotate 시 파일 재오픈
- head fingerprint 비교: truncate 시 offset 리셋

## 서브에이전트 와처

`subagent_watcher.py`:
- 세션 디렉토리의 `subagents/` 하위에서 `agent-*.jsonl` 패턴 감시
- 새 파일 발견 시 개별 tailer 생성
- `subagent_locator.py`가 agent UUID → JSONL 경로 매핑

## Shift+S 경로 입력

`session_path_input.py` 모달:
- 전체 JSONL 경로 또는 bare session id/prefix 입력
- bare id → `~/.claude/projects/**/` 하위 glob 해석
- 매칭 실패 시 에러 메시지, 성공 시 세션 전환

## 테스트 대상

| 플랫폼 | 핵심 검증 항목 |
|--------|-------------|
| macOS/Linux | slug 디렉토리 매칭 |
| Windows | 드라이브 레터 정규화, 백슬래시 변환 |
| git-bash | cwd 필드 폴백 (slug 패턴 불일치) |
| CI (no watchfiles) | PollingTailer 폴백 |
