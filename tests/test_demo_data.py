"""데모 거래 생성기 (통합 거래내역).

공통: 재현성, 배치 경계, 행 모양, 잔액, 정산(1/n), 환불, 카드대금·할부, 유저 기록 연결, 정답 구조.
페르소나별: 얽힌 문제와 함정이 실제 데이터에 들어 있는지.
"""
from __future__ import annotations

import csv
import json
from collections import Counter

import pytest

from spending_agent.demo.__main__ import advance, build
from spending_agent.demo.generator import EXPORT_COLUMNS, build_world, load_persona, personas

PERSONAS = personas()
_CACHE: dict[str, object] = {}


def _world(pid: str):
    if pid not in _CACHE:
        _CACHE[pid] = build_world(load_persona(pid))
    return _CACHE[pid]


@pytest.fixture(scope="module", params=PERSONAS)
def world(request):
    return _world(request.param)


def _all_rows(world):
    return [r for batch in world.batches.values() for r in batch]


def _events_by_tx(world):
    return {e["tx_id"]: e for e in world.truth["facts"]["events"]}


# ---------------------------------------------------------------- 공통
def test_all_five_personas_exist():
    assert PERSONAS == ["P1", "P2", "P3", "P4", "P5"]


def test_same_persona_same_output(world):
    again = build_world(load_persona(world.persona["id"]))
    assert again.batches == world.batches
    assert again.records == world.records
    assert again.truth == world.truth


def test_onboarding_is_three_months_before_d0(world):
    dates = [r["날짜"] for r in world.batches["onboarding"]]
    assert {d[:7] for d in dates} == {"2026-09", "2026-10", "2026-11"}
    assert min(dates) >= world.start.isoformat() and max(dates) < world.d0.isoformat()


def test_one_row_per_event_and_each_row_delivered_once(world):
    ids = Counter(r["거래ID"] for r in _all_rows(world))
    assert max(ids.values()) == 1
    assert len(ids) == len([e for e in world.events if e.visible_on < world.end])


def test_delayed_rows_arrive_on_a_later_day(world):
    visible = {e["tx_id"]: e["visible_on"] for e in world.truth["facts"]["events"]}
    delayed = 0
    for day, rows in world.batches.items():
        if day == "onboarding":
            continue
        for r in rows:
            assert visible[r["거래ID"]] == day and r["날짜"] <= day
            delayed += r["날짜"] < day
    assert delayed > 0


def test_row_shape_and_signs(world):
    for r in _all_rows(world):
        assert tuple(r) == EXPORT_COLUMNS
        if r["타입"] == "수입":
            assert r["금액"] > 0
        elif r["내용"].endswith("(취소)"):
            assert r["타입"] == "지출" and r["금액"] > 0
        else:
            assert r["금액"] < 0


def test_transfers_have_no_counterparty(world):
    events = _events_by_tx(world)
    for r in _all_rows(world):
        e = events[r["거래ID"]]
        if e["kind"] in ("transfer_in", "transfer_out") and e["channel"] == "account" and e["true_category"] != "급여":
            assert r["내용"] in ("입금", "출금") and r["대분류"] == "미분류"


def test_balances_never_negative(world):
    assert world.truth["facts"]["balance"]["bank_lowest"] >= 0
    assert world.truth["facts"]["balance"]["money_end"] >= 0


def test_shared_payments_add_up(world):
    for g in world.truth["facts"]["dutch_groups"]:
        assert g["paid_count"] + g["unpaid"] == g["people"] - 1
        assert g["received"] == g["share"] * g["paid_count"]
        assert g["net_cost"] == g["total"] - g["received"]


def test_cancel_row_offsets_original(world):
    by_tx = {r["거래ID"]: r for r in _all_rows(world)}
    for c in world.truth["facts"]["cancels"]:
        if c["cancel_tx"] in by_tx:
            assert by_tx[c["original_tx"]]["금액"] + by_tx[c["cancel_tx"]]["금액"] == 0


def test_card_bills_match_breakdown(world):
    by_tx = {r["거래ID"]: r for r in _all_rows(world)}
    credit = world.persona["accounts"]["card"]["kind"] == "신용"
    assert bool(world.truth["facts"]["card_bills"]) == credit
    for b in world.truth["facts"]["card_bills"]:
        parts = sum(v for k, v in b.items() if k in ("first_bill", "lump_sum", "installments", "cancels"))
        assert parts == b["amount"]
        row = by_tx.get(b["bill_tx"])
        if row:
            assert row["타입"] == "이체" and row["대분류"] == "카드대금" and row["금액"] == -b["amount"]


