# 웹 UI: 채팅 + 검색 창 + 가격 입력

- 담당: C (KIM-YUNHO)
- 상태: 대기
- 관련: [contracts.py](../../src/spending_agent/contracts.py) `ChatRequest`/`ChatResponse`/`SearchPanelEvent`/`UserPrice`, [06-search-policy](../06-search-policy.md)

## 목표
브라우저에서 대화하고, 검색 창이 답변과 분리돼 보이고, 확인한 가격을 입력할 수 있다. A·B 코드가 없어도 `fakes.fake_chat` 으로 먼저 완성한다.

## 구조 (제안, 바꿔도 됨)
- 서버: `app/server.py` (FastAPI 등) — `POST /api/chat` → `agent.run(req)` (없으면 `fakes.fake_chat`), `POST /api/prices` → `data.prices.record_user_price` (없으면 fake)
- 화면: 채팅 영역 + 오른쪽(모바일은 아래) 검색 창 카드 + 가격 입력 폼

## 검색 창 규칙 (docs/06)
- [ ] `text`, `sources`, `suggestions_html` 을 **수정 없이** 그대로 표시 (`suggestions_html` 은 HTML 그대로 렌더링)
- [ ] 에이전트 답변과 다른 영역에 표시 (끼워 넣지 않음)
- [ ] 링크는 원래 URL로 새 탭에서 바로 연다. 중간 페이지·리다이렉트·클릭 추적 금지
- [ ] `notice` 문구 표시 (결과 블록 바깥)
- [ ] 검색 창 내용을 서버·로컬 저장소에 저장하지 않음

## 가격 입력 폼
- [ ] 필드: 상품명, 용량, 가격, 판매처, 확인일 (source_type 은 `user_verified` 고정, 캡처는 이후)
- [ ] 검색 창의 가격을 자동으로 채우지 않는다 (사용자가 판매처에서 보고 입력)

## 완료 기준
fake_chat 으로 시나리오 2개(목표 질문, 구매 고민 → 검색 창 → 가격 입력) 시연 가능. 스크린샷을 PR 에 첨부.
