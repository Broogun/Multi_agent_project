# 에이전트 공용 진입점

이 저장소에서 작업하는 모든 AI 에이전트(Codex, Cursor, Claude Code 등)와 사람은 작업 전에 아래 순서로 읽는다.

1. [docs/05-working-agreement.md](docs/05-working-agreement.md) — 작업 규칙 (역할, 흐름, 원칙, Git, AI 사용 규칙)
2. [docs/00-status.md](docs/00-status.md) — 현재 상태
3. 작업과 관련된 `docs/01~06`, [docs/todo/00-todo-list.md](docs/todo/00-todo-list.md)

검색 기능을 건드리면 [docs/06-search-policy.md](docs/06-search-policy.md)를 반드시 따른다.

핵심 금지 사항 (자세한 것은 05):
- main 에 직접 push, force push, PR merge 하지 않는다. 작업은 `<이니셜>/<YYMMDD>-<slug>` 브랜치에서.
- 비밀값(API 키)을 코드·문서·커밋·프롬프트에 넣지 않는다.
- 검색 결과를 저장·수정·분석하거나 에이전트 입력에 넣지 않는다. 링크된 페이지를 열어 읽지 않는다.
- LLM이 숫자를 만들게 하지 않는다. 숫자는 계산 코드 결과만 인용한다.
- 확인하지 않은 사실(가격, 약관, 출처)을 문서에 확정처럼 쓰지 않는다.

작업이 끝나면: 테스트(`uv run pytest`) → `docs/00-status.md` 자기 담당 줄 갱신 → 결정이 있으면 `docs/reports/` 리포트.

개인 설정은 `AGENTS.local.md`에 둔다 (git 제외).
