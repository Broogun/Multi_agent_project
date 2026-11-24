# 구매 판단 계산 + 사용자 확인 가격 저장

- 담당: B (parky2won)
- 상태: 대기
- 관련: [contracts.py](../../src/spending_agent/contracts.py) `ToolResult`/`UserPrice`, [02-specs](../02-specs.md), 이전 구현 `archive/v0-data-pipeline:tools/core.py` (`calc_bulk_purchase`, `record_user_price`, `forecast_goal`)

## 목표
에이전트(A)가 부를 계산 함수 3개가 `ToolResult` 형식으로 동작한다. 숫자는 여기서만 만든다.

## 만들 함수 (이름·인자 고정, A 가 이 이름으로 부른다)
| 모듈 | 함수 | 하는 일 |
|---|---|---|
| `spending_agent.data.prices` | `record_user_price(p: UserPrice) -> ToolResult` | 사용자 확인 가격 저장. source_type 이 `user_verified/receipt_ocr/capture_ocr` 가 아니면 `needs_check` |
| `spending_agent.data.calc` | `calc_bulk_purchase(options: list[dict], daily_use: float, shelf_life_days: int \| None = None) -> ToolResult` | 단위가격, 소진 기간, 유통기한 안 소진 여부, 가장 비싼 선택지 대비 절감액 |
| `spending_agent.data.calc` | `forecast_goal(goal_amount: int, deadline: str, current_saved: int = 0, monthly_saving: int \| None = None) -> ToolResult` | 필요 월 저축액, 남은 개월, 목표 달성 여부 (거래내역 없이 입력값 기반으로 시작) |

규칙: `calc` 에 계산식 문자열을 꼭 넣는다 (예: `"6,900 ÷ 3,000g = 2.3"`). 반올림은 마지막에만 (이전 버전에서 1원 오차 버그).

## 할 일
- [ ] `src/spending_agent/data/prices.py`, `calc.py` 작성 (저장은 SQLite `data/user.db` 로 시작)
- [ ] 단위 테스트: 경계값(유통기한 초과, 0원, 검색 가격 거부)
- [ ] `tests/test_contracts.py` 의 CALC_IMPLEMENTATIONS 에 실제 함수 등록
- [ ] A 에게 PR 리뷰 요청 (계약 확인)

## 완료 기준
`uv run pytest` 통과, 계약 테스트에 실제 함수 3개 등록.
