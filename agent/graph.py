"""LangGraph 에이전트 뼈대 (Gemini).

    agent ──(함수 호출)──▶ tools ──▶ search? ──▶ agent ...
      │
      └──(최종 답변)──▶ validate ──(숫자 검증 실패, 1회)──▶ agent
                            └──▶ END

- agent    : Gemini 가 Tool 을 고르고 최종 답변을 쓴다. 숫자는 Tool 결과만 인용 (system prompt + validate).
- tools    : tools/core.py 함수 실행. user_id 는 서버가 넣는다 (LLM이 다른 사용자 데이터를 고를 수 없음).
- search   : open_search_panel 호출을 처리. Gemini 그라운딩 결과를 UI 스트림(custom)으로만 내보내고,
             state·history 에는 "검색 창을 띄웠다"는 표시만 남긴다. → 에이전트는 검색 결과를 읽지 않는다.
- validate : 답변 숫자를 Tool 결과·사용자 입력과 대조. 실패하면 한 번 다시 쓰게 하고, 그래도 실패하면 그 문장을 뺀다.

개입·비개입 정책(1-2 탭), Outcome Tracking 은 다음 단계에서 노드로 추가한다.
"""
from __future__ import annotations

import datetime as dt
import functools
import inspect
import json
import operator
import uuid
from typing import Annotated, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from tools import core
from tools.descriptions import DESCRIPTIONS

from . import validator
from .llm import LLM

MAX_STEPS = 8
SEARCH_TOOL = "open_search_panel"

SYSTEM = """너는 사용자의 재무 목표를 돕는 소비 관리 에이전트다. 오늘은 {today}.
규칙:
1. 금액·기간·절감액 같은 숫자는 Tool 결과(value, calc)에 있는 것만 그대로 인용한다. 직접 계산하거나 추정하지 않는다.
2. Tool 결과 status 가 needs_check 또는 no_data 면 숫자를 말하지 말고 caveats 의 이유와 사용자가 확인할 것을 안내한다.
3. 답변 끝에 근거의 출처명과 기준일(sources, as_of)을 짧게 적는다.
4. 결정(해지·변경·구매)은 사용자가 한다. 선택지와 근거만 제시하고 강요하지 않는다. 소비를 비난하지 않는다.
5. 사용자가 꼭 사야 하는 물건의 판매처를 찾고 싶어 하면 open_search_panel 로 검색 창을 띄운다.
   검색 결과는 너에게 오지 않는다. 사용자가 판매처에서 확인한 가격을 말해주면 record_user_price 로 저장하고 계산한다.
6. 필요 없는 물건이면 가격 비교보다 구매 보류와 목표 영향(forecast_goal)을 먼저 제안한다.
7. 한국어로 짧게 답한다."""


class State(TypedDict, total=False):
    user_id: str
    history: Annotated[list[dict], operator.add]       # LLM 이 보는 대화 (검색 결과는 절대 들어가지 않음)
    tool_results: Annotated[list[dict], operator.add]  # 검증기용 Tool 결과
    pending_search: list[dict]
    search_panels: Annotated[list[str], operator.add]  # 띄운 검색 창 ID (내용 없음)
    answer: str
    steps: int
    retries: int
    validation: dict


def open_search_panel(query: str) -> dict:
    """구매 후보 판매처를 찾는 검색 창을 사용자 화면에 띄운다. 검색 결과는 에이전트에게 전달되지 않는다.
    query 에는 상품명과 용량만 넣는다 (개인정보·금액 금지)."""
    return {}


open_search_panel.__agent_description__ = (
    "구매 후보 판매처를 찾는 검색 창을 사용자 화면에 띄운다. 결과는 사용자에게만 보이고 너에게는 오지 않는다. "
    "query 에는 상품명과 용량만 넣는다 (개인정보·금액 금지).")


def _bind_user(fn, user_id: str):
    """user_id 인자를 숨긴 래퍼. LLM 에게는 user_id 없는 시그니처로 보인다."""
    sig = inspect.signature(fn)
    if "user_id" not in sig.parameters:
        w = functools.wraps(fn)(lambda **kw: fn(**kw))
    else:
        w = functools.wraps(fn)(lambda **kw: fn(user_id=user_id, **kw))
    w.__signature__ = sig.replace(parameters=[p for n, p in sig.parameters.items() if n != "user_id"])
    del w.__wrapped__  # inspect 가 원래 시그니처를 따라가지 않게
    w.__agent_description__ = DESCRIPTIONS.get(fn)
    return w


