"""반응 규칙과 자기 기록 효과 (spending_agent.demo.responses, docs/10 §3·§4 초안). 지금은 P1만 규칙이 있다."""
from __future__ import annotations

import copy
import datetime as dt

import pytest

from spending_agent.demo import responses as R
from spending_agent.demo.generator import build_world, load_persona, personas

D = dt.date
SUBS = [{"response": "cancel_movieon", "day": D(2026, 12, 1)},   # 12/3, 1/3 결제 안 됨
        {"response": "cancel_sorirang", "day": D(2026, 12, 1)},  # 12/7, 1/7
        {"response": "cancel_webtoon", "day": D(2026, 12, 6)}]   # 12/5는 이미 결제, 1/5 안 됨
DELIVERY = [{"response": "delivery", "day": D(2026, 12, 13), "per_week": 1}]


@pytest.fixture(scope="module")
def p1() -> dict:
    return load_persona("P1")


# ------------------------------------------------------------------ 형식
@pytest.mark.parametrize("pid", personas())
def test_format(pid):
    R.check(load_persona(pid))


def _broken(p, key, rid, change):
    q = copy.deepcopy(p)
    change(next(r for r in q[key] if r["id"] == rid))
    return q


@pytest.mark.parametrize("key, rid, change, msg", [
    ("responses", "delivery", lambda r: r["target"].update(merchants=["없는가맹점"]), "모르는 가맹점"),
    ("responses", "delivery", lambda r: r["reject"].append({"action": "끊기"}), "이유"),
    ("responses", "delivery", lambda r: r.pop("effect"), "effect"),
    ("responses", "delivery", lambda r: r["accept"][0].update(max_per_week=0), "max_per_week"),
    ("responses", "delivery", lambda r: r.update(facts=["H99"]), "숨은 사실"),
    ("self_effects", "delivery_records", lambda s: s["effect"].update(habits=["없는습관"]), "모르는 습관"),
    ("self_effects", "delivery_records", lambda s: s["effect"].update(ratio=0), "ratio"),
    ("self_effects", "delivery_records", lambda s: s.pop("why"), "why"),
    ("self_effects", "goal_behind", lambda s: s.update(trigger={}), "trigger"),
])
def test_format_errors(p1, key, rid, change, msg):
    with pytest.raises(ValueError, match=msg):
        R.check(_broken(p1, key, rid, change))


def test_in_truth_for_simulator(p1):
    assert build_world(p1).truth["user_sim"]["responses"] == p1["responses"]


# ------------------------------------------------------------------ 반응
@pytest.mark.parametrize("target, action, strength, decision, rule", [
    ("무비온", "해지", {}, "수락", "cancel_movieon"),
    ("소리랑", "해지", {}, "수락", "cancel_sorirang"),
    ("웹툰패스", "해지", {}, "수락", "cancel_webtoon"),
    ("주말 배달", "줄이기", {"per_week": 1}, "수락", "delivery"),
    ("여행 적금", "저축 늘리기", {"amount": 40000}, "수락", "saving_up"),
    # 거절: 보호 지출, 숨은 사실과 충돌, 강도 초과
    ("씨네박스", "해지", {}, "거절", "keep_cinebox"),
    ("멜로디플러스", "해지", {}, "거절", "keep_melody"),
    ("한끼배달", "줄이기", {"per_week": 2}, "거절", "delivery"),
    ("배달", "끊기", {}, "거절", "delivery"),
    ("배달", "바꾸기", {}, "거절", "delivery"),
    ("적금", "저축 늘리기", {"amount": 80000}, "거절", "saving_up"),
    ("새솔모바일", "바꾸기", {}, "거절", "keep_family_phone"),
    ("든든생명", "해지", {}, "거절", "keep_insurance"),
    ("을지로호프", "끊기", {}, "거절", "yearend_pub"),
    ("1/16 축의금", "줄이기", {"per_week": 1}, "거절", "wedding_gift"),
    # 보류: 규칙에 없는 대상·행동, 뭉뚱그린 대상
    ("을지로호프", "줄이기", {"per_week": 1}, "보류", "yearend_pub"),
    ("구름드라이브", "해지", {}, "보류", None),
    ("구독", "해지", {}, "보류", None),
    ("무비온·씨네박스", "해지", {}, "보류", None),
])
def test_react(p1, target, action, strength, decision, rule):
    out = R.react(p1, {"target": target, "action": action, **strength})
    assert (out["decision"], out["response"]) == (decision, rule)
    if decision == "거절":
        assert out["why"]
        assert out["protected"] is (rule == "keep_cinebox")


