# 01. 폴더 구조

```
Multi_agent_project/
├─ AGENTS.md                 # 모든 AI 에이전트 공용 진입점 → docs/05
├─ CLAUDE.md                 # Claude Code 진입점 (@AGENTS.md)
├─ .cursor/rules/00-team.mdc # Cursor 진입점 → docs/05
├─ AGENTS.local.md / CLAUDE.local.md  # 개인 설정 (git 제외, 선택)
├─ README.md                 # 설치·실행 3줄 + 문서 안내
├─ pyproject.toml / uv.lock  # 의존성 (uv)
├─ .env.example              # 필요한 키 이름 (값은 .env, git 제외)
├─ .github/
│  ├─ CODEOWNERS             # 담당 범위별 리뷰어
│  ├─ PULL_REQUEST_TEMPLATE.md
│  ├─ ISSUE_TEMPLATE/        # 작업·버그
│  └─ workflows/ci.yml       # ruff + pytest (실제 API 테스트 제외)
├─ docs/
│  ├─ 00-status.md           # 현재 상태 (담당별 줄)
│  ├─ 01-folder-architecture.md
│  ├─ 02-specs.md            # 인터페이스·데이터 형식
│  ├─ 03-product-plan.md     # 무엇을 만드나, 범위, 일정
│  ├─ 04-glossary.md         # 용어
│  ├─ 05-working-agreement.md # 작업 규칙 (먼저 읽기)
│  ├─ 06-search-policy.md    # 검색 API 사용 원칙 (약관·저장·표시)
│  ├─ 07-onboarding.md       # 팀원 첫날 30분
│  ├─ todo/                  # 할 일 (00-todo-list.md + 상세 파일)
│  ├─ reports/               # 결정·완료 기록 (YYMMDD-HHMM-이니셜-slug.md)
│  └─ learning/              # 학습 문서 (참고용)
├─ src/spending_agent/       # 코드 (Python 패키지)
│  ├─ config.py              # .env 로딩
│  ├─ contracts.py           # 모듈 사이 계약 (3명 합의로만 변경)
│  ├─ fakes.py               # 가짜 구현 (다른 담당 코드가 없을 때 개발용)
│  ├─ search/                # A: Gemini 그라운딩 검색 (결과는 UI로만)
│  ├─ agent/                 # A: 에이전트 흐름 (LangGraph)
│  └─ data/                  # B: 사용자 데이터·계산
├─ app/                      # C: 웹 UI
├─ tests/                    # pytest (가짜 LLM, 네트워크 없음)
├─ eval/                     # B: 평가셋·채점 기록
├─ scripts/                  # 일회성·운영 스크립트
├─ data/
│  └─ manual/                # 수동 정리 규칙 (구독 요금·제도·가맹점 사전, 출처·기준일 포함)
│                            # data/ 의 나머지(로컬 DB, 원본)는 git 제외
└─ out/                      # 생성물 (git 제외)
```

실행: `uv sync` → `uv run pytest` → (키 설정 후) 각 모듈 README.
