"""정기결제 탐지 + 거래 파생 지표 F01~F08 (통합 문서 3-2 탭 "거래 파생 지표").

    python -m pipeline.features --user u_demo

모두 결정적 규칙으로 계산한다 (LLM 없음). 결과는 data/user.db 에 저장:
  recurring        정기결제 후보 (detect_recurring)
  feature_weekly   사용자·주(월요일 시작)별 지표 F01~F04, F08
  feature_period   기간 단위 지표 F05~F07, F08 요약
각 행에 FEATURE_VERSION 을 남겨, 규칙이 바뀌면 다시 계산한 값과 구분한다.

정기결제 규칙: 같은 가맹점(정규화 브랜드 우선), 금액 차이 10% 이내, 간격 25~35일(월)/6~8일(주)/350~380일(연), 2회 이상.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
from collections import defaultdict

from . import merchants

FEATURE_VERSION = "2026-11-24.1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS recurring (
    user_id TEXT, merchant_key TEXT, merchant_norm TEXT, cycle TEXT, typical_amount INTEGER,
    occurrences INTEGER, first_date TEXT, last_date TEXT, next_expected TEXT,
    payment_sources TEXT, confidence REAL, txn_ids TEXT, version TEXT,
    PRIMARY KEY (user_id, merchant_key, cycle)
);
CREATE TABLE IF NOT EXISTS feature_weekly (
    user_id TEXT, week_start TEXT, feature TEXT, value REAL, detail TEXT, version TEXT,
    PRIMARY KEY (user_id, week_start, feature)
);
CREATE TABLE IF NOT EXISTS feature_period (
    user_id TEXT, feature TEXT, key TEXT, value REAL, detail TEXT, period TEXT, version TEXT,
    PRIMARY KEY (user_id, feature, key)
);
"""

CYCLES = {"monthly": (25, 35), "weekly": (6, 8), "yearly": (350, 380)}


def week_start(day: str) -> str:
    d = dt.date.fromisoformat(day)
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def load_txns(con, user_id):
    con.row_factory = None
    cols = ["txn_id", "occurred_at", "occurred_date", "amount", "txn_type", "merchant_raw",
            "merchant_norm", "category", "tags", "format_id", "dup_status"]
    rows = con.execute(
        f"SELECT {', '.join(cols)} FROM txn WHERE user_id=? AND txn_type IN ('spend','refund')"
        " AND COALESCE(dup_status,'') NOT IN ('candidate','confirmed_dup') ORDER BY occurred_at", (user_id,)).fetchall()
    out = []
    for r in rows:
        t = dict(zip(cols, r))
        t["tags"] = set(json.loads(t["tags"])) if t["tags"] else set()
        t["key"] = t["merchant_norm"] or merchants.norm_text(t["merchant_raw"])
        out.append(t)
    return out


# ------------------------------------------------------------------ 정기결제
def detect_recurring(txns) -> list[dict]:
    by_key = defaultdict(list)
    for t in txns:
        if t["txn_type"] == "spend" and t["amount"] > 0:
            by_key[t["key"]].append(t)
    found = []
    for key, items in by_key.items():
        items.sort(key=lambda t: t["occurred_date"])
        # 구독의 같은 달 중복 결제(F07 대상)는 연쇄 비율 계산에서 한 번으로 본다
        if any("subscription" in t["tags"] for t in items):
            items = list({(t["occurred_date"][:7], t["amount"]): t for t in items}.values())
            items.sort(key=lambda t: t["occurred_date"])
        for cycle, (lo, hi) in CYCLES.items():
            # 가장 긴 연쇄: 간격이 범위 안이고 금액이 직전 대비 10% 이내
            best = []
            for i in range(len(items)):
                chain = [items[i]]
                for t in items[i + 1:]:
                    gap = (dt.date.fromisoformat(t["occurred_date"]) - dt.date.fromisoformat(chain[-1]["occurred_date"])).days
                    if gap < lo:
                        continue
                    if gap > hi:
                        break
                    if abs(t["amount"] - chain[-1]["amount"]) <= 0.1 * chain[-1]["amount"]:
                        chain.append(t)
                if len(chain) > len(best):
                    best = chain
            # 자주 가는 가게(카페·배달)가 우연히 30일 간격 쌍을 만드는 오탐 방지:
            # 연쇄가 그 가맹점 결제의 80% 이상을 차지해야 정기결제로 본다
            if len(best) >= 2 and len(best) / len(items) >= 0.8:
                amounts = [t["amount"] for t in best]
                last = dt.date.fromisoformat(best[-1]["occurred_date"])
                step = {"monthly": 30, "weekly": 7, "yearly": 365}[cycle]
                found.append({
                    "merchant_key": key, "merchant_norm": best[0]["merchant_norm"], "cycle": cycle,
                    "typical_amount": int(statistics.median(amounts)), "occurrences": len(best),
                    "first_date": best[0]["occurred_date"], "last_date": best[-1]["occurred_date"],
                    "next_expected": (last + dt.timedelta(days=step)).isoformat(),
                    "payment_sources": sorted({t["format_id"] for t in best}),
                    "confidence": round(min(1.0, 0.4 + 0.2 * len(best)), 2),
                    "txn_ids": [t["txn_id"] for t in best],
                })
                break  # 한 가맹점은 가장 짧은 주기 하나로
    return found