# ------------------------------------------------------------------ 반영 (③의 제안 부분)
def test_apply_keeps_original_and_earlier_days(p1):
    before = copy.deepcopy(p1)
    q = R.apply(p1, DELIVERY + [{"response": "cancel_movieon", "day": D(2026, 12, 13)}])
    assert p1 == before
    base, served = build_world(p1), build_world(q)
    early = [(e.id, e.amount) for e in base.events if e.at.date() <= D(2026, 12, 13)]
    assert early == [(e.id, e.amount) for e in served.events if e.at.date() <= D(2026, 12, 13)]


def test_cancel_stops_later_charges(p1):
    w = build_world(R.apply(p1, [{"response": "cancel_movieon", "day": D(2026, 12, 13)}]))
    charges = sorted(e.at.date() for e in w.events if e.merchant == "ott_a" and e.at.date() >= D(2026, 12, 1))
    assert charges == [D(2026, 12, 3)]                 # 12/3은 제안 전, 1/3부터 안 나감


def test_reduce_is_subset_of_baseline(p1):
    """두 세계는 같은 날 같은 난수를 쓰므로, 줄이면 원래 배달 중 일부만 남는다."""
    base = {e.id for e in build_world(p1).events if e.category == "배달"}
    served = {e.id for e in build_world(R.apply(p1, DELIVERY)).events if e.category == "배달"}
    assert served < base
    assert all(i.split("-")[1] > "20261213" for i in base - served)


def test_per_week_to_frequency(p1):
    q = R.apply(p1, DELIVERY + [{"response": "delivery", "day": D(2027, 1, 3), "per_week": 0.5}])
    wk = R.weekly(p1, "delivery")
    assert round(wk, 2) == 1.87
    boosts = next(h for h in q["habits"] if h["id"] == "delivery")["boosts"]
    assert [(b["from"], b["until"], round(b["factor"], 3)) for b in boosts] == [
        (D(2026, 12, 14), D(2027, 1, 3), round(1 - 1 / wk, 3)),        # 주 1회 줄임
        (D(2027, 1, 4), D(2027, 1, 24), round(1 - 0.5 / wk, 3)),       # 다시 받은 날부터 새 강도로 3주
        (D(2027, 1, 25), R.FOREVER, round(1 - 0.5 / wk * 0.5, 3)),     # 그 뒤 줄인 폭의 절반만 남음
    ]


def test_saving_up_moves_money_only(p1):
    acc = [{"response": "saving_up", "day": D(2026, 12, 13), "amount": 40000}]
    w = build_world(R.apply(p1, acc))
    amounts = [e.amount for e in w.events if e.id.startswith("saving-") and e.at.date() >= D(2026, 12, 1)]
    assert amounts == [240000, 240000]
    assert R.compare(p1, acc)["feedback_effect"] == 0  # 저축 이체는 지출이 아니다 (내 돈의 이동)


@pytest.mark.parametrize("scale", R.SELF_SCALES.values())
def test_cancellations_save_exact_amounts(p1, scale):
    out = R.compare(p1, SUBS, scale)
    assert out["feedback_effect"] == 13500 * 2 + 7900 * 2 + 4900


# ------------------------------------------------------------------ 자기 기록 효과 (②)
def test_no_self_effect_at_zero(p1):
    out = R.compare(p1, [], R.SELF_SCALES["없음"])
    assert out["app"] == out["without"] and out["fired"]["app"] == []


def test_self_effect_follows_records_and_goal(p1):
    q, fired = R.with_self(p1, R.SELF_SCALES["중간"])
    assert {f["id"] for f in fired} == {"delivery_records", "goal_behind"}
    first = min(f["day"] for f in fired)
    assert first > D(2026, 12, 14)                     # 원래 2주에는 계기가 없다
    base, app = build_world(p1), build_world(q)
    assert [e.id for e in base.events if e.at.date() <= first] == [e.id for e in app.events if e.at.date() <= first]
    assert {e.id for e in app.events} < {e.id for e in base.events}
    assert R.compare(p1, [])["self_effect"] > 0


