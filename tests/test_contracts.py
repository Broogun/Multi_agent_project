"""계약 테스트: 가짜 구현과 (생기면) 실제 구현이 contracts.py 형식을 지키는지.

실제 구현을 추가하면 IMPLEMENTATIONS 에 함수를 등록한다. 같은 테스트를 통과해야 바꿔 끼울 수 있다.
"""
import pytest

from spending_agent import contracts, fakes

TOOL_RESULT_KEYS = set(contracts.ToolResult.__annotations__)
PANEL_KEYS = set(contracts.SearchPanelEvent.__annotations__)
CHAT_KEYS = set(contracts.ChatResponse.__annotations__)

# 실제 구현이 생기면 여기에 추가: ("이름", 함수)
CALC_IMPLEMENTATIONS = [
    ("fake_calc_bulk_purchase", lambda: fakes.fake_calc_bulk_purchase([], 10)),
    ("fake_forecast_goal", lambda: fakes.fake_forecast_goal(2_000_000, "2027-08-31", 500_000)),
    ("fake_record_user_price", lambda: fakes.fake_record_user_price(
        {"user_id": "u", "product_name": "쌀", "price": 6900, "checked_date": "2026-11-24", "source_type": "user_verified"})),
]
SEARCH_IMPLEMENTATIONS = [("fake_search_panel", fakes.fake_search_panel)]
CHAT_IMPLEMENTATIONS = [("fake_chat", fakes.fake_chat)]


@pytest.mark.parametrize("name,call", CALC_IMPLEMENTATIONS)
def test_tool_result_shape(name, call):
    r = call()
    assert set(r) == TOOL_RESULT_KEYS, name
    assert r["status"] in ("ok", "needs_check", "no_data", "stale")
    for v in r["calc"].values():
        assert isinstance(v, str)


def test_search_price_rejected():
    r = fakes.fake_record_user_price({"user_id": "u", "product_name": "쌀", "price": 1, "checked_date": "2026-11-24",
                                      "source_type": "search_result"})
    assert r["status"] == "needs_check" and r["value"] is None


@pytest.mark.parametrize("name,fn", SEARCH_IMPLEMENTATIONS)
def test_search_panel_shape(name, fn):
    e = fn("쌀 3kg")
    assert set(e) == PANEL_KEYS, name
    assert e["type"] == "search_panel"
    assert e["notice"] == contracts.SEARCH_NOTICE


@pytest.mark.parametrize("name,fn", CHAT_IMPLEMENTATIONS)
def test_chat_shape(name, fn):
    for msg in ("쌀 어디서 살까", "여행 목표 가능해?"):
        r = fn({"user_id": "u", "message": msg, "session_id": "s"})
        assert set(r) == CHAT_KEYS, name
        for e in r["events"]:
            assert set(e) == PANEL_KEYS
            assert e["text"] not in r["answer"]  # 검색 결과를 답변에 섞지 않음