def build(llm: LLM, today: str | None = None):
    today = today or dt.date.today().isoformat()

    def toolset(user_id):
        return {f.__name__: _bind_user(f, user_id) for f in DESCRIPTIONS} | {SEARCH_TOOL: open_search_panel}

    # ------------------------------------------------------------ 노드
    def agent(state: State):
        tools = list(toolset(state["user_id"]).values())
        steps = state.get("steps", 0) + 1
        if steps > MAX_STEPS:
            return {"answer": "요청을 처리하는 단계가 너무 길어져 멈췄습니다. 질문을 나눠 주세요.", "steps": steps}
        reply = llm.chat(SYSTEM.format(today=today), state["history"], tools)
        if reply.calls:
            return {"history": [{"role": "model", "calls": reply.calls}], "steps": steps}
        return {"history": [{"role": "model", "text": reply.text}], "answer": reply.text, "steps": steps}

    def run_tools(state: State):
        calls = state["history"][-1].get("calls", [])
        ts = toolset(state["user_id"])
        hist, results, pending = [], [], []
        for c in calls:
            if c["name"] == SEARCH_TOOL:
                pending.append(c)
                continue
            fn = ts.get(c["name"])
            try:
                r = fn(**c["args"]) if fn else {"status": "error", "caveats": [f"알 수 없는 Tool: {c['name']}"]}
            except TypeError as e:
                r = {"status": "error", "caveats": [f"인자 오류: {e}"]}
            hist.append({"role": "tool", "name": c["name"], "result": r})
            results.append(r)
        return {"history": hist, "tool_results": results, "pending_search": pending}

    def search(state: State):
        """그라운딩 결과는 UI 스트림으로만. history 에는 표시 사실만."""
        writer = get_stream_writer()
        hist, panels = [], []
        for c in state.get("pending_search", []):
            q = str(c["args"].get("query", ""))[:80]
            panel_id = uuid.uuid4().hex[:8]
            g = llm.grounded_search(q)
            writer({"type": "search_panel", "panel_id": panel_id, "query": q, "text": g.text,
                    "suggestions_html": g.suggestions_html, "sources": g.sources,
                    "notice": "검색 결과이며, 실제 가격은 판매처에서 확인하세요."})
            panels.append(panel_id)
            hist.append({"role": "tool", "name": SEARCH_TOOL, "result": {
                "status": "displayed", "panel_id": panel_id,
                "note": "검색 창을 사용자 화면에 띄웠다. 내용은 알 수 없다. 사용자가 판매처에서 확인한 가격을 말해주면 이어서 돕는다."}})
        return {"history": hist, "search_panels": panels, "pending_search": []}

    def validate(state: State):
        user_texts = [h["text"] for h in state["history"] if h["role"] == "user"]
        bad = validator.check(state.get("answer", ""), state.get("tool_results", []), user_texts)
        if not bad:
            return {"validation": {"ok": True}}
        if state.get("retries", 0) < 1:
            fb = ("[검증기] 다음 문장의 숫자가 Tool 결과나 사용자 입력에 없다: " + " / ".join(bad)
                  + " — Tool 결과의 숫자만 인용해서 다시 답하라. 필요하면 Tool 을 호출하라.")
            return {"history": [{"role": "user", "text": fb}], "retries": state.get("retries", 0) + 1,
                    "validation": {"ok": False, "bad": bad, "action": "retry"}}
        kept = state["answer"]
        for s in bad:
            kept = kept.replace(s, "")
        kept = kept.strip() + "\n\n(확인되지 않은 숫자가 든 문장은 뺐습니다.)"
        return {"answer": kept, "validation": {"ok": False, "bad": bad, "action": "removed"}}

    # ------------------------------------------------------------ 연결
    def after_agent(state: State):
        last = state["history"][-1]
        if last.get("calls"):
            return "tools"
        return "validate"

    def after_tools(state: State):
        return "search" if state.get("pending_search") else "agent"

    def after_validate(state: State):
        v = state.get("validation", {})
        return "agent" if v.get("action") == "retry" else END

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", run_tools)
    g.add_node("search", search)
    g.add_node("validate", validate)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", after_agent, ["tools", "validate"])
    g.add_conditional_edges("tools", after_tools, ["search", "agent"])
    g.add_edge("search", "agent")
    g.add_conditional_edges("validate", after_validate, ["agent", END])
    return g.compile()


def run(llm: LLM, user_id: str, message: str, history: list[dict] | None = None):
    """UI 이벤트(검색 창)와 최종 상태를 함께 돌려준다."""
    graph = build(llm)
    ui_events, final = [], None
    init = {"user_id": user_id, "history": (history or []) + [{"role": "user", "text": message}]}
    for mode, chunk in graph.stream(init, stream_mode=["custom", "values"]):
        if mode == "custom":
            ui_events.append(chunk)
        else:
            final = chunk
    return final, ui_events
