"""가짜 구현. 다른 담당의 실제 코드가 아직 없을 때 이걸로 개발·테스트한다.

- C (화면): fake_chat() 으로 화면을 먼저 만든다. 검색 창 이벤트도 들어 있다.
- A (에이전트): fake_calc_bulk_purchase() 등으로 B 함수 대신 붙여 본다.
- B (데이터): 이 파일과 같은 형식(contracts.ToolResult)을 지키면 A 가 그대로 바꿔 끼운다.

실제 구현이 들어오면 tests/test_contracts.py 가 실제 함수도 같은 형식인지 확인한다.
"""
from __future__ import annotations

import datetime as dt

from .contracts import SEARCH_NOTICE, ChatRequest, ChatResponse, SearchPanelEvent, ToolResult, UserPrice


def _result(value, calc=None, status="ok", caveats=()) -> ToolResult:
    today = dt.date.today().isoformat()
    return {"status": status, "value": value, "calc": calc or {},
            "sources": [{"id": "U_PRICE", "name": "사용자가 확인한 가격", "as_of": today}],
            "as_of": today, "caveats": list(caveats)}


# ---------------------------------------------------------------- B 대역
def fake_record_user_price(p: UserPrice) -> ToolResult:
    if p.get("source_type") not in ("user_verified", "receipt_ocr", "capture_ocr"):
        return _result(None, status="needs_check", caveats=["사용자가 판매처에서 확인한 가격만 저장한다."])
    return _result({"id": 1})


def fake_calc_bulk_purchase(options: list[dict], daily_use: float, shelf_life_days: int | None = None) -> ToolResult:
    return _result(
        {"best": "3kg", "options": [{"name": "1kg", "unit_price": 2.8, "days_to_use_up": 100.0},
                                    {"name": "3kg", "unit_price": 2.3, "days_to_use_up": 300.0}],
         "saving_vs_most_expensive": 1500},
        calc={"1kg": "2,800 ÷ 1,000g = 2.8", "3kg": "6,900 ÷ 3,000g = 2.3",
              "saving": "(2.8 − 2.3) × 3,000 = 1,500"})


def fake_forecast_goal(goal_amount: int, deadline: str, current_saved: int = 0) -> ToolResult:
    return _result({"monthly_required": 150000, "months_left": 10, "on_track": True},
                   calc={"required": "(2,000,000 − 500,000) ÷ 10개월 = 150,000"})


# ---------------------------------------------------------------- A 대역
def fake_search_panel(query: str) -> SearchPanelEvent:
    return {"type": "search_panel", "panel_id": "fake0001", "query": query,
            "text": f"(가짜 검색 요약) {query}: 대형마트 온라인몰, 공식몰에서 판매. 용량과 배송비를 함께 비교하세요.",
            "suggestions_html": "<div class='fake-suggestion'>Google 검색 제안 자리</div>",
            "sources": [{"title": "example.com", "uri": "https://example.com"}],
            "notice": SEARCH_NOTICE}


def fake_chat(req: ChatRequest) -> ChatResponse:
    msg = req["message"]
    if any(k in msg for k in ("어디서", "판매처", "살까", "사려고")):
        return {"answer": "꼭 필요한 물건이면 판매처를 찾아볼게요. 검색 창에서 확인한 가격을 알려주시면 단위가격을 계산해 드릴게요.",
                "events": [fake_search_panel("쌀 3kg")], "validation": {"ok": True}}
    return {"answer": "목표까지 매달 150,000원이 필요해요. (출처: 사용자 입력, 계산식 포함)",
            "events": [], "validation": {"ok": True}}
