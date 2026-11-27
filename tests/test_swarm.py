"""압축 메모리 기반 Swarm (agent.swarm). 가짜 LLM 만 쓴다 (네트워크 없음)."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json

from spending_agent.agent.memory import MAX_BOUNCE, MAX_HOPS, Board
from spending_agent.agent.swarm import SwarmAgent, dry_run_llm
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
    assert "웹툰패스" in first_weekly and "가격 변화" in first_weekly            # C05 · C04 신호가 작업판에 먼저 올라감
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
            assert outs[0]["ok"] is False and "도구 결과" in outs[0]["error"]
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
def test_decimal_fragments_are_not_allowed_numbers():
    from spending_agent.agent.swarm import _numbers
    assert 75 not in _numbers({"result": 0.75}) and 0 not in _numbers({"result": 0.75})
    assert {1234567, 240000, 3} <= _numbers({"calc": "1,234,567 − 240,000.0 = 3"})


def test_changing_proposals_clears_stale_gap():
    from types import SimpleNamespace
    agent = SwarmAgent(ScriptedLLM(lambda *a: Step()))
    ctx = SimpleNamespace(tools=SimpleNamespace(results=lambda: [{"value": {"result": 10000}}]))
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
