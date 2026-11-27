"""싱글 에이전트 기준선 + 비교 하네스 (docs/10, 아키텍처 명세 11.1 의 A1).

멀티 에이전트 구조와 같은 조건으로 비교하기 위한 기준선이다. 서비스 코드(agent/)와 따로 둔다.

| 파일 | 하는 일 | 구조와 상관없이 공유 |
|---|---|---|
| harness.py | 날짜 진행, 유저 이벤트·피드백 호출, 숫자 검증, 서비스 있음 세계 반영. `Agent` 프로토콜 | 예 |
| tools.py | 결정적 도구 (보이는 거래만, ToolResult) | 예 |
| simulator.py | 규칙형 유저 시뮬레이터 (숨은 사실 답, 제안 반응) | 예 |
| score.py | 채점 (규칙 + 선택적 LLM 심판) | 예 |
| llm.py | LLM 연결부 (가짜 LLM, OpenAI 어댑터) | 예 |
| single.py | 싱글 에이전트: LLM 1개를 피드백 / 유저 이해 두 방식으로 부른다 | 기준선 전용 |

멀티 에이전트는 `harness.Agent`(feedback, understand)를 구현해 `harness.run()` 에 넣으면 같은 하네스로 비교된다.

실행 (결과는 eval/runs/, git 제외):
    uv run python -m spending_agent.baseline run P1 --llm dry                       # 연습 (API 안 부름)
    uv run --with openai python -m spending_agent.baseline run P1 --llm openai     # 실제 실행 (.env 의 OPENAI_API_KEY)
        [--model gpt-6.1-terra] [--until 2026-12-14] [--judge] [--judge-model gpt-6.2-sol]
    uv run python -m spending_agent.baseline score eval/runs/<파일>.json           # 다시 채점
"""
