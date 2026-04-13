---
name: agentlens-release-notes
description: "agentlens의 CHANGELOG.md, README.md, docs/USAGE.md, docs/ROADMAP.md 갱신 시 사용. Keep a Changelog 포맷, 아스키 스크린샷 블록 유지, 기능 섹션 톤, 버전 범프 규칙. 릴리즈, 문서 갱신, README 업데이트, 변경 이력 작성 시 반드시 이 스킬을 사용할 것."
---

# agentlens Release Notes

문서 4종의 갱신 규약.

## CHANGELOG.md

**포맷**: [Keep a Changelog](https://keepachangelog.com/) 준수

```markdown
## [Unreleased]

### Added
- 새 기능 bullet

### Changed
- 기존 기능 변경 bullet

### Fixed
- 버그 수정 bullet

### Removed
- 삭제된 기능 bullet
```

- `## [Unreleased]` 하위에 작성한다. 릴리즈 시 날짜+버전으로 교체.
- bullet은 과거형("Added")이 아닌 명사형("Add")으로 시작한다.
- 관련 커밋 해시나 이슈 번호가 있으면 괄호로 병기한다.

## README.md

### 기능 섹션
- bullet 리스트로 기능을 나열한다
- `**굵은 제목**:` + 설명 형태
- 새 기능은 관련 기능 근처에 삽입 (마지막에 추가하지 않음)

### 아스키 스크린샷 블록
```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│ Timeline                             │ Flowchart                            │
...
```
이 블록의 **구조를 유지**한다. 새 컬럼/패널 추가 시 블록도 갱신하되, 기존 정렬을 깨지 않는다.

### 키 바인딩 목록
키 바인딩 추가/변경 시 README의 키 설명도 갱신한다.

## docs/USAGE.md

- 설치, 실행, 키 바인딩 상세
- 키 바인딩 변경 시 키 맵 테이블 갱신
- 새 CLI 옵션 추가 시 옵션 목록 갱신

## docs/ROADMAP.md

- 완료: `[x]`
- 진행 중: `[ ]`
- 드롭: `~~삭선~~`
- 새 항목은 관련 섹션에 추가

## 톤

- 기술적이지만 간결하게
- 사용자 관점의 변경 사항을 먼저, 내부 구현 세부는 뒤에
- "수정했습니다" 같은 경어 금지 — "수정" 또는 "Fix" 명사형
