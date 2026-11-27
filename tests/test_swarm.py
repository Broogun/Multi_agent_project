"""압축 메모리 기반 Swarm (agent.swarm). 가짜 LLM 만 쓴다 (네트워크 없음)."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json

from spending_agent.agent.memory import MAX_BOUNCE, MAX_HOPS, Board
from spending_agent.agent.swarm import FALLBACK_MESSAGE, SwarmAgent, calculated_numbers, dry_run_llm
from spending_agent.baseline import __main__ as cli
from spending_agent.baseline.harness import run
from spending_agent.baseline.llm import ScriptedLLM, Step, ToolCall, tool_outputs
from spending_agent.baseline.score import score

D = dt.date
ONBOARD = D(2026, 12, 1)


def _role(system: str) -> str:
    for r in ("counselor", "analyst", "strategist", "reviewer"):
        if f"({r})" in system:
            return r
    raise AssertionError(system[:80])


def _understand_reply():
    return Step(text=json.dumps({"profile_updates": [{"field": "보호 지출", "value": "씨네박스", "kind": "사실"}],
                                 "patterns": ["주말 배달"], "questions_for_next": []}, ensure_ascii=False))


def _call(name, args, i="c"):
    return Step(calls=[ToolCall(f"{name}-{i}", name, args)])


def c08_policy():
    """P1-C08: 구독만으로 부족 → 리뷰어 반려 → 배달 추가 → 통과 → 상담이 전달."""
    visits = {"strategist": 0, "reviewer": 0}

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role, outs = _role(system), tool_outputs(transcript)
        if role == "analyst":
            if not outs:
                return _call("update_issue", {"type": "정기결제 중복", "target": "구독", "status": "새로 생김"})
            return _call("handoff_to_strategist", {"reason": "부족분을 채울 대안 필요"})
        if role == "strategist":
            if not outs:
                visits["strategist"] += 1
                expr = "13500 + 12800" if visits["strategist"] == 1 else "242400 / 10"
                return _call("calculate", {"expression": expr})
            if len(outs) == 1:
                saving = outs[0]["value"]["result"]
                target, action = ("무비온·소리랑", "해지") if visits["strategist"] == 1 else ("주말 배달", "줄이기")
                return _call("propose_action", {"target": target, "action": action, "per_week": 1,
                                                "saving_monthly": saving, "text": f"{target} 조정"})
            return _call("handoff_to_reviewer", {"reason": "검증 요청"})
        if role == "reviewer":
            if not outs:
                return _call("calculate", {"expression": "240000 - 200000"})
            if len(outs) == 1:
                return _call("check_gap_coverage", {"monthly_shortfall": 40000})
            if len(outs) == 2:
                visits["reviewer"] += 1
                ok = outs[1]["covered"]
                return _call("record_review", {"ok": ok, "reason": "충분" if ok else f"남은 {outs[1]['remaining_gap']}"})
            return _call("handoff_to_counselor" if outs[1]["covered"] else "handoff_to_strategist",
                         {"reason": "통과" if outs[1]["covered"] else "구독만으로 부족, 배달 추가"})
        return _call("respond_to_user", {"message": "부족분을 채울 두 가지를 골라 봤어요.", "goal_on_track": False})
    return policy


def _onboarding(policy):
    llm = ScriptedLLM(policy)
    rec = dataclasses.asdict(run("P1", SwarmAgent(llm), ONBOARD))
    return rec, rec["feedbacks"][0], next(c for c in rec["calls"] if c["mode"] == "feedback")["meta"]


def test_reviewer_rejects_then_strategist_combines_issues():
    _, fb, meta = _onboarding(c08_policy())
    path = [(t["from_agent"], t["to_agent"]) for t in meta["trace"]]
    assert path == [("analyst", "strategist"), ("strategist", "reviewer"), ("reviewer", "strategist"),
                    ("strategist", "reviewer"), ("reviewer", "counselor")]
    assert meta["bounces"] == 1 and meta["hops"] == 5 and not meta["forced"]
    assert [s["target"] for s in fb["output"]["suggestions"]] == ["무비온·소리랑", "주말 배달"]
    assert fb["validation_final"]["ok"]
    assert set(meta["by_agent"]) == {"analyst", "strategist", "reviewer", "counselor"}
    gap = next(t["output"]["value"] for t in reversed(fb["tools"]) if t["name"] == "check_gap_coverage")
    assert gap == {"monthly_shortfall": 40000, "proposed_saving_sum": 26300 + 24240, "remaining_gap": 0,
                   "covered": True, "calc": "40,000 − (26,300 + 24,240) = 남은 부족분 0"}


def test_pre_run_flags_rules_before_llm():
    seen = []

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        seen.append(transcript[0]["text"])
        return _call("respond_to_user", {"message": "확인했어요."}) if _role(system) == "counselor" \
            else _call("handoff_to_counselor", {"reason": "바꿀 것 없음"})

    llm = ScriptedLLM(policy)
    rec = dataclasses.asdict(run("P1", SwarmAgent(llm), D(2026, 12, 6)))
    weekly = rec["feedbacks"][1]
    assert [t["name"] for t in weekly["tools"][:4]] == ["recurring_payments", "card_bills", "goal_progress",
                                                       "unnamed_transfers"]
    first_weekly = next(t for t in seen if "주간 피드백" in t or "웹툰패스" in t)
    board = json.loads(first_weekly.split("[작업판]\n", 1)[1].split("\n[받은 바통]", 1)[0])["규칙 전처리"]
    assert [x["merchant"] for x in board["새 결제(온보딩 이후 처음)"]] == ["웹툰패스"]           # C05
    assert {"merchant": "씨네박스", "month": "2026-11", "from": 9900, "to": 12900, "diff": 3000} in board["가격 변화"]  # C04
    assert "regular" in first_weekly                                           # 정형 이체 표시 (질문 낭비 줄이기)


def test_hop_limit_forces_counselor():
    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role = _role(system)
        if role == "counselor":
            return _call("respond_to_user", {"message": "지금까지 본 걸 정리했어요."})
        return _call("handoff_to_strategist" if role == "analyst" else "handoff_to_analyst", {"reason": "핑퐁"})

    _, fb, meta = _onboarding(policy)
    assert meta["forced"] and "한도" in meta["forced"] and meta["hops"] <= MAX_HOPS + 1
    assert fb["output"]["message"] == "지금까지 본 걸 정리했어요."


def test_bounce_limit():
    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role = {"analyst": "handoff_to_strategist", "strategist": "handoff_to_reviewer",
                "reviewer": "handoff_to_strategist"}.get(_role(system))
        return _call(role, {"reason": "반려"}) if role else _call("respond_to_user", {"message": "보류할게요."})

    _, _, meta = _onboarding(policy)
    assert meta["bounces"] == MAX_BOUNCE + 1 and "반려" in meta["forced"]


def test_invented_numbers_are_refused():
    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role, outs = _role(system), tool_outputs(transcript)
        if role == "analyst":
            return _call("handoff_to_strategist", {"reason": "대안"})
        if role == "strategist":
            if not outs:
                return _call("propose_action", {"target": "배달", "action": "줄이기", "per_week": 1,
                                                "saving_monthly": 123457, "text": "배달 줄이기"})
            assert outs[0]["ok"] is False and "계산" in outs[0]["error"]
            return _call("handoff_to_counselor", {"reason": "근거 있는 후보 없음"})
        return _call("respond_to_user", {"message": "이번엔 제안 없이 정리만 할게요."})

    _, fb, _ = _onboarding(policy)
    assert fb["output"]["suggestions"] == []


def test_unreviewed_proposals_cannot_be_sent():
    agent = SwarmAgent(ScriptedLLM(lambda *a: Step()))
    b = Board(proposals=[{"target": "배달", "action": "줄이기", "saving_monthly": 24240, "text": "x"}])
    assert agent._respond(b, {"message": "hi"})["ok"] is False
    b.review = {"ok": True, "reason": "충분"}
    assert agent._respond(b, {"message": "hi"})["out"]["suggestions"][0]["target"] == "배달"


def test_message_with_made_up_number_is_rewritten_once():
    tries = []

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        if _role(system) != "counselor":
            return _call("handoff_to_counselor", {"reason": "전달"})
        tries.append(transcript[0]["text"])
        msg = "매달 98,765원을 아낄 수 있어요." if len(tries) == 1 else "내역을 정리했어요."
        return _call("respond_to_user", {"message": msg})

    _, fb, meta = _onboarding(policy)
    assert meta["retries"] == 1 and "98765" in tries[1] and fb["validation_final"]["ok"]


def test_full_run_with_memory_and_score():
    llm = ScriptedLLM(c08_policy())
    agent = SwarmAgent(llm)
    rec = dataclasses.asdict(run("P1", agent, D(2026, 12, 14)))
    assert rec["agent"] == "swarm" and len(rec["feedbacks"]) == 3
    assert agent.memory["profile"]["보호 지출"]["value"] == "씨네박스"            # 유저 이해가 메모리에 남김
    assert agent.memory["active_issues"]                                        # 쟁점 보드가 다음 피드백으로 이어짐
    s = score(rec)
    assert s["suggestions"]["protected_violations"] == 0 and s["numbers"]["final_ok"] == s["numbers"]["feedbacks"]
    biggest = max(c["meta"].get("input_tokens", 0) for c in rec["calls"])
    assert biggest == 0                                                         # 가짜 LLM: 토큰 0 (형식 확인용)


def test_cli_runs_swarm(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    cli.main(["run", "P1", "--llm", "dry", "--agent", "swarm", "--until", "2026-12-06"])
    rec = json.loads(next(tmp_path.glob("baseline_P1_swarm_dry_*.json")).read_text(encoding="utf-8"))
    assert all(not c["meta"].get("forced") for c in rec["calls"] if c["mode"] == "feedback")   # 연습 정책이 정상 종료
    assert "P1 · swarm" in capsys.readouterr().out


# ------------------------------------------------------------------ Codex 리뷰 반영
def _ok(name, value):
    return {"name": name, "output": {"status": "ok", "value": value}}


def test_only_calculated_values_are_allowed():
    log = [_ok("list_transactions", {"rows": [{"날짜": "2026-12-12", "거래ID": "T13700", "금액": -26800}]}),
           _ok("check_gap_coverage", {"remaining_gap": 13700}),
           _ok("calculate", {"result": 0.75}), _ok("calculate", {"result": 24240}),
           _ok("forecast_goal", {"monthly_required": 240000, "gap": 1200000, "months_left": 5})]
    assert calculated_numbers(log) == {24240, 240000, 1200000}       # 날짜·거래ID·개별 금액·검산 기록·소수는 안 됨


def test_changing_proposals_clears_stale_gap():
    from types import SimpleNamespace
    agent = SwarmAgent(ScriptedLLM(lambda *a: Step()))
    ctx = SimpleNamespace(tools=SimpleNamespace(log=[_ok("calculate", {"result": 10000})]))
    b = Board(proposals=[{"saving_monthly": 40000}], gap={"covered": True, "proposed_saving_sum": 40000},
              review={"ok": True})
    agent._c_drop_action(ctx, b, {"index": 0})
    assert b.gap == {} and b.review == {}
    agent._c_propose_action(ctx, b, {"target": "배달", "action": "줄이기", "saving_monthly": 10000, "text": "x"})
    assert b.gap == {}
    assert agent._c_record_review(ctx, b, {"ok": True, "reason": "?"})["ok"] is False      # 다시 검산해야 통과


def test_one_off_settlement_is_not_regular():
    seen = []

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        seen.append(transcript[0]["text"])
        return _call("respond_to_user", {"message": "확인했어요."}) if _role(system) == "counselor" \
            else _call("handoff_to_counselor", {"reason": "끝"})

    run("P4", SwarmAgent(ScriptedLLM(policy)), ONBOARD)
    board = json.loads(seen[0].split("[작업판]\n", 1)[1].split("\n[받은 바통]", 1)[0])
    groups = {(g["direction"], g["amount"]): g["regular"] for g in board["규칙 전처리"]["이름 없는 이체"]}
    assert groups.get(("입금", 13900)) is False and groups.get(("입금", 12200)) in (False, None)   # 동아리 정산 묶음


def test_swarm_dry_run_ends_with_response():
    rec = dataclasses.asdict(run("P1", SwarmAgent(dry_run_llm()), D(2026, 12, 6)))
    metas = [c["meta"] for c in rec["calls"] if c["mode"] == "feedback"]
    assert all(not m["forced"] and m["hops"] == 1 for m in metas)
    assert all(f["output"]["message"] == "내역을 확인했어요." for f in rec["feedbacks"])


# ------------------------------------------------------------------ PR #17 리뷰 반영 (KIM-YUNHO)
def _ctx(*log):
    from types import SimpleNamespace
    return SimpleNamespace(today="2026-12-13", tools=SimpleNamespace(log=list(log)))


def test_uncalculated_saving_and_shortfall_are_refused():
    agent = SwarmAgent(ScriptedLLM(lambda *a: Step()))
    ctx = _ctx(_ok("check_gap_coverage", {"remaining_gap": 13700}),
               _ok("list_transactions", {"rows": [{"날짜": "2026-12-12"}]}))
    b = Board()
    r = agent._c_propose_action(ctx, b, {"target": "배달", "action": "줄이기", "saving_monthly": 13700, "text": "x"})
    assert r["ok"] is False and b.proposals == []                    # 검산 기록의 남은 부족분을 그대로 쓰면 안 됨
    assert agent._c_check_gap_coverage(ctx, b, {"monthly_shortfall": 12})["ok"] is False     # 날짜 조각


def test_switch_and_goal_adjust_carry_strengths():
    agent = SwarmAgent(ScriptedLLM(lambda *a: Step()))
    ctx, b = _ctx(_ok("calculate", {"result": 9000})), Board()
    bad = agent._c_propose_action(ctx, b, {"target": "카페", "action": "바꾸기", "saving_monthly": 9000, "text": "x"})
    assert bad["ok"] is False                                        # 바꾸기는 to 필요
    assert agent._c_propose_action(ctx, b, {"target": "카페", "action": "바꾸기", "to": "집커피", "saving_monthly": 9000,
                                            "text": "집커피로"})["ok"]
    assert agent._c_propose_action(ctx, b, {"target": "여행", "action": "목표 조정", "deadline": "2027-06-30",
                                            "text": "두 달 미루기"})["ok"]
    b.review = {"ok": True}
    out = SwarmAgent._approved(b)
    assert out[0]["to"] == "집커피" and out[1]["deadline"] == "2027-06-30" and "saving_monthly" not in out[1]


def test_review_string_false_is_rejection():
    agent = SwarmAgent(ScriptedLLM(lambda *a: Step()))
    b = Board(proposals=[{"saving_monthly": 1}], gap={"covered": False})
    agent._c_record_review(_ctx(), b, {"ok": "false", "reason": "부족"})
    assert b.review["ok"] is False and agent._approved(b) == []


def test_suggestion_text_with_bad_number_is_fixed_by_counselor():
    seen = []

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role, outs = _role(system), tool_outputs(transcript)
        if role == "analyst":
            return _call("handoff_to_strategist", {"reason": "대안"})
        if role == "strategist":
            if not outs:
                return _call("calculate", {"expression": "242400 / 10"})
            if len(outs) == 1:
                return _call("propose_action", {"target": "주말 배달", "action": "줄이기", "per_week": 1,
                                                "saving_monthly": 24240, "text": "주 1회 줄이면 매달 77,777원"})
            return _call("handoff_to_reviewer", {"reason": "검증"})
        if role == "reviewer":
            if not outs:
                return _call("calculate", {"expression": "24240 * 1"})
            if len(outs) == 1:
                return _call("check_gap_coverage", {"monthly_shortfall": 24240})
            if len(outs) == 2:
                return _call("record_review", {"ok": True, "reason": "충분"})
            return _call("handoff_to_counselor", {"reason": "통과"})
        seen.append(transcript[0]["text"])
        if len(seen) == 2 and not outs:
            return _call("edit_suggestion_text", {"index": 0, "text": "주 1회만 줄여 볼까요?"})
        return _call("respond_to_user", {"message": "작게 시작해 봐요."})

    _, fb, meta = _onboarding(policy)
    assert meta["retries"] == 1 and "제안 0" in seen[1]
    assert fb["output"]["suggestions"][0]["text"] == "주 1회만 줄여 볼까요?" and fb["validation_final"]["ok"]


def test_forced_counselor_cannot_hand_off_and_message_is_not_empty():
    tools_seen = []

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role = _role(system)
        if role == "counselor":
            tools_seen.append({t["name"] for t in tools})
            return Step(text="...")                                   # 아무것도 안 함 → 멈춤
        return _call("handoff_to_strategist" if role == "analyst" else "handoff_to_analyst", {"reason": "핑퐁"})

    _, fb, meta = _onboarding(policy)
    assert not any(n.startswith("handoff_to_") for n in tools_seen[-1])
    assert fb["output"]["message"] == FALLBACK_MESSAGE
    assert meta["blocked_handoffs"]
    assert len(meta["trace"]) == meta["hops"] - len(meta["blocked_handoffs"])          # 막힌 이동은 바통 기록에 없음


def test_memory_keeps_newest_issues_and_drops_resolved():
    from types import SimpleNamespace
    agent = SwarmAgent(ScriptedLLM(lambda *a: Step()))
    issues = [{"type": "t", "target": f"x{i}", "status": "그대로"} for i in range(25)]
    issues.append({"type": "t", "target": "done", "status": "해결"})
    agent._pre_run = lambda ctx: {}
    agent._swarm = lambda ctx, board, meta: {"issues": issues, "goal": None, "suggestions": [], "message": ""}
    agent.feedback(SimpleNamespace(fixed_answers=None, tools=SimpleNamespace(log=[])))
    kept = [i["target"] for i in agent.memory["active_issues"]]
    assert kept == [f"x{i}" for i in range(18, 25)] and "done" not in kept       # 상한 7개, 최근순


def test_string_evidence_is_not_split():
    agent = SwarmAgent(ScriptedLLM(lambda *a: Step()))
    b = Board()
    agent._c_update_issue(_ctx(), b, {"type": "새 정기결제", "target": "웹툰패스", "status": "새로 생김", "evidence": "TABC123"})
    assert next(iter(b.issues.values()))["evidence"] == ["TABC123"]


def test_harness_tool_calls_counted_separately():
    _, fb, meta = _onboarding(c08_policy())
    assert meta["harness_tool_calls"] == 4
    assert meta["tool_calls"] == sum(1 for t in fb["tools"][4:] if t["name"] != "check_gap_coverage")


# ------------------------------------------------------------------ 비용 줄이기 (P1 실측 338만 토큰 뒤)
def test_calculate_many_logs_each_and_allows_proposal():
    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role, outs = _role(system), tool_outputs(transcript)
        if role == "analyst":
            return _call("handoff_to_strategist", {"reason": "대안"})
        if role == "strategist":
            if not outs:
                return _call("calculate_many", {"expressions": ["240000 - 200000", "13500 + 12800", "242400 / 10"]})
            if len(outs) == 1:
                vals = [r["result"] for r in outs[0]["results"]]
                return Step(calls=[ToolCall("p1", "propose_action", {"target": "구독", "action": "해지", "saving_monthly": vals[1],
                                                                   "text": "구독 두 개 해지"}),
                                   ToolCall("p2", "propose_action", {"target": "주말 배달", "action": "줄이기", "per_week": 1,
                                                                   "saving_monthly": vals[2], "text": "주 1회만"})])
            return _call("handoff_to_reviewer", {"reason": "검증"})
        if role == "reviewer":
            if not outs:
                return _call("calculate_many", {"expressions": ["240000 - 200000"]})
            if len(outs) == 1:
                return _call("check_gap_coverage", {"monthly_shortfall": 40000})
            if len(outs) == 2:
                return _call("record_review", {"ok": True, "reason": "충분"})
            return _call("handoff_to_counselor", {"reason": "통과"})
        return _call("respond_to_user", {"message": "두 가지를 골랐어요."})

    _, fb, meta = _onboarding(policy)
    assert [s["target"] for s in fb["output"]["suggestions"]] == ["구독", "주말 배달"]
    assert sum(t["name"] == "calculate" for t in fb["tools"]) == 4                 # 식마다 log (숫자 근거)
    assert meta["llm_calls"] <= 10


def test_repeated_lookups_are_cached_per_feedback():
    calls = []

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role, outs = _role(system), tool_outputs(transcript)
        if role in ("analyst", "strategist") and len(outs) < 2:
            calls.append(role)
            return _call("recurring_payments", {}, i=str(len(outs)))
        if role == "analyst":
            assert outs[0].get("value") and outs[1].get("cached") is True        # 처음엔 결과, 같은 역할의 두 번째는 안내만
            return _call("handoff_to_strategist", {"reason": "대안"})
        if role == "strategist":
            assert outs[0].get("value") and outs[1].get("cached") is True        # 다른 역할은 처음 한 번 결과를 받는다
            return _call("handoff_to_counselor", {"reason": "제안 없음"})
        return _call("respond_to_user", {"message": "확인했어요."})

    _, fb, _ = _onboarding(policy)
    assert sum(t["name"] == "recurring_payments" for t in fb["tools"]) == 1    # 전처리 1번만 실제로 실행


def test_transaction_lookups_are_narrow_and_slim():
    seen = {}

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role, outs = _role(system), tool_outputs(transcript)
        if role == "analyst" and not outs:
            return _call("list_transactions", {"limit": 100})
        if role == "analyst":
            seen.update(outs[0])
            return _call("handoff_to_counselor", {"reason": "끝"})
        return _call("respond_to_user", {"message": "확인했어요."})

    _, fb, _ = _onboarding(policy)
    rows = seen["value"]["rows"]
    assert len(rows) == 30 and set(rows[0]) == {"거래ID", "날짜", "내용", "금액", "소분류"}
    full = next(t for t in fb["tools"] if t["name"] == "list_transactions")["output"]["value"]["rows"][0]
    assert "결제수단" in full                                                   # log 에는 원본 그대로


def test_bounce_limit_delivers_checked_proposals_with_gap():
    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role, outs = _role(system), tool_outputs(transcript)
        if role == "analyst":
            return _call("handoff_to_strategist", {"reason": "대안"})
        if role == "strategist":
            if not outs:
                return _call("calculate_many", {"expressions": ["13500 + 12800", "240000 - 200000"]})
            if len(outs) == 1:
                return _call("propose_action", {"target": "구독", "action": "해지", "saving_monthly": 26300, "text": "구독 해지"})
            return _call("handoff_to_reviewer", {"reason": "검증"})
        if role == "reviewer":
            if not outs:
                return _call("calculate_many", {"expressions": ["240000 - 200000"]})
            if len(outs) == 1:
                return _call("check_gap_coverage", {"monthly_shortfall": 40000})
            if len(outs) == 2:
                return _call("record_review", {"ok": False, "reason": "부족"})
            return _call("handoff_to_strategist", {"reason": "반려"})
        return _call("respond_to_user", {"message": "구독부터 정리해 봐요. 남은 부족분은 다음에 같이 봐요."})

    _, fb, meta = _onboarding(policy)
    assert "반려" in meta["forced"]
    assert [s["target"] for s in fb["output"]["suggestions"]] == ["구독"]       # 핑퐁으로 끝나도 검산한 제안은 나간다


def test_counselor_sees_slim_memory_and_board():
    prompts = {}

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role = _role(system)
        prompts.setdefault(role, transcript[0]["text"])
        if role == "analyst":
            return _call("handoff_to_counselor", {"reason": "끝"})
        return _call("respond_to_user", {"message": "확인했어요."})

    _onboarding(policy)
    assert "규칙 전처리" in prompts["analyst"] and "규칙 전처리" not in prompts["counselor"]
    assert len(prompts["counselor"]) < len(prompts["analyst"])


# ------------------------------------------------------------------ 온보딩: 목표 대화 → 보고서 → 목표안 → 검산 (261127)
P1_GOAL = {"name": "일본 여행 (2주)", "amount": 2100000, "deadline": "2027-04-30", "current_saved": 800000}


def onboarding_goal_policy(seen=None):
    """analyst 확인 질문 → counselor 목표 대화 → strategist 보고서·목표안·제안 → reviewer 목표 검산 → counselor 전달."""
    seen = seen if seen is not None else {}

    def policy(system, transcript, tools):
        if not tools:
            return _understand_reply()
        role, outs, board = _role(system), tool_outputs(transcript), transcript[0]["text"]
        if "지금은" not in board and "[온보딩]" not in system:                     # 주간: 바로 마무리
            seen.setdefault("weekly", board)
            return _call("respond_to_user", {"message": "확인했어요."}) if role == "counselor" \
                else _call("handoff_to_counselor", {"reason": "주간"})
        if role == "analyst":
            if not outs:
                return _call("ask_user", {"target": "무비온", "question": "무비온은 요즘도 보세요?"})
            return _call("handoff_to_counselor", {"reason": "목표 대화 부탁"})
        if role == "counselor" and '"목표안"' not in board:
            if not outs:
                return _call("ask_user", {"target": "목표", "question": "어떤 목표로 언제까지 얼마를 모으고 싶으세요?"})
            return _call("handoff_to_strategist", {"reason": f"목표: {outs[0]['user_answer']}"})
        if role == "counselor":
            return _call("respond_to_user", {"message": "여행까지 매달 260,000원이 필요해요. 구독과 주말 배달을 같이 볼게요."})
        if role == "strategist":
            steps = [lambda: _call("calculate_many", {"expressions": ["(353500 + 145400 + 180800) // 3",
                                                                       "13500 + 12800", "242400 / 10"]}),
                     lambda: _call("set_report", {"monthly_leftover": outs[0]["results"][0]["result"],
                                                  "months": [{"month": "2026-09", "leftover": 353500}],
                                                  "patterns": ["주말 저녁 배달이 잦다"]}),
                     lambda: _call("propose_goal", P1_GOAL),
                     lambda: _call("propose_action", {"target": "무비온·소리랑", "action": "해지", "saving_monthly": 26300,
                                                      "text": "안 쓰는 구독 두 개를 정리해요"}),
                     lambda: _call("propose_action", {"target": "주말 배달", "action": "줄이기", "per_week": 1,
                                                      "saving_monthly": 24240, "text": "토요일 배달을 한 번 줄여요"}),
                     lambda: _call("handoff_to_reviewer", {"reason": "검산 부탁"})]
            return steps[len(outs)]()
        if role == "reviewer":
            if not outs:
                return _call("check_goal", {})
            if len(outs) == 1:
                seen["check"] = outs[0]
                return _call("record_review", {"ok": outs[0]["feasible"], "reason": "남는 돈 + 제안으로 닿는다"})
            return _call("handoff_to_counselor", {"reason": "통과"})
        raise AssertionError(role)
    return policy


def test_onboarding_sets_report_and_goal_then_user_creates_it():
    seen = {}
    rec = dataclasses.asdict(run("P1", SwarmAgent(ScriptedLLM(onboarding_goal_policy(seen))), D(2026, 12, 6)))
    fb = rec["feedbacks"][0]
    meta = next(c for c in rec["calls"] if c["mode"] == "feedback")["meta"]
    out = fb["output"]
    assert [(t["from_agent"], t["to_agent"]) for t in meta["trace"]] == [
        ("analyst", "counselor"), ("counselor", "strategist"), ("strategist", "reviewer"), ("reviewer", "counselor")]
    assert out["report"]["monthly_leftover"] == 226566 and out["report"]["required_cut"] == 260000 - 226566
    assert out["goal_proposal"] == P1_GOAL | {"monthly_required": 260000}           # 필요 월 저축액은 코드가 계산
    assert seen["check"]["remaining_gap"] == 0 and seen["check"]["feasible"]
    assert fb["validation_final"]["ok"] and [q["fact"] for q in fb["questions"]] == ["H2", "G1"]
    assert rec["goal"] == P1_GOAL and rec["goal_source"] == "agent" and rec["goal_edits"] == []
    assert not any(t["name"] == "check_goal" for t in fb["tools"][:meta["harness_tool_calls"]])
    assert meta["tool_calls"] == sum(1 for t in fb["tools"][meta["harness_tool_calls"]:] if t["name"] != "check_goal")
    assert '"목표": {"name": "일본 여행 (2주)"' in seen["weekly"]                        # 주간은 만든 목표를 기준으로
    s = score(rec)
    assert s["report"]["ok"] and s["goal"]["asked"] == ["goal"] and s["goal"]["monthly_required"] == 260000


def _onboarding_ctx(today=ONBOARD):
    from spending_agent.baseline.harness import FeedbackCtx, verify_feedback
    from spending_agent.baseline.tools import ToolBox, View
    from spending_agent.demo.generator import build_world, load_persona
    from spending_agent.demo.responses import with_goal
    p = with_goal(load_persona("P1"), None)
    w = build_world(p)
    box = ToolBox(View(p, w, list(w.batches["onboarding"]), today - dt.timedelta(days=1), today), max_questions=5)
    return FeedbackCtx(today.isoformat(), "onboarding", "2026-12-06", box, lambda o: verify_feedback(o, box.results()))


def test_onboarding_gates_need_report_numbers_and_goal_check():
    agent, board, ctx = SwarmAgent(dry_run_llm()), Board(), _onboarding_ctx()
    assert "set_report" in agent._c_propose_goal(ctx, board, P1_GOAL)["error"]              # 보고서 먼저
    assert "300000" in agent._c_set_report(ctx, board, {"monthly_leftover": 300000})["error"]   # 계산 안 한 숫자
    agent._c_calculate_many(ctx, board, {"expressions": ["(353500 + 145400 + 180800) // 3"]})
    assert agent._c_set_report(ctx, board, {"monthly_leftover": 226566})["ok"]
    assert "deadline" in agent._c_propose_goal(ctx, board, P1_GOAL | {"deadline": "4월 말"})["error"]
    assert agent._c_propose_goal(ctx, board, P1_GOAL)["required_cut"] == 33434
    assert "check_goal" in agent._c_record_review(ctx, board, {"ok": True, "reason": "좋다"})["error"]
    assert "리뷰" in agent._respond(board, {"message": "목표안이에요"})["error"]            # 리뷰 전 목표안은 못 보낸다
    check = agent._c_check_goal(ctx, board, {})
    assert check["remaining_gap"] == 33434 and not check["feasible"]                     # 제안이 없으면 못 닿는다
    assert agent._c_record_review(ctx, board, {"ok": True, "reason": "여지 없음, 남은 부족분 33,434원"})["ok"]
    out = agent._respond(board, {"message": "목표안이에요"})["out"]
    assert out["goal_proposal"]["monthly_required"] == 260000 and out["report"]["required_cut"] == 33434
    agent._c_calculate_many(ctx, board, {"expressions": ["226566 - 10000"]})
    agent._c_set_report(ctx, board, {"monthly_leftover": 216566})                           # 남는 돈이 바뀌면
    assert board.goal_proposal == {} and board.review == {}                                # 목표안·리뷰를 다시 한다
