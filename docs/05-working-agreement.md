# 05. 작업 규칙 (팀 3명 + AI 에이전트)

사람이든 AI 에이전트든 이 저장소에서 작업하기 전에 이 문서부터 읽는다. 에이전트 진입 파일(`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/`)은 모두 이 문서를 가리킨다.

원본 기획: Google Docs "AI 에이전트 소비 관리 서비스 — 프로젝트 통합 문서"
https://docs.google.com/document/d/1qNxeOV329Fr0PMaX9TIPHjwHkG9RrHqN3F6OLKTHtNU/edit

## 1. 역할과 담당 범위

| 담당 (GitHub) | 역할 | 맡는 일 | 주로 고치는 곳 | 리뷰어 | 첫 TODO |
|---|---|---|---|---|---|
| Broogun (팀장) | A | 검색·에이전트: 검색 연동, 에이전트 흐름, 프롬프트, 전체 통합 | `src/spending_agent/search/`, `src/spending_agent/agent/` | parky2won | [search-module](todo/search-module.md) |
| parky2won | B | 데이터·평가: 계산 로직, 사용자 확인 가격, 평가 시나리오 | `src/spending_agent/data/`, `eval/`, `tests/` | KIM-YUNHO | [calc-core](todo/calc-core.md) |
| KIM-YUNHO | C | 화면·연동: 웹 UI, 검색 창 렌더링, 가격 입력, 시연 | `app/` | Broogun | [web-ui](todo/web-ui.md) |

- 팀장(A)은 계약(`src/spending_agent/contracts.py`)과 통합을 책임진다. 계약은 3명 합의로만 바꾼다.
- 다른 담당의 코드가 아직 없으면 `src/spending_agent/fakes.py` 로 개발한다 → [07 온보딩](07-onboarding.md).

- 담당 범위 밖 파일도 고칠 수 있다. 대신 PR에서 그 범위 담당자를 리뷰어로 지정한다 (`.github/CODEOWNERS`).
- 역할을 바꾸면 이 표와 CODEOWNERS를 같은 PR에서 고친다.

## 2. 작업 흐름 (사람·에이전트 공통)

1. 요청의 목표를 한 줄로 정리한다.
2. 읽는다: 이 문서 → `docs/00-status.md` → `docs/01~06` 중 관련 문서 → `docs/todo/00-todo-list.md`.
3. 관련 TODO가 있으면 그 문서를 기준으로 진행한다. 없고 작업이 반나절보다 크면 TODO를 먼저 만들고 담당자를 적는다.
4. 브랜치를 만든다 (§4). main 에서 직접 작업하지 않는다.
5. 실행한다. 코드를 바꾸면 테스트를 추가하거나 고친다.
6. 기록한다: 결정이 있으면 `docs/reports/`에 리포트(왜, 버린 대안 포함). 스펙이 바뀌면 `docs/02-specs.md`.
7. `docs/00-status.md`의 **자기 담당 줄**과 TODO 상태를 갱신한다.
8. PR을 올린다. 템플릿 체크리스트를 채운다.

필요한 문서만 읽는다. **맥락은 대화가 아니라 저장소 문서에 둔다.** 회의·메신저에서 정한 것도 리포트로 옮겨야 결정으로 친다.

## 3. 원칙 (기획의 결정 사항)

- LLM은 숫자를 만들지 않는다. 금액·절감액·기간은 계산 코드가 만들고, LLM은 그 결과만 인용한다.
- 검색 결과는 보여주기만 한다. 저장·수정·분석하지 않는다. 자세한 규칙은 [06-search-policy](06-search-policy.md).
- 계산에 쓰는 가격은 사용자가 판매처에서 직접 확인한 값과 공공 데이터뿐이다.
- 크롤링하지 않는다. 링크된 페이지를 코드나 에이전트가 열어 읽지 않는다.
- 결정(구매·해지·변경)은 사용자가 한다. 에이전트는 선택지와 근거를 제시한다.
- 사용자 데이터는 그 사용자 범위에서만 쓴다. 다른 사용자와 공유하거나 학습에 쓰지 않는다.