def test_goal_trigger_needs_behind_month(p1):
    """12월 순저축이 필요 월 저축액보다 적어서 12/31에 걸린다. 배달을 일찍 줄이면 12월이 따라잡아 걸리지 않는다."""
    _, fired = R.with_self(p1, 1.0)
    assert {"id": "goal_behind", "day": D(2026, 12, 31)} in fired
    _, fired = R.with_self(p1, 1.0, DELIVERY)
    assert all(f["id"] != "goal_behind" for f in fired)


@pytest.mark.parametrize("scale", R.SELF_SCALES.values())
def test_ranking_same_at_every_scale(p1, scale):
    """자기 기록 효과 크기(가정)를 바꿔도 제안의 순위는 같아야 한다."""
    effects = [R.compare(p1, acc, scale)["feedback_effect"] for acc in ([], SUBS, DELIVERY, SUBS + DELIVERY)]
    assert effects == sorted(effects) and effects[0] == 0 and len(set(effects)) == 4


def test_rejected_suggestions_change_nothing(p1):
    bad = [{"target": "씨네박스", "action": "해지"}, {"target": "배달", "action": "끊기"},
           {"target": "새솔모바일", "action": "바꾸기"}, {"target": "배달", "action": "줄이기", "per_week": 3}]
    assert all(R.react(p1, s)["decision"] != "수락" for s in bad)


# ------------------------------------------------------------------ P1~P5 공통: 케이스가 기대하는 제안 묶음
GOOD = {
    "P1": [(D(2026, 12, 1), {"target": "무비온", "action": "해지"}),
           (D(2026, 12, 1), {"target": "소리랑", "action": "해지"}),
           (D(2026, 12, 13), {"target": "주말 배달", "action": "줄이기", "per_week": 1})],
    "P2": [(D(2026, 12, 1), {"target": "카페", "action": "바꾸기", "to": "학교 앞 저가 테이크아웃"}),
           (D(2026, 12, 6), {"target": "편의점", "action": "줄이기", "per_week": 1}),
           (D(2026, 12, 13), {"target": "게임", "action": "상한 정하기", "amount": 30000}),
           (D(2026, 12, 20), {"target": "카페", "action": "줄이기", "per_week": 1})],
    "P3": [(D(2026, 12, 1), {"target": "야근 택시", "action": "경비 청구"}),
           (D(2026, 12, 6), {"target": "배달", "action": "줄이기", "per_week": 0.5}),
           (D(2026, 12, 13), {"target": "휴대폰 요금", "action": "바꾸기", "to": "데이터 적은 요금제"})],
    "P4": [(D(2026, 12, 6), {"target": "동아리 회식", "action": "바꾸기", "to": "회식 전에 미리 걷기"}),
           (D(2026, 12, 6), {"target": "배달", "action": "줄이기", "per_week": 0.5}),
           (D(2026, 12, 6), {"target": "편의점", "action": "줄이기", "per_week": 1})],
    "P5": [(D(2026, 12, 1), {"target": "쇼핑", "action": "바꾸기", "to": "장바구니에 두고 이틀 뒤 결정"}),
           (D(2026, 12, 6), {"target": "배달", "action": "줄이기", "per_week": 1}),
           (D(2026, 12, 13), {"target": "무비온", "action": "해지"})],
}
BAD = {   # 보호 지출·숨은 사실·기간 제약에 걸리는 제안
    "P1": [(D(2026, 12, 6), {"target": "씨네박스", "action": "해지"}, True),
           (D(2026, 12, 6), {"target": "배달", "action": "끊기"}, False)],
    "P2": [(D(2026, 12, 13), {"target": "카페", "action": "줄이기", "per_week": 1}, True),       # 기말고사 기간
           (D(2026, 12, 6), {"target": "카페", "action": "끊기"}, True),
           (D(2026, 12, 6), {"target": "집에서 커피", "action": "바꾸기", "to": "집에서 드립"}, False),
           (D(2027, 1, 10), {"target": "하늘길투어", "action": "줄이기", "per_week": 1}, False)],
    "P3": [(D(2026, 12, 6), {"target": "택시", "action": "줄이기", "per_week": 1}, False),
           (D(2026, 12, 6), {"target": "PT", "action": "끊기"}, True),
           (D(2026, 12, 13), {"target": "부모님 생활비", "action": "줄이기", "per_week": 1}, False),
           (D(2026, 12, 27), {"target": "경조사", "action": "줄이기", "per_week": 1}, False)],
    "P4": [(D(2026, 12, 6), {"target": "동아리 회식", "action": "줄이기", "per_week": 1}, True),
           (D(2026, 12, 20), {"target": "못 받은 정산", "action": "정산 받기"}, False),
           (D(2026, 12, 27), {"target": "부모님 용돈", "action": "수입 늘리기"}, False),
           (D(2026, 12, 13), {"target": "노트북 적금", "action": "저축 늘리기", "amount": 50000}, False),  # 잔액 바닥
           (D(2026, 12, 6), {"target": "생필품", "action": "바꾸기", "to": "대용량 묶음"}, False)],
    "P5": [(D(2026, 12, 6), {"target": "쇼핑", "action": "끊기"}, False),
           (D(2026, 12, 6), {"target": "포차 샤로수길", "action": "줄이기", "per_week": 1}, True),
           (D(2026, 12, 31), {"target": "부모님 용돈", "action": "줄이기", "per_week": 1}, False),
           (D(2026, 12, 13), {"target": "노트북 할부", "action": "해지"}, False),
           (D(2026, 12, 1), {"target": "비상금 목표", "action": "목표 조정", "deadline": "2027-12-31", "amount": 2000000},
            False)],
}


