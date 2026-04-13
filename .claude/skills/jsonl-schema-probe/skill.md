---
name: jsonl-schema-probe
description: "Claude Code 세션 JSONL 또는 서브에이전트 JSONL의 이벤트 타입·필드 빈도·미지 필드를 자동 탐지. agentlens 파서 수정, 새 이벤트 타입 지원, 스키마 문서 갱신, JSONL 스키마 조사 작업 시 반드시 이 스킬을 사용할 것."
---

# JSONL Schema Probe

Claude Code 세션 JSONL 파일을 분석하여 이벤트 타입·필드 빈도표를 생성하고, `parser.py`가 놓칠 수 있는 미지 필드를 탐지한다.

## 워크플로우

1. **대상 파일 선정**: 지정된 JSONL 경로 또는 `~/.claude/projects/` 하위 최신 파일
2. **probe 스크립트 실행**: `scripts/probe.py`를 실행하여 빈도표 생성
3. **기존 스키마와 비교**: `docs/jsonl-schema-observed.md`와 diff
4. **리포트 작성**: `_workspace/01_schema_report.md`

## 스크립트 사용법

```bash
python .claude/skills/jsonl-schema-probe/scripts/probe.py <jsonl_path>
```

출력:
- 이벤트 타입 빈도 표 (type → count)
- `message.content[]` 블록 타입 빈도
- tool_use.name 빈도
- 전체 필드 목록 (깊이 2까지)

## 샘플링 전략

- 10만 라인 이하: 전수 분석
- 10만 라인 초과: 처음 1만 + 마지막 1만 + 랜덤 1만 (총 3만 라인)

이유: 세션 초반에 permission-mode 등 1회성 이벤트가 집중되고, 후반에 새 패턴이 등장할 수 있다. 랜덤 샘플은 중간부를 커버한다.

## 스키마 문서 갱신 규약

`docs/jsonl-schema-observed.md` 갱신 시:
- 상단의 Source/Captured/Line count 메타데이터를 반드시 업데이트한다
- 기존 테이블에 행을 추가하되 삭제하지 않는다 (빈도 0이면 `(deprecated?)` 태그)
- 새 tool name은 `## Tool names observed` 테이블에 추가하고, flowchart 역할을 분류한다