## 4. Git 규칙

- 기본 브랜치: `main`. 직접 push 금지, PR로만 합친다.
- 브랜치 이름: `<github아이디 소문자>/<YYMMDD>-<slug>` (예: `broogun/261125-search-client`).
- PR: 다른 팀원 1명 승인 + CI 통과 후 **Squash merge**. 승인자는 담당 범위 리뷰어(§1).
- PR 크기: 리뷰어가 30분 안에 읽을 수 있게. 크면 쪼갠다.
- 커밋 메시지: 한국어 한 줄 요약 + 필요하면 본문. AI가 쓴 커밋도 사람이 내용을 읽고 올린다.
- 충돌이 잦은 파일(`docs/00-status.md`, `docs/todo/00-todo-list.md`)은 자기 줄만 고친다.
- 되돌릴 수 없는 작업(대량 삭제, 히스토리 재작성, force push)은 팀 채널에 먼저 알린다. 지우기 전에 태그로 보존한다 (예: `archive/v0-data-pipeline`).

## 5. AI 에이전트 사용 규칙

- 도구는 자유 (Cursor, Claude Code, Codex 등). 공용 규칙은 이 문서 하나로 관리한다. 개인 설정은 `AGENTS.local.md`, `CLAUDE.local.md` (git 제외).
- 에이전트에게 작업을 맡길 때 브랜치에서 시킨다. main push, PR merge, force push는 에이전트에게 시키지 않는다.
- AI가 만든 코드는 올린 사람이 책임진다. PR 전에 직접 읽고, 테스트를 돌리고, 템플릿의 "AI 사용" 칸을 채운다.
- 에이전트가 만든 숫자·출처·약관 해석은 원문으로 확인한 것만 문서에 쓴다. 확인 못 한 것은 "미확인"으로 적는다.
- 비밀값(API 키, 비밀번호)을 프롬프트·코드·문서·커밋에 넣지 않는다. `.env`만 쓴다.

## 6. 키·비용 관리

- 키는 각자 발급해 각자 `.env`에 둔다. 공유가 필요한 키는 팀 채널 DM으로만 전달하고 저장소에는 `.env.example`에 이름만 적는다.
- 검색·LLM 제공자는 미정이며 실제 연동은 보류한다. 도입 전에 데이터 이용 조건·지원 지역·과금 방식과 예산을 확인한다 → [06-search-policy](06-search-policy.md#비용).
- 테스트는 가짜 LLM으로 돌린다. 실제 API를 부르는 테스트는 `@pytest.mark.live`로 표시하고 CI에서 돌리지 않는다.

## 7. 문서 규칙

- 리포트 파일명: `YYMMDD-HHMM-<역할 a/b/c>-slug.md` (예: `261124-1530-a-search-pivot.md`). 역할 글자를 넣어 3명이 동시에 만들어도 겹치지 않게 한다.
- TODO 파일명: `slug.md`, 목록 `docs/todo/00-todo-list.md`에 담당자와 함께 등록.
- 형식은 각 폴더의 `_template.md`를 따른다.
- 문서는 짧게 유지한다. `00-status.md`는 40줄 이내, 본문 복제 금지(링크만).

## 8. 학습 문서 (선택)

새 개념이나 설계 결정이 나온 작업은 다른 두 사람이 설명할 수 있도록 학습 문서를 만든다.
- 스킬: `cognitive-teacher` (개인 PC에 설치, 저장소에 포함하지 않음)
- 저장: `docs/learning/YYMMDD-slug.html`. 상단에 작성일, 대상 코드·문서, 관련 리포트
- 학습 문서는 참고용이다. 기준은 `docs/01~06`이다.

## 9. 주간 리듬 (제안)

- 매 작업 끝: `00-status.md` 자기 줄 갱신.
- 주 1회 30분: TODO 목록 정리, 열린 질문 결정 → 리포트 1건으로 남김.
- PR 리뷰는 요청받은 다음 작업일 안에 한다.
