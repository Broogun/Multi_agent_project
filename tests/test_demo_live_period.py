"""운영 기간 연장 (12/1 ~ 1/31, docs/10 §8).

원래 데이터(온보딩 + 12/1~12/14 거래·기록)는 그대로 두고 추가만 했는지, 피드백 일정, 기록 습관,
기간 중 쟁점, 시점별 정답(쟁점 상태·확인할 케이스·목표 달성도)이 맞게 만들어지는지 본다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest

from spending_agent.demo.generator import ISSUE_TYPES, STATUSES, build_world, load_persona, personas

PERSONAS = personas()
_CACHE: dict[str, object] = {}
ORIGINAL_LAST_DAY = "2026-12-14"
HABIT_RECORDS_FROM = "2026-12-15"

# 처음 만든 데이터(온보딩 + 12/14까지의 배치와 기록)의 해시. 연장하면서 바뀌면 안 된다.
# 일부러 원래 데이터를 바꿀 때만 새 값으로 고친다 (시연 시나리오·공유 DB도 같이 확인).
ORIGINAL = {
    "P1": ("f8b530ce0cb3b16b", 366, 4),
    "P2": ("8ab37dbb3b3d8da4", 284, 4),
    "P3": ("beefa24b414781bf", 266, 4),
    "P4": ("0f289cc7ffd70f8c", 263, 5),
    "P5": ("f31a392e32e3689d", 347, 4),
}
FEEDBACK = [("2026-12-01", "onboarding"), ("2026-12-06", "weekly"), ("2026-12-13", "weekly"),
            ("2026-12-20", "weekly"), ("2026-12-27", "weekly"), ("2026-12-31", "monthly"),
            ("2027-01-03", "weekly"), ("2027-01-10", "weekly"), ("2027-01-17", "weekly"),
            ("2027-01-24", "weekly"), ("2027-01-31", "monthly")]


def _world(pid: str):
    if pid not in _CACHE:
        _CACHE[pid] = build_world(load_persona(pid))
    return _CACHE[pid]


@pytest.fixture(scope="module", params=PERSONAS)
def world(request):
    return _world(request.param)


def _original_digest(w) -> tuple[str, int, int]:
    batches = {k: v for k, v in w.batches.items() if k == "onboarding" or k <= ORIGINAL_LAST_DAY}
    records = {k: v for k, v in w.records.items() if k <= ORIGINAL_LAST_DAY}
    blob = json.dumps({"batches": batches, "records": records}, ensure_ascii=False, sort_keys=True, default=str)
    return (hashlib.sha256(blob.encode()).hexdigest()[:16], sum(len(v) for v in batches.values()),
            sum(len(v) for v in records.values()))


def _point(w, day: str) -> dict:
    return next(t for t in w.truth["facts"]["timeline"] if t["date"] == day)


def _rows(w, desc: str, start: str = "", end: str = "9999") -> list[dict]:
    return [r for k, b in w.batches.items() if k != "onboarding" for r in b
            if r["내용"] == desc and start <= r["날짜"] <= end]


# ---------------------------------------------------------------- 공통
def test_original_data_is_unchanged(world):
    assert _original_digest(world) == ORIGINAL[world.persona["id"]]


def test_live_period_runs_to_jan_31(world):
    assert world.end == dt.date(2027, 2, 1)
    days = [k for k in world.batches if k != "onboarding"]
    assert days[0] == "2026-12-01" and days[-1] == "2027-01-31" and len(days) == 62


def test_feedback_schedule(world):
    """온보딩 → 일요일 저녁 주간 → 말일 저녁 월간. 1/31은 일요일이지만 월간 하나로 합친다."""
    assert [(pt["date"], pt["kind"]) for pt in world.feedback] == FEEDBACK
    assert world.truth["period"]["feedback"] == world.feedback


def test_habit_records_start_after_original_and_link_arrived_transactions(world):
    facts = world.truth["facts"]
    visible = {e["event_id"]: e["visible_on"] for e in facts["events"]}
    out = {r["record_id"]: r for day in world.records.values() for r in day}
    habit = [r for r in facts["records"] if r["source"] == "habit"]
    assert len(habit) >= 4
    for r in habit:
        assert r["written_at"][:10] >= HABIT_RECORDS_FROM
        assert visible[r["about_event"]] < r["written_at"][:10]          # 이미 들어온 거래를 골라서 쓴다
        assert out[r["record_id"]]["transaction_id"] == r["about_tx"]
    scripted = {r["about_event"] for r in facts["records"] if r["source"] == "scripted"}
    assert not scripted & {r["about_event"] for r in habit}             # 같은 거래를 두 번 기록하지 않는다


def test_habit_record_text_comes_from_templates(world):
    """기록 문장은 LLM이 아니라 페르소나 파일의 문장 틀에서 나온다."""
    rules = {r["id"]: r for r in world.persona["record_habits"]}
    by_id = {e.id: e for e in world.events}
    texts = {r["record_id"]: r["text"] for day in world.records.values() for r in day}
    for r in world.truth["facts"]["records"]:
        if r["source"] == "habit":
            e = by_id[r["about_event"]]
            options = {t.format(merchant=world.rows[e.id]["내용"], amount=f"{e.amount:,}")
                       for t in rules[r["rule"]]["texts"]}
            assert texts[r["record_id"]] in options


def test_every_record_has_a_feedback_point(world):
    dates = {pt["date"] for pt in world.feedback}
    for r in world.truth["facts"]["records"]:
        assert r["feedback_at"] in dates and r["feedback_at"] >= r["written_at"][:10]
        if r["source"] == "scripted" and r["written_at"][:10] > ORIGINAL_LAST_DAY:
            assert r["learn"], r["record_id"]                            # 기간 중 핵심 기록은 채점 기준이 있다


def test_mid_period_issues(world):
    """페르소나당 기간 중 쟁점 2~3개. 유형은 일반 목록, 근거는 detect_by 전에 들어온 거래, 케이스가 하나 이상."""
    issues, cases = world.truth["rubric"]["issues"], world.truth["rubric"]["cases"]
    hidden = {h["id"] for h in world.truth["user_sim"]["hidden_facts"]}
    assert 2 <= len(issues) <= 3
    for i in issues:
        assert i["type"] in ISSUE_TYPES
        assert i["from"].isoformat() > ORIGINAL_LAST_DAY                # 원래 2주 뒤에 생긴다
        assert set(i.get("facts", [])) <= hidden
        assert len(i["evidence_tx"]) == len(i.get("evidence", []))
        assert any(c.get("issue") == i["id"] for c in cases), i["id"]


def test_cases_have_check_points(world):
    dates = {pt["date"] for pt in world.feedback}
    written = {r["record_id"]: r["written_at"][:10] for r in world.truth["facts"]["records"]}
    for c in world.truth["rubric"]["cases"]:
        if c["phase"] == "onboarding":
            assert c["check_at"] == "2026-12-01"
        elif "기록이 없는 날" in c["signal"]:
            assert c["check_at"] is None                                  # 매일 본다
        else:
            assert c["check_at"] in dates, c["id"]
            if "record" in c:
                assert c["check_at"] >= written[c["record"]], c["id"]


def test_timeline_statuses(world):
    """기존 얽힌 문제는 내내 '필수', 기간 중 쟁점은 from 전 '아직' → detect_by 부터 '필수' → until 뒤 '종료'."""
    issues = {i["id"]: i for i in world.truth["rubric"]["issues"]}
    problems = {p["id"] for p in world.truth["rubric"]["problems"]}
    timeline = world.truth["facts"]["timeline"]
    assert [t["date"] for t in timeline] == [d for d, _ in FEEDBACK]
    for t in timeline:
        assert set(t["issues"]) == problems | set(issues)
        assert set(t["issues"].values()) <= set(STATUSES)
        assert all(t["issues"][pid] == "필수" for pid in problems)
        for iid, i in issues.items():
            day = dt.date.fromisoformat(t["date"])
            if day < i["from"]:
                assert t["issues"][iid] == "아직"
            elif day >= i["detect_by"] and ("until" not in i or day <= i["until"]):
                assert t["issues"][iid] == "필수"
    case_ids = [cid for t in timeline for cid in t["cases"]]
    assert len(case_ids) == len(set(case_ids))


def test_goal_progress_only_counts_finished_months(world):
    """진척은 끝난 달만 더한다 (월급 전후로 흔들리지 않게). 이번 달은 따로 보여준다."""
    goal = world.truth["facts"]["goal"]
    for t in world.truth["facts"]["timeline"]:
        g = t["goal_progress"]
        done = g["months_done"]
        assert g["progress"] == goal["current_saved"] + sum(m["net"] for m in done)
        assert g["required_by_now"] == goal["monthly_required"] * len(done)
        assert all(m["net"] == m["income"] - m["spend"] for m in done)
        assert (g["this_month"] is None) == (t["kind"] == "monthly")
    assert [len(t["goal_progress"]["months_done"]) for t in world.truth["facts"]["timeline"]][-1] == 2


# ---------------------------------------------------------------- 페르소나별 기간 중 스토리
def _spend(pid, ym, category):
    return _world(pid).truth["facts"]["monthly"][ym]["spend"].get(category, 0)


def test_p1_yearend_then_back_and_price_changes():
    w = _world("P1")
    assert _spend("P1", "2026-12", "외식") > 1.5 * _spend("P1", "2026-11", "외식")      # 연말 몰림
    assert _spend("P1", "2027-01", "외식") < _spend("P1", "2026-12", "외식")            # 1월엔 돌아감 (제안 없이)
    assert [-r["금액"] for r in _rows(w, "씨네박스")] == [12900, 12900]                  # 인상 가격 유지
    assert [-r["금액"] for r in _rows(w, "든든생명보험(주)")] == [48000, 52800]          # 갱신형 인상
    gift = [r for r in _rows(w, "출금", "2027-01-16", "2027-01-16")]
    assert [r["금액"] for r in gift] == [-100000] and gift[0]["대분류"] == "미분류"       # 이름 없는 축의금


def test_p2_vacation_game_cap_and_trip():
    w = _world("P2")
    assert _spend("P2", "2027-01", "카페") < 0.5 * _spend("P2", "2026-11", "카페")      # 방학: 카페 감소
    assert _spend("P2", "2027-01", "PC방") > 2 * _spend("P2", "2026-11", "PC방")        # 방학: PC방 증가
    assert _spend("P2", "2026-12", "게임") > 30000 > _spend("P2", "2027-01", "게임")     # 12월만 상한(H3) 초과
    g = _point(w, "2027-01-31")["goal_progress"]
    jan = g["months_done"][-1]
    assert jan["goal_spend"] == 280000 and jan["net"] == jan["income"] - jan["spend"]   # 여행 예약은 목표에 쓴 돈


def test_p3_yearend_pt_reenroll_and_phone():
    w = _world("P3")
    assert [-r["금액"] for r in _rows(w, "새솔모바일(주)")] == [89000, 59000]            # 약정 종료 (조언 효과 아님)
    pt = [i for i in w.truth["facts"]["installments"] if i["event_id"] == "S_pt_0106"]
    assert pt and pt[0]["months"] == 3
    jan_bill = next(b for b in w.truth["facts"]["card_bills"] if b["month"] == "2027-01")
    assert jan_bill["installments"] == 550000 // 3                                       # 10월 PT 마지막 회차만
    assert _spend("P3", "2026-12", "경조사") >= 200000


def test_p4_rent_shortfall_and_alba_recovery():
    w = _world("P4")
    m = w.truth["facts"]["monthly"]
    assert m["2027-01"]["income"]["알바"] > m["2026-11"]["income"]["알바"] > m["2026-12"]["income"]["알바"]
    assert m["2026-12"]["income"]["추가 지원"] == 200000
    support = _rows(w, "입금", "2026-12-23", "2026-12-23")
    assert [r["금액"] for r in support] == [200000] and support[0]["대분류"] == "미분류"   # 이름 없는 입금
    club = next(g for g in w.truth["facts"]["dutch_groups"] if g["payment"] == "S_club_1218")
    assert (club["people"], club["total"], club["unpaid"]) == (10, 220000, 3)
    assert m["2027-01"]["saving"] == 0                                                    # 1월에도 적금 못 넣음 (H12)
    assert w.truth["facts"]["balance"]["bank_lowest"] < 50000                             # 월세 뒤 바닥


def test_p5_relapse_parents_skip_and_card_bill():
    w = _world("P5")
    parents = [r for r in _rows(w, "출금") if r["금액"] == -200000]
    assert [r["날짜"] for r in parents] == ["2027-01-26"]                                # 12/26은 건너뜀
    bills = {b["month"]: b["amount"] for b in w.truth["facts"]["card_bills"]}
    assert bills["2027-01"] > bills["2026-12"]
    shop = [e for e in w.events if e.category == "쇼핑" and e.kind == "purchase"
            and dt.date(2026, 12, 25) <= e.at.date() <= dt.date(2027, 1, 3)]
    assert len(shop) >= 4                                                                 # 월급 직후 재발
