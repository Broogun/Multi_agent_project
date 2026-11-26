"""에이전트 Tool 뼈대 (발표자료 24p Tool 목록 → 3분할 오케스트레이션 구조로 재배치).

세 층으로 나눈다.
  1) 하네스 직접 호출 (LLM 이 고르지 않음): load_snapshot, detect_recurring, verify_response, assert_user_scope
  2) LLM 툴: 에이전트별 TOOLSETS. user_id 는 인자가 아니라 칠판에서 주입한다.
  3) 쓰기: record_user_price / record_action 은 사용자가 UI 에서 입력·버튼을 눌렀을 때만 (LLM 은 제안만).

계산·조회의 실제 구현은 B(data/)가 Backend 형식으로 제공한다. 지금은 FakeBackend 로 개발한다.
모든 B 함수는 contracts.ToolResult 를 돌려주고, status 가 ok 가 아니면 에이전트는 숫자를 말하지 않는다.
검색은 모델에게 panel_id 만 돌려준다. 결과 본문은 pending_events 로 화면에 바로 간다 (docs/06).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Protocol

from .. import fakes
from ..contracts import ToolResult, UserPrice, Validation
from .state import MAX_QUESTIONS_PER_WEEK, AgentName, CaseState, ProfileDelta

PII = re.compile(r"(\d{2,3}-\d{3,4}-\d{4}|\d{6}-?[1-4]\d{6}|\d{3,6}-\d{2,6}-\d{2,8}|[\w.+-]+@[\w-]+\.[\w.]+)")


# ================================================================ B 가 구현할 형식
class Backend(Protocol):
    # 조회 (발표자료의 MCP 읽기 Tool. 지금은 Python 함수, 공유가 필요해지면 같은 함수를 MCP 로 노출)
    def get_spending_summary(self, user_id: str, months: int = 3) -> ToolResult: ...
    def get_recurring_payments(self, user_id: str) -> ToolResult: ...
    def get_goal_status(self, user_id: str) -> ToolResult: ...
    def get_outcome_history(self, user_id: str, limit: int = 20) -> ToolResult: ...
    def get_mobile_plan_candidates(self, data_gb: float, budget_monthly: int, network: str | None) -> ToolResult: ...
    def get_policy_rules(self, topic: str, region: str | None) -> ToolResult: ...
    def get_price_baseline(self, product: str, region: str | None) -> ToolResult: ...
    def get_strategy_library(self, domain: str) -> ToolResult: ...
    def get_user_prices(self, user_id: str, price_ids: list[str]) -> list[UserPrice]: ...
    # 계산
    def forecast_goal(self, goal_amount: int, deadline: str, current_saved: int = 0) -> ToolResult: ...
    def estimate_data_need(self, activities: dict[str, float]) -> ToolResult: ...
    def calc_net_saving(self, current_monthly: int, alt_monthly: int, months: int,
                        switching_costs: dict[str, int]) -> ToolResult: ...
    def calc_bulk_purchase(self, options: list[UserPrice], daily_use: float,
                           shelf_life_days: int | None = None) -> ToolResult: ...


def _todo(name: str) -> ToolResult:
    return {"status": "no_data", "value": None, "calc": {}, "sources": [], "as_of": None,
            "caveats": [f"{name}: 아직 구현 전 (B)"]}


class FakeBackend:
    """B 실제 구현 전 대역. fakes.py 에 있는 것은 그대로 쓰고, 없는 것은 no_data 를 돌려준다."""

    def get_spending_summary(self, user_id, months=3):
        return _todo("get_spending_summary")

    def get_recurring_payments(self, user_id):
        return _todo("get_recurring_payments")

    def get_goal_status(self, user_id):
        return _todo("get_goal_status")

    def get_outcome_history(self, user_id, limit=20):
        return _todo("get_outcome_history")

    def get_mobile_plan_candidates(self, data_gb, budget_monthly, network):
        return _todo("get_mobile_plan_candidates")

    def get_policy_rules(self, topic, region):
        return _todo("get_policy_rules")

    def get_price_baseline(self, product, region):
        return _todo("get_price_baseline")

    def get_strategy_library(self, domain):
        return _todo("get_strategy_library")

    def get_user_prices(self, user_id, price_ids):
        return []

    def forecast_goal(self, goal_amount, deadline, current_saved=0):
        return fakes.fake_forecast_goal(goal_amount, deadline, current_saved)

    def estimate_data_need(self, activities):
        return _todo("estimate_data_need")

    def calc_net_saving(self, current_monthly, alt_monthly, months, switching_costs):
        return _todo("calc_net_saving")

    def calc_bulk_purchase(self, options, daily_use, shelf_life_days=None):
        return fakes.fake_calc_bulk_purchase(list(options), daily_use, shelf_life_days)


SearchFn = Callable[[str], dict]


# ================================================================ 1) 하네스 직접 호출
def assert_user_scope(state: CaseState, user_id: str) -> None:
    """모든 조회 전에 호출. 칠판의 사용자와 다르면 막는다."""
    if not state.get("user_id") or state["user_id"] != user_id:
        raise PermissionError("user scope mismatch")


def load_snapshot(state: CaseState, backend: Backend) -> CaseState:
    """요청 시작 시 장기 DB 에서 최소 요약만 칠판에 올린다 (LLM 툴 아님)."""
    uid = state["user_id"]
    assert_user_scope(state, uid)
    state["user_snapshot"] = {
        "spending": backend.get_spending_summary(uid),
        "goal": backend.get_goal_status(uid),
        "outcomes": backend.get_outcome_history(uid),
    }
    return state


def detect_recurring(state: CaseState, backend: Backend) -> ToolResult:
    """반복결제 탐지는 규칙 코드. 유저 이해 단계 전에 하네스가 부른다."""
    r = backend.get_recurring_payments(state["user_id"])
    state["tool_results"].append(r)
    return r


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM.findall(text):
        n = m.replace(",", "").rstrip(".")
        if len(n.split(".")[0]) >= 2:  # 한 자리 수(개수·순번)는 검사하지 않음
            out.add(n)
    return out


def verify_response(answer: str, tool_results: list[ToolResult]) -> Validation:
    """답변 속 두 자리 이상 숫자가 ok 상태 Tool 결과(value·calc)에 있는지 대조한다."""
    allowed: set[str] = set()
    for r in tool_results:
        if r.get("status") == "ok":
            allowed |= _numbers(json.dumps(r.get("value"), ensure_ascii=False))
            allowed |= _numbers(" ".join(r.get("calc", {}).values()))
    bad = sorted(_numbers(answer) - allowed)
    return {"ok": not bad, "bad": bad} if not bad else {"ok": False, "action": "retry", "bad": bad}


# ================================================================ 2) LLM 툴
def _record(state: CaseState, r: ToolResult) -> dict:
    state["tool_results"].append(r)
    return dict(r)


def build_tools(agent: AgentName, state: CaseState, backend: Backend, search: SearchFn | None = None
                ) -> list[Callable]:
    """에이전트별 LLM 툴 목록. 함수 이름·docstring·타입 힌트가 그대로 툴 스키마가 된다."""
    uid = state["user_id"]
    search = search or fakes.fake_search_panel

    # ---- 팀장
    def call_agent(name: str, reason: str) -> dict:
        """다음에 일할 에이전트를 고른다. name: user_understanding | strategy | care_report.
        칠판의 open_issues 를 보고 필요한 경우에만 부른다."""
        target = {"strategy": "strategy_calc"}.get(name, name)
        if target not in ("user_understanding", "strategy_calc", "care_report"):
            return {"ok": False, "error": f"unknown agent: {name}"}
        state["next_agent"] = target
        state["active_plan"].append(f"{name}: {reason}")
        return {"ok": True}

    def finish(decision: str, reason: str) -> dict:
        """이번 요청을 끝낸다. decision: respond | ask_user | no_action.
        개입 가치가 없거나 최근 거절한 제안이면 no_action 으로 끝낸다."""
        if decision not in ("respond", "ask_user", "no_action"):
            return {"ok": False, "error": f"unknown decision: {decision}"}
        state["decision"] = decision
        state["next_agent"] = None
        state["critique_notes"].append(f"finish:{decision}:{reason}")
        return {"ok": True}

    # ---- 유저 이해
    def ask_user(question: str, options: list[str]) -> dict:
        """사용자에게 버튼 질문을 한다. 추측하지 말고 부족한 정보만 묻는다. 주 3개까지."""
        if state.get("questions_this_week", 0) >= MAX_QUESTIONS_PER_WEEK:
            return {"ok": False, "error": "이번 주 질문 한도 초과. 질문 없이 진행하라."}
        state["pending_questions"].append({"question": question, "options": options[:4]})
        state["questions_this_week"] = state.get("questions_this_week", 0) + 1
        state["decision"] = "ask_user"
        return {"ok": True}

    def propose_profile_delta(kind: str, field: str, value: str, evidence_ref: str, confidence: float) -> dict:
        """새로 파악한 유저 정보 변경안을 남긴다. DB 에 바로 쓰지 않는다.
        kind: hard_fact(사용자가 말한 사실) | soft_signal(추정, confidence 0~1)."""
        if kind not in ("hard_fact", "soft_signal"):
            return {"ok": False, "error": f"unknown kind: {kind}"}
        delta: ProfileDelta = {
            "kind": kind, "field": field, "value": value, "evidence_ref": evidence_ref,
            "requires_user_confirm": kind == "hard_fact",
            "confidence": 1.0 if kind == "hard_fact" else max(0.0, min(1.0, confidence)),
        }
        state["profile_deltas"].append(delta)
        return {"ok": True}

    # ---- 전략 · 계산·비교
    def forecast_goal(goal_amount: int, deadline: str, current_saved: int = 0) -> dict:
        """목표 금액·기한(YYYY-MM)으로 월 필요액과 달성 가능성을 계산한다."""
        return _record(state, backend.forecast_goal(goal_amount, deadline, current_saved))

    def estimate_data_need(activities: dict[str, float]) -> dict:
        """활동별 하루 사용 시간(예: {"video_hd": 1.5})으로 월 데이터 필요량을 추정한다."""
        return _record(state, backend.estimate_data_need(activities))

    def get_mobile_plan_candidates(data_gb: float, budget_monthly: int, network: str | None = None) -> dict:
        """조건에 맞는 요금제 후보를 팀이 관리하는 요금제 DB·공공 API 에서 찾는다."""
        return _record(state, backend.get_mobile_plan_candidates(data_gb, budget_monthly, network))

    def get_policy_rules(topic: str, region: str | None = None) -> dict:
        """교통 환급·에너지캐시백 같은 제도 규칙(적용 기간 포함)을 조회한다."""
        return _record(state, backend.get_policy_rules(topic, region))

    def get_price_baseline(product: str, region: str | None = None) -> dict:
        """공공 가격 데이터로 상품의 시세 범위를 조회한다. 판매처 실제 가격이 아니다."""
        return _record(state, backend.get_price_baseline(product, region))

    def calc_net_saving(current_monthly: int, alt_monthly: int, months: int, switching_costs: dict[str, int]) -> dict:
        """순절감액 = (현재 월 비용 − 대안 월 비용) × 기간 − 전환 비용(위약금·유심비·배송비 등)."""
        return _record(state, backend.calc_net_saving(current_monthly, alt_monthly, months, switching_costs))

    def calc_bulk_purchase(price_ids: list[str], daily_use: float, shelf_life_days: int | None = None) -> dict:
        """사용자가 확인한 가격(price_ids) 2개 이상을 단위가격·소진 기간으로 비교한다.
        확인한 가격이 없으면 호출하지 말고 open_search_panel 로 사용자가 확인하게 하라."""
        prices = backend.get_user_prices(uid, price_ids)
        if len(prices) < 2:
            return {"status": "needs_check", "caveats": ["사용자가 확인한 가격이 2개 이상 필요"]}
        return _record(state, backend.calc_bulk_purchase(prices, daily_use, shelf_life_days))

    def open_search_panel(query: str) -> dict:
        """사용자가 직접 확인할 수 있게 검색 창을 띄운다. 상품명·용량·조건만 넣는다.
        결과 내용은 돌려받지 않는다. 가격은 사용자가 확인해 입력해야 계산에 쓴다."""
        q = PII.sub("", query).strip()
        ev = search(q)
        state["pending_events"].append(ev)
        state["search_panel_ids"].append(ev["panel_id"])
        return {"panel_id": ev["panel_id"]}

    # ---- 전략 · 습관·행동
    def get_strategy_library(domain: str) -> dict:
        """팀이 정리한 절약 전략 라이브러리(근거 등급 A~D)에서 분야별 전략을 가져온다. 팁을 지어내지 않는다."""
        return _record(state, backend.get_strategy_library(domain))

    def get_spending_pattern(category: str) -> dict:
        """칠판에 로드된 지출 요약에서 카테고리 패턴을 본다 (새 조회 없음)."""
        return {"category": category, "spending": state.get("user_snapshot", {}).get("spending")}

    def propose_action(strategy: str, detail: str, tool_result_index: int | None = None) -> dict:
        """사용자에게 제안할 대안을 후보로 남긴다. 수락·거절 기록은 사용자가 버튼을 눌렀을 때만 된다."""
        state["proposed_actions"].append({"strategy": strategy, "detail": detail, "ref": tool_result_index})
        return {"ok": True}

    toolsets: dict[str, list[Callable]] = {
        "lead": [call_agent, finish],
        "user_understanding": [ask_user, propose_profile_delta],
        "strategy_calc": [forecast_goal, estimate_data_need, get_mobile_plan_candidates, get_policy_rules,
                          get_price_baseline, calc_net_saving, calc_bulk_purchase, open_search_panel],
        "strategy_habit": [get_strategy_library, get_spending_pattern, open_search_panel, propose_action],
        "care_report": [forecast_goal],
    }
    return toolsets[agent]


TOOL_NAMES: dict[str, list[str]] = {
    a: [f.__name__ for f in build_tools(a, CaseState(user_id="_", tool_results=[]), FakeBackend())]
    for a in ("lead", "user_understanding", "strategy_calc", "strategy_habit", "care_report")
}
