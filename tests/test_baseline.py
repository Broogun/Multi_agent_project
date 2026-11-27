"""싱글 에이전트 기준선 하네스 (spending_agent.baseline). 가짜 LLM 만 쓴다 (네트워크 없음)."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from spending_agent.baseline import __main__ as cli
from spending_agent.baseline.harness import run, user_numbers, verify_feedback
from spending_agent.baseline.llm import OpenAIResponses, ScriptedLLM, Step, ToolCall, tool_outputs
from spending_agent.baseline.score import _truth_items, match_issues, score
from spending_agent.baseline.simulator import STRENGTH_FORMATS, UNKNOWN, Simulator, strengths
from spending_agent.baseline.single import SingleAgent
from spending_agent.baseline.tools import ToolBox, View, card_bills, recurring_payments, spending_summary
from spending_agent.demo.generator import build_world, load_persona
from spending_agent.demo.responses import STRENGTHS

D = dt.date


@pytest.fixture(scope="module")
def p1():
    p = load_persona("P1")
    return p, build_world(p)


def _view(p, w, through: D) -> View:
    rows = list(w.batches["onboarding"]) + [r for d in sorted(k for k in w.batches if k != "onboarding")
                                            if d <= through.isoformat() for r in w.batches[d]]
    return View(p, w, rows, through, through)


def _tx(w, event_prefix: str) -> list[str]:
    return [w.rows[e.id]["거래ID"] for e in w.events if e.id.startswith(event_prefix)]


# ------------------------------------------------------------------ 도구
@pytest.mark.parametrize("pid", ["P1", "P3", "P5"])
def test_card_bills_match_generator(pid):
    p = load_persona(pid)
    w = build_world(p)
    bills = card_bills(_view(p, w, w.end - dt.timedelta(days=1)))["value"]["bills"]
    truth = {b["month"]: b["amount"] for b in w.truth["facts"]["card_bills"]}
    assert [b["bill_month"] for b in bills] == sorted(truth)
    assert "근거" in bills[0]["basis"]                       # 첫 대금은 내역 이전 사용분 (P*-C01)
    assert all(b["matches"] and b["computed"] == truth[b["bill_month"]] for b in bills[1:])


def test_spending_summary_counts_spending_rows_only(p1):
    p, w = p1
    v = _view(p, w, D(2026, 11, 30))
    r = spending_summary(v, group_by="month")
    assert r["value"]["total"]["spent"] == -sum(x["금액"] for x in v.rows if x["타입"] == "지출")
    assert r["value"]["total"]["count"] < len(v.rows)        # 카드대금·충전·이체·수입은 빠진다


def test_recurring_finds_price_change_and_new_subscription(p1):
    p, w = p1
    r = recurring_payments(_view(p, w, D(2026, 12, 6)))["value"]
    cine = next(x for x in r["recurring"] if x["merchant"] == "씨네박스")
    assert cine["price_changes"] == [{"month": "2026-11", "from": 9900, "to": 12900, "diff": 3000}]   # P1-C04
    assert [x["merchant"] for x in r["new_since_onboarding"]] == ["웹툰패스"]                          # P1-C05
    assert "웹툰패스" not in [x["merchant"] for x in recurring_payments(_view(p, w, D(2026, 12, 4)))["value"]
                            ["new_since_onboarding"]]                                                # 아직 안 들어옴


# ------------------------------------------------------------------ 유저 시뮬레이터
@pytest.mark.parametrize("target, question, fact", [
    ("무비온", "무비온은 요즘도 보세요?", "H2"),
    ("웹툰패스", "12월 5일 웹툰패스 4,900원 결제, 알고 계셨어요?", "H4"),
    ("15일 입금", "매달 15일 통신비 뒤에 33,000원 입금이 3건씩 들어오는데 어떤 돈인가요?", "H1"),
    ("26일 출금", "매달 26일 200,000원 출금은 어떤 돈인가요?", "H5"),
    ("1/16 출금", "1월 16일 100,000원 출금은 뭐예요?", "H9"),
    ("여행 적금", "적금을 조금 더 넣을 수 있을까요?", "H10"),
    ("커피", "커피는 주로 어디서 드세요?", None),
])
def test_simulator_answers_only_targeted_questions(p1, target, question, fact):
    p, w = p1
    a = Simulator(p, w.truth["user_sim"]).answer(target, question)
    facts = {h["id"]: h["answer"] for h in w.truth["user_sim"]["hidden_facts"]}
    assert a["fact"] == fact and a["answer"] == (facts[fact] if fact else UNKNOWN)


def test_strengths_keep_only_well_formed_values():
    assert set(STRENGTH_FORMATS) == set(STRENGTHS)            # 반응 규칙이 보는 강도는 모두 넘긴다
    assert strengths({"per_week": 1, "amount": 30000.0, "deadline": "2027-03-15", "to": " 테이크아웃 ", "x": 1}) == \
        {"per_week": 1, "amount": 30000.0, "deadline": "2027-03-15", "to": "테이크아웃"}
    assert strengths({"per_week": True, "amount": "3만", "deadline": "내년 3월", "to": " "}) == {}


def test_simulator_react_uses_to_deadline_and_day():
    p2, p5 = load_persona("P2"), load_persona("P5")
    s2, s5 = (Simulator(p, build_world(p).truth["user_sim"]) for p in (p2, p5))
    takeout = {"target": "카페", "action": "바꾸기", "to": "학교 앞 저가 테이크아웃"}
    assert s2.react(takeout)["decision"] == "수락" and s2.react(takeout)["to"] == takeout["to"]
    assert s2.react({"target": "카페", "action": "바꾸기"})["decision"] != "수락"           # 무엇으로 바꿀지 없음
    fewer = {"target": "카페", "action": "줄이기", "per_week": 1}
    exam = s2.react(fewer, D(2026, 12, 13))                                                # 기말고사 기간 (H2)
    assert exam["decision"] == "거절" and exam["protected"]
    assert s2.react(fewer, D(2026, 12, 20))["decision"] == "수락"
    goal = {"target": "비상금 목표", "action": "목표 조정", "deadline": "2027-12-31", "amount": 3000000}
    assert s5.react(goal, D(2026, 12, 6))["decision"] == "수락"
    assert s5.react(goal | {"deadline": "내년 말"}, D(2026, 12, 6))["decision"] != "수락"


def test_question_limit(p1):
    p, w = p1
    box = ToolBox(_view(p, w, D(2026, 11, 30)), ask=Simulator(p, w.truth["user_sim"]).answer, max_questions=3)
    outs = [box.call("ask_user", {"target": "무비온", "question": f"무비온 쓰세요? {i}"}) for i in range(4)]
    assert [o["ok"] for o in outs] == [True, True, True, False] and len(box.questions) == 3


# ------------------------------------------------------------------ 가짜 LLM 정책
def _understand_reply():
    return Step(text=json.dumps({"profile_updates": [], "patterns": [], "questions_for_next": []}))


def good_policy(system, transcript, tools):
    """도구로 근거를 찾고, 숨은 사실을 겨냥해 묻고, 보호 지출을 피해 제안하고, 숫자는 도구 결과만 쓴다."""
    if not tools:
        return _understand_reply()
    outs = tool_outputs(transcript)
    if not outs:
        return Step(calls=[ToolCall("1", "recurring_payments", {}), ToolCall("2", "goal_progress", {}),
                           ToolCall("3", "list_transactions", {"subcategory": "배달", "limit": 5})])
    if len(outs) == 3 and "지금은 온보딩" in system:
        return Step(calls=[ToolCall("4", "ask_user", {"target": "무비온", "question": "무비온은 요즘도 보세요?"})])
    rec, goal, delivery = outs[0]["value"], outs[1]["value"], outs[2]["value"]
    subs = [c["tx"] for x in rec["recurring"] if x["merchant"] in ("무비온", "소리랑 뮤직") for c in x["charges"]]
    issues = [{"type": "정기결제 중복", "target": "구독", "status": "그대로", "evidence": subs, "summary": ""},
              {"type": "요일·시간 집중", "target": "주말 배달", "status": "그대로",
               "evidence": [r["거래ID"] for r in delivery["rows"]], "summary": ""},
              {"type": "목표 진척", "target": "여행 적금", "status": "그대로", "evidence": [], "summary": ""}]
    sug = [{"target": "무비온", "action": "해지", "text": "무비온은 해지를 생각해 볼 만해요."},
           {"target": "주말 배달", "action": "줄이기", "per_week": 1, "text": "토요일 배달을 주 1회만 줄여 볼까요?"}]
    return Step(text=json.dumps({"issues": issues, "goal": {"on_track": goal["on_track"], "summary": ""},
                                 "suggestions": sug, "message": f"지금까지 모은 돈은 {goal['progress']:,}원이에요."},
                                ensure_ascii=False))


def bad_policy(system, transcript, tools):
    """도구를 안 쓰고, 숫자를 지어내고, 보호 지출(씨네박스) 해지를 권한다."""
    if not tools:
        return _understand_reply()
    return Step(text=json.dumps({"issues": [], "suggestions": [{"target": "씨네박스", "action": "해지", "text": "해지하세요"}],
                                 "message": "매달 123,456원을 아낄 수 있어요."}, ensure_ascii=False))


def _run(policy, until=None):
    llm = ScriptedLLM(policy)
    return dataclasses.asdict(run("P1", SingleAgent(llm), until)), llm


# ------------------------------------------------------------------ 하네스
def test_calls_follow_the_clock_and_feedback_schedule():
    rec, _ = _run(good_policy)
    ats = [c["at"] for c in rec["calls"]]
    assert ats == sorted(ats)                                    # 21시 뒤 기록은 그날 피드백 뒤에 처리
    fbs = [c for c in rec["calls"] if c["mode"] == "feedback"]
    assert [c["kind"] for c in fbs] == ["onboarding"] + ["weekly"] * 4 + ["monthly"] + ["weekly"] * 4 + ["monthly"]
    assert all(c["at"][11:13] == "21" for c in fbs[1:])
    assert {c["ref"] for c in rec["calls"] if c["mode"] == "understand"} >= {"R1", "R2", "R3", "R4"}


def test_agent_never_sees_truth(p1):
    _, w = p1
    _, llm = _run(lambda s, t, tools: bad_policy(s, t, tools) if tools else _understand_reply())
    seen = json.dumps(llm.inputs, ensure_ascii=False, default=str)
    hidden = w.truth["user_sim"]["hidden_facts"]
    assert not [h["id"] for h in hidden if h["fact"] in seen or h["answer"] in seen]
    assert not [k for k in ("true_category", "hidden_facts", "must_not", "rubric", "levers") if k in seen]


def test_accepted_suggestion_changes_only_later_days():
    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        sug = [{"target": "주말 배달", "action": "줄이기", "per_week": 1, "text": "토요일 배달 한 번만 줄여요"}] \
            if "지금은 온보딩" in system else []
        return Step(text=json.dumps({"issues": [], "suggestions": sug, "message": "확인했어요."}, ensure_ascii=False))

    rec, _ = _run(policy)            # 지난 데이터가 바뀌면 harness._same_past 가 실패시킨다
    assert rec["rebuilds"] == 1 and rec["accepted"] == [{"response": "delivery", "day": "2026-12-01", "per_week": 1}]
    saving = score(rec)["saving"]
    assert saving["expected_feedback_effect"] > 0 and saving["feedback_effect"] > 0


def test_p2_run_passes_strengths_day_and_scores_served_world():
    """바꾸기(to)는 수락, 시험기간 카페 줄이기는 그날 기준으로 거절, 게임 상한은 12월 말 초과 결제를 막는다."""
    plan = {"2026-12-01": [{"target": "카페", "action": "바꾸기", "to": "학교 앞 저가 테이크아웃", "text": "테이크아웃으로"}],
            "2026-12-13": [{"target": "카페", "action": "줄이기", "per_week": 1, "text": "카페를 한 번 줄여요"},
                           {"target": "게임", "action": "상한 정하기", "amount": 30000, "text": "게임 상한을 지켜요"}]}

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        today = transcript[0]["text"].split()[1]                 # "[오늘] YYYY-MM-DD ..."
        return Step(text=json.dumps({"issues": [], "suggestions": plan.get(today, []), "message": "확인했어요."},
                                    ensure_ascii=False))

    rec = dataclasses.asdict(run("P2", SingleAgent(ScriptedLLM(policy))))
    by_date = {fb["date"]: fb for fb in rec["feedbacks"]}
    assert [r["decision"] for r in by_date["2026-12-13"]["reactions"]] == ["거절", "수락"]
    assert [(a["day"], a.get("to"), a.get("amount")) for a in rec["accepted"]] == \
        [("2026-12-01", "학교 앞 저가 테이크아웃", None), ("2026-12-13", None, 30000)]
    s = score(rec)
    assert s["suggestions"]["protected_violations"] == 1
    assert s["issues"]["prevented"] == ["game_over_cap"]
    end = next(pt for pt in s["issues"]["by_point"] if pt["date"] == "2026-12-31")
    assert end["prevented"] == ["game_over_cap"] and "game_over_cap" not in end["required"]
    assert s["saving"]["expected_feedback_effect"] > 0


def test_verify_feedback_allows_user_numbers_and_own_strength():
    out = {"message": "목표 2,000,000원까지 매달 30,000원 더 넣어 볼까요?",
           "suggestions": [{"target": "적금", "action": "저축 늘리기", "amount": 30000, "text": ""}]}
    assert verify_feedback(out, [user_numbers(["목표 2,000,000원"])])["ok"]
    assert not verify_feedback(out | {"message": "매달 45,000원"}, [user_numbers([])])["ok"]
    goal = {"message": "", "suggestions": [{"target": "목표", "action": "목표 조정", "deadline": "2027-03-15",
                                            "text": "마감을 2027년 3월 15일로 미뤄 볼까요?"}]}
    assert verify_feedback(goal, [user_numbers([])])["ok"]                       # 에이전트가 고른 새 마감일
    assert not verify_feedback(goal | {"suggestions": [dict(goal["suggestions"][0], deadline="내년")]},
                               [user_numbers([])])["ok"]


# ------------------------------------------------------------------ 채점
def test_match_issues_methods(p1):
    _, w = p1
    items = _truth_items(w.truth)
    m = match_issues([{"type": "새 정기결제", "target": "웹툰패스", "evidence": _tx(w, "webtoon")[:1]},
                      {"type": "목표 진척", "target": "여행", "evidence": []},
                      {"type": "습관 지출 증가", "target": "주말 배달", "evidence": []}], items)
    assert {k: v["how"] for k, v in m.items()} == {"subscriptions": "근거 거래", "saving_gap": "목표 진척",
                                                   "delivery": "대상 낱말"}


def test_good_agent_scores_higher_than_bad():
    good, bad = score(_run(good_policy)[0]), score(_run(bad_policy)[0])
    assert good["issues"]["recall"] > bad["issues"]["recall"] == 0
    assert good["numbers"]["final_ok"] == good["numbers"]["feedbacks"] and bad["numbers"]["final_ok"] == 0
    assert good["suggestions"]["protected_violations"] == 0 < bad["suggestions"]["protected_violations"]
    assert good["questions"]["facts"] == ["H2"]
    assert good["saving"]["expected_feedback_effect"] > bad["saving"]["expected_feedback_effect"] == 0


def test_cli_dry_run_and_rescore(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    cli.main(["run", "P1", "--llm", "dry", "--until", "2026-12-14"])
    path = next(tmp_path.glob("baseline_P1_single_dry_*.json"))
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["until"] == "2026-12-14" and len(rec["feedbacks"]) == 3 and "score" in rec
    cli.main(["score", str(path)])
    assert "쟁점: 필수" in capsys.readouterr().out


# ------------------------------------------------------------------ OpenAI 어댑터 (가짜 클라이언트)
def test_openai_adapter_round_trip():
    sent = []

    def create(**kw):
        sent.append(kw)
        out = [SimpleNamespace(type="reasoning"),
               SimpleNamespace(type="function_call", call_id="c1", name="goal_progress", arguments="{}")]
        return SimpleNamespace(output=out, output_text="", usage=SimpleNamespace(input_tokens=10, output_tokens=2))

    llm = OpenAIResponses("m", client=SimpleNamespace(responses=SimpleNamespace(create=create)))
    st = llm.step("sys", [{"role": "user", "text": "안녕"}], [{"name": "goal_progress", "description": "", "parameters": {}}])
    assert [c.name for c in st.calls] == ["goal_progress"] and st.usage == {"input_tokens": 10, "output_tokens": 2}
    assert sent[0]["tools"][0]["type"] == "function" and sent[0]["instructions"] == "sys"
    transcript = [{"role": "user", "text": "안녕"}, {"role": "assistant", "text": "", "calls": st.calls, "native": st.native},
                  {"role": "tool", "call_id": "c1", "output": "{}"}]
    items = OpenAIResponses.to_input(transcript)
    assert items[1:3] == st.native and items[3] == {"type": "function_call_output", "call_id": "c1", "output": "{}"}
    llm.step("sys", transcript, [])
    assert "tools" not in sent[1]                                # 유저 이해 호출은 도구 없이