# ------------------------------------------------------------------ 지표
def compute(con, user_id) -> dict:
    con.executescript(SCHEMA)
    merchants.apply(con, user_id)
    txns = load_txns(con, user_id)
    if not txns:
        return {}
    period = f"{txns[0]['occurred_date']}~{txns[-1]['occurred_date']}"
    weekly = defaultdict(lambda: defaultdict(lambda: {"count": 0, "amount": 0}))
    spends = [t for t in txns if t["txn_type"] == "spend"]

    for t in spends:
        w = week_start(t["occurred_date"])
        hour = int(t["occurred_at"][11:13]) if len(t["occurred_at"]) > 10 else None
        if "delivery_app" in t["tags"]:                                  # F01
            weekly[w]["F01_delivery"]["count"] += 1
            weekly[w]["F01_delivery"]["amount"] += t["amount"]
        if "convenience" in t["tags"] and t["amount"] < 10000:           # F02
            weekly[w]["F02_conv_small"]["count"] += 1
            weekly[w]["F02_conv_small"]["amount"] += t["amount"]
        if "taxi" in t["tags"]:                                          # F03
            f = weekly[w]["F03_taxi"]
            f["count"] += 1
            f["amount"] += t["amount"]
            f["late_night"] = f.get("late_night", 0) + (1 if hour is not None and hour < 5 else 0)
            f["time_known"] = f.get("time_known", 0) + (1 if hour is not None else 0)
        if "cafe" in t["tags"]:                                          # F04
            f = weekly[w]["F04_cafe"]
            f["count"] += 1
            f["amount"] += t["amount"]
            f.setdefault("brands", defaultdict(int))[t["merchant_norm"]] += 1
        if "transit" in t["tags"]:                                       # F08 (주간)
            f = weekly[w]["F08_transit"]
            f["count"] += 1
            f["amount"] += t["amount"]
            f["wide"] = f.get("wide", 0) + (t["amount"] if "transit_wide" in t["tags"] else 0)

    rows = []
    for w, feats in weekly.items():
        for name, d in feats.items():
            value = d["count"]
            detail = {k: (dict(v) if isinstance(v, defaultdict) else v) for k, v in d.items()}
            if name == "F03_taxi":
                # 결제 시각이 있는 거래만 심야 비율 분모에 넣는다 (카드사 A형은 날짜만 있음)
                detail["late_night_ratio"] = round(d["late_night"] / d["time_known"], 2) if d.get("time_known") else None
            if name == "F04_cafe":
                detail["carbon_point_benefit"] = carbon_point_benefit(detail["brands"])
            rows.append((user_id, w, name, value, json.dumps(detail, ensure_ascii=False), FEATURE_VERSION))
    con.execute("DELETE FROM feature_weekly WHERE user_id=?", (user_id,))
    con.executemany("INSERT INTO feature_weekly VALUES (?,?,?,?,?,?)", rows)

    # 정기결제
    rec = detect_recurring(txns)
    con.execute("DELETE FROM recurring WHERE user_id=?", (user_id,))
    con.executemany(
        "INSERT INTO recurring VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(user_id, r["merchant_key"], r["merchant_norm"], r["cycle"], r["typical_amount"], r["occurrences"],
          r["first_date"], r["last_date"], r["next_expected"], json.dumps(r["payment_sources"]),
          r["confidence"], json.dumps(r["txn_ids"]), FEATURE_VERSION) for r in rec])

    prows = []
    # F05 가맹점 내 결제액 분포 (같은 가맹점 5회 이상)
    by_m = defaultdict(list)
    for t in spends:
        by_m[t["key"]].append(t["amount"])
    for k, amts in by_m.items():
        if len(amts) >= 5:
            q = statistics.quantiles(amts, n=4)
            prows.append(("F05_merchant_dist", k, statistics.median(amts),
                          {"n": len(amts), "median": statistics.median(amts), "p75": q[2], "min": min(amts), "max": max(amts)}))

    # F06 신규 정기결제 첫 발생: 0원·소액 승인 뒤 같은 가맹점 정상가 결제 (무료체험 유료 전환)
    for k, items in group_by_key(spends + [t for t in txns if t["txn_type"] == "spend" and t["amount"] == 0]).items():
        items.sort(key=lambda t: t["occurred_date"])
        for i, t in enumerate(items):
            if t["amount"] <= 1000:
                later = [u for u in items[i + 1:] if u["amount"] > 1000
                         and 20 <= (dt.date.fromisoformat(u["occurred_date"]) - dt.date.fromisoformat(t["occurred_date"])).days <= 40]
                if later:
                    prows.append(("F06_trial_conversion", k, later[0]["amount"],
                                  {"trial_date": t["occurred_date"], "trial_amount": t["amount"],
                                   "paid_date": later[0]["occurred_date"], "paid_amount": later[0]["amount"]}))
                    break

    # F07 같은 서비스 다중 결제: 같은 구독이 같은 달에 2번 이상 (결제수단이 달라도)
    month_sub = defaultdict(list)
    for t in spends:
        if "subscription" in t["tags"]:
            month_sub[(t["key"], t["occurred_date"][:7])].append(t)
    for (k, month), items in month_sub.items():
        if len(items) >= 2:
            prows.append(("F07_duplicate_subscription", f"{k}|{month}", len(items),
                          {"month": month, "amounts": [t["amount"] for t in items],
                           "raw_names": sorted({t["merchant_raw"] for t in items}),
                           "sources": sorted({t["format_id"] for t in items})}))

    # F08 교통 패턴 변화: 최근 2개월 vs 그 이전 (월평균 건수, 광역 요금 비중)
    transit = [t for t in spends if "transit" in t["tags"]]
    if transit:
        end = dt.date.fromisoformat(txns[-1]["occurred_date"])
        cut = end - dt.timedelta(days=61)
        recent = [t for t in transit if dt.date.fromisoformat(t["occurred_date"]) > cut]
        before = [t for t in transit if dt.date.fromisoformat(t["occurred_date"]) <= cut]
        start = dt.date.fromisoformat(txns[0]["occurred_date"])
        before_months = max((cut - start).days / 30.4, 0.5)

        def summ(ts, months):
            amt = sum(t["amount"] for t in ts)
            wide = sum(t["amount"] for t in ts if "transit_wide" in t["tags"])
            return {"monthly_count": round(len(ts) / months, 1), "monthly_amount": round(amt / months),
                    "wide_share": round(wide / amt, 3) if amt else 0.0}
        r, b = summ(recent, 2.0), summ(before, before_months)
        changed = (b["monthly_amount"] and abs(r["monthly_amount"] - b["monthly_amount"]) / b["monthly_amount"] >= 0.2) \
            or abs(r["wide_share"] - b["wide_share"]) >= 0.15
        prows.append(("F08_transit_change", "all", 1.0 if changed else 0.0,
                      {"recent_2m": r, "before": b, "changed": bool(changed), "cut": cut.isoformat()}))

    con.execute("DELETE FROM feature_period WHERE user_id=?", (user_id,))
    con.executemany("INSERT INTO feature_period VALUES (?,?,?,?,?,?,?)",
                    [(user_id, f, k, v, json.dumps(d, ensure_ascii=False, default=float), period, FEATURE_VERSION)
                     for f, k, v, d in prows])
    con.commit()
    return {"weeks": len(weekly), "recurring": rec, "period_features": prows}