def test_installment_rows_are_marked(world):
    by_tx = {r["거래ID"]: r for r in _all_rows(world)}
    for i in world.truth["facts"]["installments"]:
        assert f"할부 {i['months']}개월" in by_tx[i["tx"]]["결제수단"]


def test_record_links_only_already_arrived_transactions(world):
    by_id = {e.id: e for e in world.events}
    for r in world.persona.get("records", []):
        out = next(x for x in world.records[r["written_at"].date().isoformat()] if x["record_id"] == r["id"])
        target = by_id.get(r.get("about", ""))
        assert "about" not in r or target is not None, r["id"]
        arrived = target is not None and target.visible_on < r["written_at"].date()
        assert (out["transaction_id"] is not None) == arrived, r["id"]


def test_persona_references_resolve(world):
    p = world.persona
    used = {e.merchant for e in world.events if e.merchant}
    assert used <= set(p["merchants"])
    assert all(len(m["app_category"]) == 2 for m in p["merchants"].values())
    case_ids = [c["id"] for c in p.get("cases", [])]
    assert len(case_ids) == len(set(case_ids)) and all(c.startswith(p["id"] + "-") for c in case_ids)


def test_truth_is_split_into_facts_user_sim_rubric(world):
    assert {"facts", "user_sim", "rubric"} <= set(world.truth)
    ids = {h["id"] for h in world.truth["user_sim"]["hidden_facts"]}
    for prob in world.truth["rubric"]["problems"]:
        assert set(prob.get("facts", [])) <= ids, prob["id"]
    assert world.truth["facts"]["goal"]["monthly_required"] > 0


def test_every_persona_is_tangled(world):
    """한 명에 문제 하나가 아니다: 문제 3개 이상, 방향 2개 이상, 복합 케이스 1개 이상, 기록 없는 날 케이스."""
    problems, cases = world.persona["problems"], world.persona["cases"]
    assert len(problems) >= 3 and len({p["side"] for p in problems}) >= 2
    assert any(c.get("composite") for c in cases)
    assert any("기록이 없는 날" in c["signal"] for c in cases)


# ---------------------------------------------------------------- 페르소나별 스토리
def _monthly(pid):
    return _world(pid).truth["facts"]["monthly"]


def _rows_of(pid, merchant_name, batch=None):
    w = _world(pid)
    rows = w.batches[batch] if batch else _all_rows(w)
    return [r for r in rows if r["내용"] == merchant_name]


def test_p1_subscriptions_and_family_phone():
    w = _world("P1")
    assert not _rows_of("P1", "웹툰패스", "onboarding") and _rows_of("P1", "웹툰패스")   # 운영 중 첫 결제
    ott_b = {r["날짜"][:7]: -r["금액"] for r in _rows_of("P1", "씨네박스")}
    assert ott_b["2026-10"] == 9900 and ott_b["2026-11"] == 12900                      # 가격 인상
    music_b = {r["날짜"][:7] for r in _rows_of("P1", "멜로디플러스", "onboarding")}
    assert music_b == {"2026-10", "2026-11"}                                           # 10월부터 음악 2개
    assert any(g["payment"].startswith("phone-") for g in w.truth["facts"]["dutch_groups"])
    f = w.truth["facts"]
    shortfall = f["goal"]["monthly_required"] - 200000                                  # 적금 20만 원
    cancelable = 13500 + 7900 + 4900                                                    # 무비온·소리랑·웹툰패스
    assert cancelable < shortfall < cancelable + f["levers"]["delivery"]["monthly_size"]


def test_p2_cafe_trend_and_exam_spike():
    w = _world("P2")
    days = Counter(e.at.strftime("%m-%d") for e in w.events
                   if e.kind == "purchase" and e.category == "카페" and not e.id.startswith("S_"))
    sep = sum(v for k, v in days.items() if k.startswith("09"))
    nov = sum(v for k, v in days.items() if k.startswith("11"))
    exam = sum(v for k, v in days.items() if "10-19" <= k <= "10-30")
    rest = sum(v for k, v in days.items() if k.startswith("10")) - exam
    assert nov >= 1.5 * sep                       # 시험 없이도 증가
    assert exam / 12 >= 2 * rest / 19             # 중간고사 12일이 나머지 19일보다 훨씬 잦다
    m = _monthly("P2")
    surplus = [sum(m[y]["income"].values()) - sum(m[y]["spend"].values()) for y in ("2026-09", "2026-10", "2026-11")]
    assert sum(surplus) / 3 < w.truth["facts"]["goal"]["monthly_required"]   # 여유만으로는 목표에 못 미친다


