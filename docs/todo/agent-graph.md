# 에이전트 흐름 (LangGraph, LLM 제공자 미정)

- 담당: A (Broogun)
- 상태: 대기 (가짜 구현으로 개발 가능, 실제 LLM 연동은 제공자 선정 후)
- 관련: [contracts.py](../../src/spending_agent/contracts.py) `ChatRequest`/`ChatResponse`, 이전 구현 `archive/v0-data-pipeline:agent/graph.py`, `agent/validator.py`

## 목표
`spending_agent.agent.run(req: ChatRequest) -> ChatResponse` 가 동작한다. C 화면은 이 함수만 부른다.

## 입력·출력 (고정)
- 입력: `ChatRequest` (user_id, message, session_id)
- 출력: `ChatResponse` (answer, events=검색 창 목록, validation)
- 부르는 B 함수: `data.calc.calc_bulk_purchase`, `data.calc.forecast_goal`, `data.prices.record_user_price` — 아직 없으면 `fakes.py` 의 같은 이름 함수를 쓴다.

## 할 일
- [ ] 이전 `agent/graph.py` 를 `src/spending_agent/agent/` 로 옮기며 Tool 을 B 함수로 교체
- [ ] 노드: agent → tools → search(검색 창, UI 이벤트로만) → validate(숫자 검증)
- [ ] 세션별 대화 기록 (session_id 기준, 메모리 저장으로 시작)
- [ ] `tests/test_contracts.py` 의 CHAT_IMPLEMENTATIONS 에 실제 run 등록
- [ ] 검색 결과가 LLM 입력·state 에 없는지 테스트 (이전 test_agent.py 참고)

## 완료 기준
가짜 LLM 으로 목표 점검, 구매 고민→검색 창, 확인 가격→계산 시나리오를 검증한다. 실제 LLM 수동 확인은 제공자 선정 후 진행한다.