def group_by_key(txns):
    g = defaultdict(list)
    seen = set()
    for t in txns:
        if t["txn_id"] not in seen:
            seen.add(t["txn_id"])
            g[t["key"]].append(t)
    return g


def carbon_point_benefit(brands: dict) -> dict:
    """F04: 텀블러를 썼다면 받을 수 있었던 최대 혜택 = 참여 브랜드 결제 건수 × 단가.
    참여 여부(carbon_point)가 확인된 브랜드만 계산하고, 단가는 수동 규칙 DB에서 계산 가능(ok) 상태일 때만 쓴다."""
    known = {m["brand"]: m.get("carbon_point") for m in merchants.load_dict()[1]}
    eligible = {b: n for b, n in brands.items() if known.get(b) is True}
    unknown = sorted(b for b in brands if known.get(b) is None)
    return {"eligible_count": sum(eligible.values()), "unknown_brands": unknown,
            "note": "참여 브랜드 미확인: 혜택 금액은 계산하지 않음" if unknown and not eligible else None}


def main(argv=None):
    from . import ingest
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    a = ap.parse_args(argv)
    con = ingest.connect()
    r = compute(con, a.user)
    print(f"주간 지표: {r['weeks']}주")
    print("정기결제:")
    for x in r["recurring"]:
        print(f"  {x['merchant_norm'] or x['merchant_key']}: {x['cycle']} {x['typical_amount']:,}원 × {x['occurrences']}회"
              f" (다음 예상 {x['next_expected']}, 결제수단 {', '.join(x['payment_sources'])})")
    for f, k, v, d in r["period_features"]:
        if f != "F05_merchant_dist":
            print(f"  {f} [{k}]: {json.dumps(d, ensure_ascii=False, default=float)}")


if __name__ == "__main__":
    main()