def test_p3_taxi_annual_charge_and_pt_installment():
    w = _world("P3")
    taxis = [r for r in _all_rows(w) if r["내용"] == "모두택시"]
    assert taxis and all(r["시간"] >= "22:00" for r in taxis)
    assert not _rows_of("P3", "구름드라이브", "onboarding") and _rows_of("P3", "구름드라이브")   # 연간 결제 첫 등장
    assert [i["months"] for i in w.truth["facts"]["installments"]] == [3, 3]                # 10월 PT, 1월 재등록
    nov_bill = next(b for b in w.truth["facts"]["card_bills"] if b["month"] == "2026-11")
    assert nov_bill["installments"] == 550000 - 550000 // 3 * 2                          # PT 1회차
    f = w.truth["facts"]
    shortfall = f["goal"]["monthly_required"] - 700000
    assert f["levers"]["taxi"]["monthly_size"] < shortfall                                # 택시 하나로는 못 메운다
    assert max(f["levers"], key=lambda k: f["levers"][k]["monthly_size"]) == "taxi"


def test_p4_story():
    w = _world("P4")
    m = w.truth["facts"]["monthly"]
    assert m["2026-09"]["income"]["알바"] > m["2026-10"]["income"]["알바"] > m["2026-11"]["income"]["알바"]
    assert m["2026-12"]["income"]["알바"] < m["2026-11"]["income"]["알바"]
    assert m["2026-10"]["saving"] > 0 and m["2026-11"]["saving"] == 0
    variable = [sum(v for k, v in m[ym]["spend"].items() if k != "주거") for ym in ("2026-09", "2026-10", "2026-11")]
    assert max(variable) <= min(variable) * 1.2       # 지출은 비슷하다 (원인은 수입)
    refs = {r["record_id"]: r["transaction_id"] for day in w.records.values() for r in day}
    assert refs["R2"] is None and refs["R3"] is None    # 들어오기 전에 쓴 기록, 현금 기록
    rent = [r for r in w.batches["onboarding"] if r["금액"] == -400000]
    assert rent and all(r["타입"] == "이체" for r in rent)  # 앱은 월세를 지출로 보지 못한다
    coop = [r for r in w.batches["onboarding"] if r["내용"] == "한누리대학교생활협동조합"]
    assert coop and all(r["대분류"] == "쇼핑" for r in coop)  # 앱 오분류


def test_p4_problems_are_tangled():
    """지출 지렛대는 하나하나도, 다 합쳐도 필요 월 저축액에 못 미친다 → 수입·목표 쪽까지 함께 봐야 한다."""
    facts = _world("P4").truth["facts"]
    required, levers = facts["goal"]["monthly_required"], facts["levers"]
    assert facts["goal"]["months_left"] == 3
    spend = {k: v["monthly_size"] for k, v in levers.items() if v["side"] == "지출"}
    assert spend and all(0 < size < required for size in spend.values())
    assert sum(spend.values()) < required
    assert max(levers, key=lambda k: levers[k]["monthly_size"]) == "income_drop"


def test_p5_payday_shopping_returns_and_zombie_subscription():
    w = _world("P5")
    hist = [e for e in w.events if e.at.date() < w.d0]
    buys = [e for e in hist if e.kind == "purchase" and e.category == "쇼핑"]
    after_payday = [e for e in buys if e.at.day >= 25 or e.at.day <= 3]
    assert len(after_payday) >= 0.6 * len(buys)                                        # 월급 직후 몰림
    assert sum(e.kind == "cancel" for e in hist) >= 3                                    # 잦은 반품
    assert {r["날짜"][:7] for r in _rows_of("P5", "무비온")} == {"2026-09", "2026-10", "2026-11", "2026-12", "2027-01"}
    assert [i["months"] for i in w.truth["facts"]["installments"]] == [12]
    f = w.truth["facts"]
    spend = {k: v["monthly_size"] for k, v in f["levers"].items() if v["side"] == "지출"}
    assert max(spend.values()) < f["goal"]["monthly_required"] <= sum(spend.values()) * 1.1   # 다 끊어야 겨우 닿음
    assert f["balance"]["bank_lowest"] < 100000                                          # 어디로 가는지 모르겠다


# ---------------------------------------------------------------- CLI
def test_cli_build_then_advance(tmp_path, capsys):
    root = build("P4", None, out_root=tmp_path)
    with open(root / "onboarding" / "transactions.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert tuple(rows[0]) == EXPORT_COLUMNS and len(rows) == manifest["onboarding"]["transactions"]
    assert not (root / "daily").exists()
    assert advance("P4", 2, out_root=tmp_path) == ["2026-12-01", "2026-12-02"]
    assert (root / "daily" / "2026-12-02" / "records.json").exists()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["built_through"] == "2026-12-02"
    assert advance("P4", 1, out_root=tmp_path) == ["2026-12-03"]
    out = capsys.readouterr().out
    assert "유저 이해" in out and "기록 없음" in out
