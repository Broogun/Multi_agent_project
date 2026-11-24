# 검색 기반으로 다시 시작, 협업 하네스 구성

- 날짜: 261124
- 작성: A (Claude Code와 작업)
- 관련: 태그 `archive/v0-data-pipeline`, [05](../05-working-agreement.md), [06](../06-search-policy.md)

## 결론
이전 코드를 태그로 보존하고 지웠다. 검색(Gemini 그라운딩) 기반으로 다시 시작하며, 3명이 협업할 문서·규칙·저장소 구조를 먼저 만들었다.

## 배경
- 공공데이터 수집 중심 구조는 데이터 양(온라인가격 하루 45만 건)과 범위 문제가 있었고, 팀은 검색 기반으로 가기로 했다.
- 3명이 AI 에이전트로 함께 개발하므로 규칙과 맥락을 저장소 문서에 두는 하네스가 필요했다 (MINI_CNS 프로젝트 구조 참고).

## 한 일
- 태그 `archive/v0-data-pipeline` (GitHub에 push): 공공데이터 수집기 5종, 거래 CSV 파이프라인, 가맹점 사전·지표 F01~F08, MCP Tool 10개, LangGraph+Gemini 뼈대, 테스트 44개.
- 지운 것: `agent/ collectors/ pipeline/ tools/ tests/ scripts/ mcp_server.py requirements.txt`.
- 남긴 것: `data/manual/` (구독 요금·환급 제도·가맹점 사전 YAML, 출처·기준일 포함). 로컬 `data/` DB와 `.env`는 git 밖에 그대로.
- 만든 것: `docs/00~06`, `todo/`, `reports/`, 에이전트 진입 파일(AGENTS.md, CLAUDE.md, Cursor 규칙), PR·이슈 템플릿, CODEOWNERS, CI, `pyproject.toml`(uv).

## 버린 대안
- 이전 코드를 `archive/` 폴더로 남기기: 팀원과 에이전트가 옛 코드를 기준으로 착각할 수 있어 태그로만 보존.
- 리포트 번호를 전역 순번(NN)으로: 3명이 동시에 만들면 겹쳐서 이니셜로 대체.

## 다음
- 팀원 이름·역할 확정 → `05 §1`, `CODEOWNERS`
- 검색 기반에서 남길 기능 범위 결정 → [03](../03-product-plan.md)
- GitHub 설정: 팀원 초대, main 브랜치 보호