def _accepted(p, items):
    out = []
    for day, s, *_ in items:
        r = R.react(p, s, day)
        assert r["decision"] == "수락", (s, r)
        out.append({"response": r["response"], "day": day, **{k: r[k] for k in R.STRENGTHS if k in r}})
    return out


@pytest.mark.parametrize("pid", GOOD)
def test_bad_suggestions_rejected(pid):
    p = load_persona(pid)
    for day, s, protected in BAD[pid]:
        r = R.react(p, s, day)
        assert r["decision"] == "거절" and r["why"], (s, r)
        assert r["protected"] is protected, s


@pytest.mark.parametrize("pid", GOOD)
def test_protected_answer_has_rule(pid):
    """고정 답변의 지키고 싶은 소비마다 보호 거절이 있다."""
    p = load_persona(pid)
    protected = [r for r in p["responses"] if r.get("protected") or any(a.get("protected") for a in r.get("reject", []))]
    assert len(protected) >= len(p["onboarding"]["fixed_answers"]["protected"])


@pytest.fixture(scope="module")
def ranking():
    """페르소나별로 (제안 없음, 첫 제안만, 좋은 묶음 전체)의 기댓값 피드백 효과를 크기 배율마다."""
    out = {}
    for pid, items in GOOD.items():
        p = load_persona(pid)
        sets = ([], _accepted(p, items[:1]), _accepted(p, items))
        out[pid] = {s: [R.compare(p, acc, scale)["expected_feedback_effect"] for acc in sets]
                    for s, scale in R.SELF_SCALES.items()}
    return out


@pytest.mark.parametrize("pid", GOOD)
def test_good_suggestions_rank_same_at_every_scale(ranking, pid):
    for scale, (none, first, good) in ranking[pid].items():
        assert none == 0 and 0 < first < good, (scale, none, first, good)


# ------------------------------------------------------------------ P2~P5 효과별
def test_p2_cap_prevents_over_cap_issue():
    p = load_persona("P2")
    acc = _accepted(p, [(D(2026, 12, 13), {"target": "게임", "action": "상한 정하기", "amount": 30000})])
    w = build_world(R.apply(p, acc))
    dec = sum(e.amount for e in w.events if e.category == "게임" and e.at.strftime("%Y-%m") == "2026-12")
    assert dec <= 30000
    issue = next(i for i in w.truth["rubric"]["issues"] if i["id"] == "game_over_cap")
    assert issue["prevented"]                          # 상한을 지켜서 12월 말 초과 결제가 안 생겼다
    assert R.react(p, {"target": "게임", "action": "상한 정하기", "amount": 10000})["decision"] == "거절"


