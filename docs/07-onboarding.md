# 07. 팀원 온보딩 (첫날 30분)

## 1. 준비
1. GitHub 초대 메일에서 **수락** (https://github.com/Broogun/Multi_agent_project/invitations)
2. 설치: Git, Python 3.12+, [uv](https://docs.astral.sh/uv/), 쓰는 AI 도구 (Cursor / Claude Code / Codex 등)
3. 받기:
   ```
   git clone https://github.com/Broogun/Multi_agent_project.git
   cd Multi_agent_project
   uv sync --group dev
   cp .env.example .env      # Windows PowerShell: copy .env.example .env
   uv run pytest             # 전부 통과해야 정상
   ```
4. `.env` 에 각자 키 입력. Gemini 키는 A(검색·에이전트)는 필수, B·C 는 실제 호출을 볼 때만 필요. 키는 절대 커밋·공유 금지.

## 2. 읽기 (15분)
1. [05 작업 규칙](05-working-agreement.md) — 특히 §1 역할, §4 Git, §5 AI 사용 규칙
2. [00 현재 상태](00-status.md)
3. 내 TODO 문서 ([목록](todo/00-todo-list.md)) — 내 이름이 적힌 상세 파일
4. [contracts.py](../src/spending_agent/contracts.py) — 다른 사람과 주고받는 형식. **이건 혼자 바꾸지 않는다**

## 3. AI 에이전트로 작업 시작하기
AI 도구는 `AGENTS.md` 를 통해 같은 규칙을 읽는다. 첫 지시 예시:

```
AGENTS.md 와 docs/05-working-agreement.md 를 읽고, docs/todo/<내-todo>.md 를 진행해줘.
브랜치는 <github아이디>/<YYMMDD>-<slug> 로 만들고, 끝나면 테스트를 돌리고 docs/00-status.md 의 내 줄을 갱신해.
main 에 push 하거나 PR 을 merge 하지는 마.
```

## 4. 첫 PR (연습)
1. 브랜치: `git checkout -b <github아이디>/<YYMMDD>-hello`
2. `docs/00-status.md` 담당별 현황에서 **내 줄**의 "지금 하는 일"을 채운다
3. `git push -u origin <브랜치>` → GitHub 에서 PR 생성 → 템플릿 체크 → 리뷰어 지정 (05 §1 표)
4. 승인 + CI 통과 → Squash merge

## 5. 막히면
- 다른 사람 코드가 아직 없으면 `src/spending_agent/fakes.py` 의 가짜 구현으로 계속 진행한다.
- 계약(contracts.py)을 바꿔야 하면 혼자 바꾸지 말고 이슈를 열어 3명이 합의한다.
- 결정이 필요하면 `docs/00-status.md` 의 "열린 질문"에 한 줄 추가하고 팀 채널에 알린다.
