# Multi_agent_project

목표 지향 소비 관리 에이전트 (Gemini 검색 기반). 3인 팀, AI 에이전트로 함께 개발한다.

## 처음 시작
```
git clone https://github.com/Broogun/Multi_agent_project.git
cd Multi_agent_project
uv sync --group dev          # 의존성 설치 (uv: https://docs.astral.sh/uv/)
cp .env.example .env         # 키는 각자 발급해 채운다
uv run pytest                # 하네스 점검 포함
```

## 먼저 읽을 것
1. [작업 규칙](docs/05-working-agreement.md) — 역할, 작업 흐름, Git·AI 사용 규칙
2. [현재 상태](docs/00-status.md)
3. [폴더 구조](docs/01-folder-architecture.md), [검색 사용 원칙](docs/06-search-policy.md)

AI 에이전트(Cursor, Claude Code, Codex 등)는 `AGENTS.md`를 통해 같은 규칙을 읽는다.

## 이전 버전
공공데이터 수집·거래 분석·MCP Tool 코드는 태그 `archive/v0-data-pipeline`에 있다.
```
git checkout archive/v0-data-pipeline -- <경로>   # 필요한 부분만 가져오기
```
