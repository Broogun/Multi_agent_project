"""채점 (docs/10 §7, 초안). 정답(truth.json)은 여기서만 쓴다. 에이전트 구조와 상관없이 같은 기준이다.

규칙 채점 (LLM 없음, CI 에서 돈다)
- 쟁점: 피드백 시점마다 기준 세계에서 '필수'인 쟁점을 몇 개 짚었나. 에이전트 쟁점과 정답 쟁점은
  ① 근거 거래ID 가 겹치거나 ② 유형이 같고 대상 낱말이 맞거나 ③ 대상 낱말(절약 후보 분류·제목 낱말)이 맞으면 같은 것으로 본다.
  어느 방법으로 맞췄는지 남기므로 사람이 확인할 수 있다. '종료'된 쟁점을 앞서 짚었다면 '해결'로 갱신했는지도 본다.
- 숫자: 메시지·제안 문장의 숫자가 도구 결과에 있는지 (첫 답, 최종 답)
- 질문: 질문 수, 숨은 사실을 맞힌 질문 수 (시뮬레이터가 답을 정했는가)
- 제안: 수락·거절·보류 수, 보호 지출을 건드린 제안 수 (절약액이 커도 실패로 따로 센다)
- 절약: 세 세계의 끝난 달 (수입 − 지출) 차이 (responses.compare). LLM 점수는 피드백 효과(③ 앱+피드백 − ② 앱만)
- 비용: LLM 호출 수, 토큰, 시간

LLM 심판 (선택): 케이스마다 check_at 피드백의 출력·질문·도구 결과를 보고 expect / must_not 를 판정한다.
1차 기준선에서 심판이 도구 결과를 못 봐서 오판한 것(C06)을 막으려고 도구 결과를 같이 넘긴다.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from ..demo import responses
from ..demo.generator import build_world, load_persona
from .llm import LLM
from .single import parse_json

GENERIC = {"가격", "인상", "증가", "감소", "변경", "중복", "전환", "부족", "필요", "저축액", "지출", "결제", "정기결제", "대비",
           "일시적", "변화", "확인", "가장", "중", "후보", "절약", "크기", "월", "기간", "이번", "목표", "줄이기", "하순"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]+", text or "") if w not in GENERIC}


def _truth_items(truth: dict) -> dict[str, dict]:
    events = truth["facts"]["events"]
    out = {}
    for it in truth["rubric"]["problems"] + truth["rubric"]["issues"]:
        lever = it.get("lever") or {}
        cats = lever.get("categories") or ([lever["category"]] if lever.get("category") else [])
        ev = set(it.get("evidence_tx", []))
        ev |= {e["tx_id"] for e in events if e["true_category"] in cats}
        if lever.get("kind") == "unpaid_club":
            ev |= {e["tx_id"] for e in events if e.get("group")}
        out[it["id"]] = {**it, "_evidence": ev, "_words": _words(it.get("title", "")) | set(cats)}
    return out


def match_issues(agent_issues: list[dict], items: dict[str, dict]) -> dict[str, dict]:
    """정답 쟁점 id → {how, status, agent}. 에이전트 쟁점 하나는 정답 쟁점 하나에만 맞춘다 (가장 강한 방법 우선)."""
    found: dict[str, dict] = {}
    goal_items = [i for i, it in items.items() if it.get("side") == "목표"]
    for ai in agent_issues:
        if not isinstance(ai, dict):
            continue
        ev = set(ai.get("evidence") or [])
        text = f'{ai.get("target", "")} {ai.get("summary", "")}'
        words = _words(text)
        best = None
        for iid, it in items.items():
            if iid in found:
                continue
            if ev & it["_evidence"]:
                how = (3, "근거 거래")
            elif ai.get("type") and ai.get("type") == it.get("type") and words & it["_words"]:
                how = (2, "유형+대상")
            elif ai.get("type") == "목표 진척" and it.get("side") == "목표" and (len(goal_items) == 1 or words & it["_words"]):
                how = (2, "목표 진척")
            elif any(w in text for w in it["_words"]):
                how = (1, "대상 낱말")
            else:
                continue
            if best is None or how[0] > best[0][0]:
                best = (how, iid)
        if best:
            found[best[1]] = {"how": best[0][1], "status": ai.get("status"), "agent": ai}
    return found


def _sum(calls, key):
    return sum(c.get("meta", {}).get(key, 0) or 0 for c in calls)


def score(run: dict, judge: LLM | None = None) -> dict:
    p = load_persona(run["persona"])
    truth = build_world(p).truth
    items = _truth_items(truth)
    timeline = {t["date"]: t for t in truth["facts"]["timeline"]}

    points, flagged, resolved = [], set(), {}
    req_total = req_hit = unmatched = 0
    for fb in run["feedbacks"]:
        st = timeline[fb["date"]]["issues"]
        m = match_issues(fb["output"].get("issues", []), items)
        required = [i for i, s in st.items() if s == "필수"]
        hit = [i for i in required if i in m]
        # 끝난 쟁점: 끝나기 전에 짚었던 것이 끝난 뒤 피드백 중 한 번이라도 '해결'로 갱신됐는지 본다
        for i in (i for i, s in st.items() if s == "종료" and i in flagged):
            resolved[i] = resolved.get(i, False) or (i in m and m[i]["status"] == "해결")
        flagged |= {i for i in m if st.get(i) != "종료"}
        unmatched += len(fb["output"].get("issues", [])) - len(m)
        req_total, req_hit = req_total + len(required), req_hit + len(hit)
        points.append({"date": fb["date"], "kind": fb["kind"], "required": required, "hit": hit,
                       "missed": [i for i in required if i not in m], "optional_hit": [i for i in m if st.get(i) == "선택"],
                       "closed_not_updated": [i for i, ok in resolved.items() if not ok],
                       "how": {i: x["how"] for i, x in m.items()}})
    closed_total, closed_ok = len(resolved), sum(resolved.values())

    fbs = run["feedbacks"]
    qs = [q for fb in fbs for q in fb["questions"]]
    reacts = [r for fb in fbs for r in fb["reactions"]]
    decisions = {d: sum(r["decision"] == d for r in reacts) for d in ("수락", "거절", "보류")}
    upto = dt.date.fromisoformat(run["until"])
    saving = responses.compare(p, [dict(a, day=dt.date.fromisoformat(a["day"])) for a in run["accepted"]],
                               run["self_scale"], upto) if p.get("responses") else None
    out = {
        "issues": {"required": req_total, "hit": req_hit, "recall": round(req_hit / req_total, 3) if req_total else None,
                   "closed_to_update": closed_total, "closed_updated": closed_ok, "unmatched_agent_issues": unmatched,
                   "by_point": points},
        "numbers": {"feedbacks": len(fbs), "first_ok": sum(fb["validation_first"].get("ok", False) for fb in fbs),
                    "final_ok": sum(fb["validation_final"].get("ok", False) for fb in fbs),
                    "bad": {fb["date"]: fb["validation_final"].get("bad") for fb in fbs
                            if not fb["validation_final"].get("ok")}},
        "questions": {"asked": len(qs), "hit_fact": sum(q["fact"] is not None for q in qs),
                      "facts": sorted({q["fact"] for q in qs if q["fact"]}),
                      "facts_total": len(truth["user_sim"]["hidden_facts"])},
        "suggestions": {"total": len(reacts), **decisions,
                        "protected_violations": sum(r["decision"] == "거절" and r.get("protected", False) for r in reacts),
                        "rejected_ratio": round(decisions["거절"] / len(reacts), 3) if reacts else None},
        "saving": saving if saving is None else {k: saving[k] for k in (
            "through", "scale", "feedback_effect", "self_effect", "total", "required_by_now", "calc")},
        "cost": {"llm_calls": _sum(run["calls"], "llm_calls"), "tool_calls": _sum(run["calls"], "tool_calls"),
                 "input_tokens": _sum(run["calls"], "input_tokens"), "output_tokens": _sum(run["calls"], "output_tokens"),
                 "seconds": round(sum(c.get("seconds", 0) for c in run["calls"]), 1),
                 "max_input_per_call": max((c.get("meta", {}).get("input_tokens", 0) for c in run["calls"]), default=0)},
    }
    if judge:
        out["cases"] = judge_cases(run, truth, judge)
    return out


JUDGE = ("다음 평가 케이스로 AI 코치의 피드백을 채점하라. JSON만: "
         '{"expect":[true/false...], "must_not_violated":[true/false...], "verdict":"PASS|PARTIAL|FAIL", "reason":"한두 문장"}\n'
         "PASS=expect 모두 충족 & must_not 위반 없음, PARTIAL=일부 충족 & 위반 없음, FAIL=위반 있음 또는 대부분 미충족.\n"
         "AI 가 말한 숫자는 아래 도구 결과에 있으면 근거가 있는 것이다.")


def judge_cases(run: dict, truth: dict, llm: LLM) -> list[dict]:
    by_date = {fb["date"]: fb for fb in run["feedbacks"]}
    facts = {k: truth["facts"][k] for k in ("goal", "levers", "card_bills", "dutch_groups") if k in truth["facts"]}
    out = []
    for c in truth["rubric"]["cases"]:
        if c["check_at"] is None:
            out.append({"id": c["id"], "verdict": "PASS", "reason": "피드백 날이 아니면 하네스가 유저에게 말하지 않는다 (구조로 보장)"})
            continue
        fb = by_date.get(c["check_at"])
        if fb is None:
            out.append({"id": c["id"], "verdict": "SKIP", "reason": f"{c['check_at']} 피드백이 이번 실행 기간 밖"})
            continue
        tools = [{"name": t["name"], "args": t["args"], "output": t["output"]} for t in fb["tools"]]
        seen = {"output": fb["output"], "questions": fb["questions"], "tool_results": tools}
        prompt = (f"{JUDGE}\n케이스: {json.dumps(c, ensure_ascii=False, default=str)}\n"
                  f"정답 사실: {json.dumps(facts, ensure_ascii=False, default=str)[:6000]}\n"
                  f"AI 피드백(질문·도구 결과 포함): {json.dumps(seen, ensure_ascii=False, default=str)[:24000]}")
        st = llm.step("너는 소비 관리 AI 코치의 응답을 채점하는 평가자다.", [{"role": "user", "text": prompt}], [])
        j = parse_json(st.text) or {"verdict": "ERROR", "reason": st.text[:200]}
        out.append({"id": c["id"], "check_at": c["check_at"], **j,
                    "usage": st.usage})
    return out
