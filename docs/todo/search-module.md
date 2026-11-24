# Gemini 그라운딩 검색 모듈

- 담당: A (Broogun)
- 상태: 대기
- 관련: [06](../06-search-policy.md), [02 검색 창 이벤트](../02-specs.md#검색-창-이벤트-search--app), 이전 구현 `archive/v0-data-pipeline:agent/llm.py` (`grounded_search`)

## 목표
`spending_agent.search.search_panel(query: str) -> SearchPanelEvent` (contracts.py) 가 있고, 상품명을 넣으면 02 스펙의 검색 창 이벤트를 돌려준다. 결과가 에이전트 입력·state·DB에 들어가지 않는 것을 테스트로 확인한다.

## 할 일
- [ ] 이전 `grounded_search` 참고해 `search/grounding.py` 작성 (google-genai, `tools=[GoogleSearch()]`)
- [ ] 검색어 정제: 80자 제한, 숫자·이름 패턴 제거
- [ ] 가짜 클라이언트 + 격리 테스트 (결과 문자열이 에이전트 입력에 없음)
- [ ] `tests/test_contracts.py` 의 SEARCH_IMPLEMENTATIONS 에 실제 함수 등록
- [ ] `@pytest.mark.live` 실제 호출 테스트 1개 (CI 제외)
- [ ] 06 미확인 항목 결과를 06에 반영

## 진행 기록
- 261124: 생성
