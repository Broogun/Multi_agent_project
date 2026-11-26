"""Tool 뼈대 테스트 (가짜 백엔드, 네트워크 없음)."""
import inspect

import pytest

from spending_agent.agent import tools as T
from spending_agent.agent.state import MAX_QUESTIONS_PER_WEEK, new_state


def _tools(agent, **kw):
    st = new_state("u1", "chat")
    return st, {f.__name__: f for f in T.build_tools(agent, st, T.FakeBackend(), **kw)}


def test_no_tool_takes_user_id():
    for agent in T.TOOL_NAMES:
        _, fs = _tools(agent)
        for f in fs.values():
            assert "user_id" not in inspect.signature(f).parameters, f.__name__
            assert f.__doc__, f.__name__


def test_toolsets_stay_small_and_harness_hooks_are_not_tools():
    for names in T.TOOL_NAMES.values():
        assert len(names) <= 8
        assert not {"verify_response", "assert_user_scope", "record_user_price", "record_action"} & set(names)


def test_calc_result_is_recorded_for_verification():
    st, fs = _tools("strategy_calc")
    fs["forecast_goal"](2_000_000, "2027-09", 500_000)
    assert len(st["tool_results"]) == 1
    assert T.verify_response("매달 150,000원이 필요해요", st["tool_results"])["ok"]


def test_verify_response_flags_invented_numbers():
    st, fs = _tools("strategy_calc")
    fs["forecast_goal"](2_000_000, "2027-09", 500_000)
    v = T.verify_response("매달 180,000원이 필요해요", st["tool_results"])
    assert not v["ok"] and "180000" in v["bad"]


def test_non_ok_results_do_not_allow_numbers():
    st, fs = _tools("strategy_calc")
    fs["calc_net_saving"](55000, 4400, 12, {"penalty": 30000})  # FakeBackend: no_data
    assert not T.verify_response("12개월 순절감 577,200원", st["tool_results"])["ok"]


def test_bulk_purchase_requires_user_prices():
    st, fs = _tools("strategy_calc")
    r = fs["calc_bulk_purchase"](["p1"], 30)
    assert r["status"] == "needs_check" and st["tool_results"] == []


def test_search_returns_only_panel_id_and_strips_pii():
    seen = []

    def fake_search(q):
        seen.append(q)
        return {"type": "search_panel", "panel_id": "x1", "query": q, "text": "본문", "suggestions_html": None,
                "sources": [], "notice": ""}

    st, fs = _tools("strategy_calc", search=fake_search)
    out = fs["open_search_panel"]("원두 1kg 010-1234-5678")
    assert out == {"panel_id": "x1"}
    assert "010-1234-5678" not in seen[0]
    assert st["search_panel_ids"] == ["x1"] and st["pending_events"][0]["text"] == "본문"


def test_question_limit():
    st, fs = _tools("user_understanding")
    for i in range(MAX_QUESTIONS_PER_WEEK):
        assert fs["ask_user"](f"q{i}", ["예", "아니오"])["ok"]
    assert not fs["ask_user"]("q4", ["예"])["ok"]
    assert len(st["pending_questions"]) == MAX_QUESTIONS_PER_WEEK


def test_profile_delta_is_proposal_only():
    st, fs = _tools("user_understanding")
    fs["propose_profile_delta"]("soft_signal", "effort_sensitivity", "high", "msg-1", 1.7)
    fs["propose_profile_delta"]("hard_fact", "protected_spending", "주말 카페", "msg-2", 0.2)
    soft, hard = st["profile_deltas"]
    assert soft["confidence"] == 1.0 and not soft["requires_user_confirm"]
    assert hard["confidence"] == 1.0 and hard["requires_user_confirm"]


@pytest.mark.parametrize("name,ok", [("strategy", True), ("care_report", True), ("db_writer", False)])
def test_lead_call_agent(name, ok):
    _, fs = _tools("lead")
    assert fs["call_agent"](name, "r")["ok"] is ok


def test_scope_guard():
    st = new_state("u1", "chat")
    T.assert_user_scope(st, "u1")
    with pytest.raises(PermissionError):
        T.assert_user_scope(st, "u2")