def test_p2_price_change_only_after_start():
    p = load_persona("P2")
    acc = _accepted(p, GOOD["P2"][:1])
    base = {e.id: e.amount for e in build_world(p).events if e.id.startswith("cafe-")}
    served = {e.id: e.amount for e in build_world(R.apply(p, acc)).events if e.id.startswith("cafe-")}
    assert base.keys() == served.keys()                # 가격만 바뀌고 방문은 그대로
    assert all(served[i] == base[i] for i in base if i.split("-")[1] <= "20261201")
    assert all(served[i] < base[i] for i in base if i.split("-")[1] > "20261201")


def test_p3_reimbursement_from_late_taxi():
    p = load_persona("P3")
    acc = _accepted(p, GOOD["P3"][:1])
    w = build_world(R.apply(p, acc))
    late = sum(e.amount for e in w.events if e.id.startswith("taxi-") and e.at.hour >= 23
               and D(2026, 12, 2) <= e.at.date() <= D(2026, 12, 31))
    refund = [e for e in w.events if e.id == "reimburse-taxi-202612"]
    assert late > 0 and [(e.at.date(), e.amount, e.kind) for e in refund] == [(D(2027, 1, 10), late, "transfer_in")]
    assert w.rows["reimburse-taxi-202612"]["내용"] == "경비정산"


def test_p3_phone_plan_after_contract():
    p = load_persona("P3")
    acc = _accepted(p, GOOD["P3"][2:])
    bills = sorted((e.at.date(), e.amount) for e in build_world(R.apply(p, acc)).events
                   if e.id.startswith("phone-") and e.at.date() >= D(2026, 12, 1))
    assert bills == [(D(2026, 12, 20), 89000), (D(2027, 1, 20), 39000)]   # 약정 끝난 뒤부터


def test_p4_prepaid_club_has_no_unpaid():
    p = load_persona("P4")
    acc = _accepted(p, GOOD["P4"][:1])
    base, served = build_world(p), build_world(R.apply(p, acc))
    repaid = [len([e for e in w.events if e.group == "S_club_1218" and e.kind == "transfer_in"]) for w in (base, served)]
    assert repaid == [6, 9]                            # 10명 중 대표 1명 빼고 9명 모두 보냄


def test_p4_goal_flex_and_saving_timing():
    p = load_persona("P4")
    goal = {"target": "노트북 목표", "action": "목표 조정", "deadline": "2027-03-15", "amount": 1100000}
    acc = _accepted(p, [(D(2026, 12, 13), goal)])
    c = R.compare(p, acc, 0.0)
    assert c["served_required_by_now"] < c["required_by_now"] and c["feedback_effect"] == 0
    assert R.react(p, dict(goal, deadline="2027-04-30"))["decision"] == "거절"
    saving = {"target": "노트북 적금", "action": "저축 늘리기", "amount": 50000}
    assert R.react(p, saving, D(2026, 12, 13))["decision"] == "거절"   # 잔액 바닥일 때
    assert R.react(p, saving, D(2027, 1, 10))["decision"] == "수락"    # 알바비 회복 뒤


def test_p5_pay_first_and_goal():
    p = load_persona("P5")
    acc = _accepted(p, [(D(2026, 12, 13), {"target": "월급날 자동이체", "action": "저축 늘리기", "amount": 50000}),
                        (D(2026, 12, 1), {"target": "비상금 목표", "action": "목표 조정", "deadline": "2027-12-31"})])
    w = build_world(R.apply(p, acc))                   # 잔액이 음수가 되면 여기서 실패한다
    assert [(e.at.date(), e.amount) for e in w.events if e.id.startswith("emergency_saving-")] == [
        (D(2026, 12, 25), 50000), (D(2027, 1, 25), 50000)]
    c = R.compare(p, acc, 0.0)
    assert c["served_required_by_now"] < c["required_by_now"]
    assert c["expected_feedback_effect"] > 0           # 떼어 두면 월급 직후 쇼핑이 조금 준다 (가정)


def test_expected_effect_steadier_than_actual():
    """P5 쇼핑은 결제 한 건이 커서 실제 자기 기록 효과가 크게 흔들린다. 기댓값은 배율에 비례한다."""
    p = load_persona("P5")
    small, mid = (R.compare(p, [], R.SELF_SCALES[s]) for s in ("작게", "중간"))
    assert abs(mid["expected_self_effect"] - 2 * small["expected_self_effect"]) <= 0.1 * mid["expected_self_effect"]
