"""MCP Tool 구현 (순수 함수). mcp_server.py 가 이 함수들을 Tool로 등록한다.

원칙 (통합 문서 1-1·3-2 탭):
- 숫자는 여기서만 만든다. LLM은 이 결과의 숫자만 인용한다.
- 모든 결과는 envelope.result() 형식: 값, 계산 내역, 출처, 기준일, 주의사항.
- 근거가 공식 확인 전(needs_official_source)이거나 오래됐으면 계산하지 않고 needs_check 를 돌려준다.
- 검색 결과 가격은 받지 않는다. 가격 입력은 사용자 확인·영수증·캡처·공공 API 출처만 허용.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics

from pipeline import features, ingest, merchants
from . import cache
from .envelope import SOURCES, result, source

ALLOWED_PRICE_SOURCES = {"user_verified", "receipt_ocr", "capture_ocr", "public_api"}
MONTHS_HORIZON = 12

USER_PRICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_price (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, product_name TEXT NOT NULL,
    size TEXT, unit_size REAL, unit TEXT, price INTEGER NOT NULL, seller TEXT, checked_date TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('user_verified','receipt_ocr','capture_ocr','public_api')),
    created_at TEXT
);
"""


def _user_db():
    return ingest.connect()


def _rules(program_like: str | None = None, section: str | None = None):
    con = cache.prices_db()
    con.row_factory = None
    try:
        q = "SELECT id, section, program, source_url, source_grade, as_of, review_by, usable, body FROM v_manual_rule_active WHERE 1=1"
        args = []
        if program_like:
            q += " AND program LIKE ?"
            args.append(f"%{program_like}%")
        if section:
            q += " AND section = ?"
            args.append(section)
        cols = ["id", "section", "program", "source_url", "source_grade", "as_of", "review_by", "usable", "body"]
        return [dict(zip(cols, r), body=json.loads(r[8])) for r in con.execute(q, args)]
    except Exception:
        return []


def _rule_source(r):
    return source("M_RULES", r["as_of"], rule_id=r["id"], url=r["source_url"], grade=r["source_grade"], usable=r["usable"])


# ================================================================== 정기결제·구독
def detect_recurring(user_id: str) -> dict:
    con = _user_db()
    con.executescript(features.SCHEMA)
    if not con.execute("SELECT 1 FROM recurring WHERE user_id=? LIMIT 1", (user_id,)).fetchone():
        features.compute(con, user_id)
    rows = con.execute("SELECT merchant_norm, merchant_key, cycle, typical_amount, occurrences, first_date, last_date,"
                       " next_expected, payment_sources, confidence FROM recurring WHERE user_id=? ORDER BY typical_amount DESC",
                       (user_id,)).fetchall()
    items = [{"merchant": r[0] or r[1], "cycle": r[2], "amount": r[3], "occurrences": r[4], "first": r[5], "last": r[6],
              "next_expected": r[7], "payment_sources": json.loads(r[8]), "confidence": r[9]} for r in rows]
    monthly = sum(i["amount"] for i in items if i["cycle"] == "monthly")
    last = max((i["last"] for i in items), default=None)
    return result({"recurring": items, "monthly_total": monthly},
                  calc={"rule": "같은 가맹점, 금액 ±10%, 간격 25~35일(월)/6~8일(주)/350~380일(연), 2회 이상, 가맹점 결제의 80% 이상",
                        "monthly_total": " + ".join(f"{i['amount']:,}" for i in items if i["cycle"] == "monthly") or "0"},
                  sources=[source("U_TXN", last)],
                  caveats=["2회만 관측된 항목은 신뢰도가 낮다. 사용자에게 계속 쓰는지 확인한다."])


def check_subscriptions(user_id: str) -> dict:
    rec = detect_recurring(user_id)["value"]["recurring"]
    sub_names = {m["brand"]: m.get("subscription") for m in merchants.load_dict()[1] if "subscription" in m.get("tags", [])}
    con = _user_db()
    items, sources, caveats = [], [source("U_TXN", max((r["last"] for r in rec), default=None))], []
    for r in rec:
        if r["merchant"] not in sub_names:
            continue
        program = sub_names[r["merchant"]]
        item = {"service": r["merchant"], "paid_amount": r["amount"], "cycle": r["cycle"], "last_paid": r["last"],
                "next_expected": r["next_expected"], "payment_sources": r["payment_sources"], "plan": None, "options": []}
        plans = [p for p in _rules(program) if p["section"] == "subscriptions"] if program else []
        exact = [p for p in plans if p["body"].get("monthly_price") == r["amount"]]
        if exact:
            p = exact[0]
            item["plan"] = {"name": p["body"].get("plan"), "price_grade": p["source_grade"], "usable": p["usable"]}
            sources.append(_rule_source(p))
            annual = p["body"].get("annual_price")
            if annual:
                saving = r["amount"] * 12 - annual
                if p["usable"] == "ok":
                    item["options"].append({"type": "annual", "annual_price": annual, "yearly_saving": saving,
                                            "calc": f"{r['amount']:,} × 12 − {annual:,} = {saving:,}"})
                else:
                    item["options"].append({"type": "annual", "status": "needs_check",
                                            "reason": "연간 요금이 공식 출처로 확인되지 않아 절감액을 계산하지 않음"})
            cheaper = sorted((q for q in plans if q["body"].get("monthly_price", 10**9) < r["amount"]),
                             key=lambda q: q["body"]["monthly_price"])
            for q in cheaper[:2]:
                opt = {"type": "downgrade", "plan": q["body"].get("plan"), "usable": q["usable"]}
                if q["usable"] == "ok":
                    opt.update(monthly_price=q["body"]["monthly_price"],
                               monthly_saving=r["amount"] - q["body"]["monthly_price"])
                else:
                    opt["status"] = "needs_check"
                item["options"].append(opt)
                sources.append(_rule_source(q))
        elif plans:
            caveats.append(f"{r['merchant']}: 결제액 {r['amount']:,}원과 일치하는 요금제가 규칙 DB에 없음 (요금 변경 또는 앱 결제 가능성)")
        items.append(item)

    fp = con.execute("SELECT feature, key, detail FROM feature_period WHERE user_id=? AND feature IN "
                     "('F06_trial_conversion','F07_duplicate_subscription')", (user_id,)).fetchall()
    alerts = []
    for f, k, d in fp:
        d = json.loads(d)
        if f == "F07_duplicate_subscription":
            alerts.append({"type": "duplicate", "service": k.split("|")[0], "month": d["month"],
                           "amounts": d["amounts"], "raw_names": d["raw_names"],
                           "possible_extra": sum(d["amounts"]) - max(d["amounts"])})
        else:
            alerts.append({"type": "trial_converted", "service": k, **d})
    total = sum(i["paid_amount"] for i in items if i["cycle"] == "monthly")
    return result({"subscriptions": items, "monthly_total": total, "alerts": alerts}, sources=sources,
                  calc={"monthly_total": " + ".join(f"{i['paid_amount']:,}" for i in items) or "0"},
                  caveats=caveats + ["유지·변경·해지는 사용자가 결정한다. 해지는 각 서비스 공식 앱·웹에서 진행한다."])


# ================================================================== 목표
def forecast_goal(user_id: str, goal_amount: int, deadline: str, current_saved: int = 0,
                  monthly_income: int | None = None, months_basis: int = 3) -> dict:
    con = _user_db()
    last = con.execute("SELECT max(occurred_date) FROM txn WHERE user_id=?", (user_id,)).fetchone()[0]
    if not last:
        return result(status="no_data", caveats=["거래내역이 없습니다."])
    last_d = dt.date.fromisoformat(last)
    # 최근 N개 완결된 달 (마지막 달이 말일까지 있으면 포함)
    end_month = last_d.replace(day=1) if (last_d + dt.timedelta(days=1)).day != 1 else (last_d + dt.timedelta(days=1)).replace(day=1)
    months = []
    m = end_month
    for _ in range(months_basis):
        m = (m - dt.timedelta(days=1)).replace(day=1)
        months.append(m.strftime("%Y-%m"))
    spend = {mm: 0 for mm in months}
    for mm, s in con.execute("SELECT substr(occurred_date,1,7), sum(amount) FROM v_spending WHERE user_id=?"
                             " AND COALESCE(dup_status,'')!='candidate' GROUP BY 1", (user_id,)):
        if mm in spend:
            spend[mm] = s
    income_src = "입력값"
    if monthly_income is None:
        inc = dict(con.execute("SELECT substr(occurred_date,1,7), sum(amount) FROM txn WHERE user_id=? AND txn_type='income'"
                               " GROUP BY 1", (user_id,)).fetchall())
        vals = [inc.get(mm, 0) for mm in months]
        monthly_income = round(sum(vals) / len(vals))
        income_src = f"최근 {months_basis}개월 수입 거래 평균"
    avg_spend = round(sum(spend.values()) / len(spend))
    surplus = monthly_income - avg_spend
    dl = dt.date.fromisoformat(deadline)
    today = last_d
    months_left = max((dl.year - today.year) * 12 + dl.month - today.month, 1)
    need = max(goal_amount - current_saved, 0)
    required = -(-need // months_left)  # 올림
    projected = current_saved + surplus * months_left
    gap = projected - goal_amount
    if surplus > 0:
        eta_months = -(-need // surplus)
        y, mo = divmod(today.month - 1 + eta_months, 12)
        eta = f"{today.year + y}-{mo + 1:02d}"
    else:
        eta = None
    return result(
        {"monthly_required": required, "monthly_surplus": surplus, "months_left": months_left,
         "projected_at_deadline": projected, "gap_at_deadline": gap, "on_track": gap >= 0,
         "monthly_shortfall": max(required - surplus, 0), "eta_month": eta},
        calc={"required": f"({goal_amount:,} − {current_saved:,}) ÷ {months_left}개월 = {required:,} (올림)",
              "avg_spend": f"({' + '.join(f'{v:,}' for v in spend.values())}) ÷ {len(spend)} = {avg_spend:,} ({', '.join(months)})",
              "income": f"{monthly_income:,} ({income_src})",
              "surplus": f"{monthly_income:,} − {avg_spend:,} = {surplus:,}",
              "projected": f"{current_saved:,} + {surplus:,} × {months_left} = {projected:,}"},
        sources=[source("U_TXN", last)],
        caveats=["이자·추가 수입이 없다는 가정의 시뮬레이션이며 예측이 아니다.",
                 "할부·일회성 큰 지출이 있는 달은 평균을 끌어올린다. calc 의 월별 지출을 함께 보여준다.",
                 "지출 평균은 이체·카드대금 정산·중복 후보를 제외한 순지출이다."])


# ================================================================== 통신
def compare_mobile_plan(current_monthly_fee: int, data_gb_needed: float, months_left_on_contract: int = 0,
                        early_termination_fee: int | None = None, bundle_discount_lost_monthly: int = 0,
                        switching_cost: int = 0, include_prepaid: bool = False, top_n: int = 3) -> dict:
    if months_left_on_contract > 0 and early_termination_fee is None:
        return result(status="needs_check", caveats=["약정이 남아 있으면 위약금을 사용자에게 확인해야 순절감액을 계산할 수 있다."])
    url = ("http://openapi.epost.go.kr/postal/retrieveAlddlChargeService/retrieveAlddlChargeService/getAlddlChargeList")
    body, fetched, stale = cache.cached_get("P01_EPOST_MVNO", url, {}, key_name="ServiceKey")
    plans = cache.common.parse_xml_items(body, "alddlCharge")
    need_mb = data_gb_needed * 1024
    cands = []
    for p in plans:
        fee = cache.common.num(p.get("chargeAmount"))
        data = cache.common.num(p.get("dataAmount")) or 0
        if fee is None or data < need_mb or fee >= current_monthly_fee:
            continue
        if not include_prepaid and "선불" in (p.get("chargeDiv") or ""):
            continue
        diff = current_monthly_fee - fee
        now_net = (diff - bundle_discount_lost_monthly) * MONTHS_HORIZON - (early_termination_fee or 0) - switching_cost
        wait_net = (diff - bundle_discount_lost_monthly) * (MONTHS_HORIZON - months_left_on_contract) - switching_cost
        cands.append({"plan": p.get("chargeName"), "carrier_network": p.get("telecomName"), "mvno": p.get("bizName"),
                      "monthly_fee": int(fee), "data_mb": int(data), "voice_min_raw": p.get("voiceAmount"),
                      "type": p.get("chargeDiv"),
                      "net_saving_12m_switch_now": int(now_net),
                      "net_saving_12m_after_contract": int(wait_net) if months_left_on_contract else None,
                      "calc": f"({current_monthly_fee:,} − {int(fee):,} − {bundle_discount_lost_monthly:,}) × {MONTHS_HORIZON}"
                              f" − 위약금 {early_termination_fee or 0:,} − 전환비용 {switching_cost:,} = {int(now_net):,}"})
    cands.sort(key=lambda c: -max(c["net_saving_12m_switch_now"], c["net_saving_12m_after_contract"] or -10**9))
    status = "stale" if stale else ("ok" if cands else "no_data")
    return result({"candidates": cands[:top_n], "considered": len(plans), "eligible": len(cands)},
                  sources=[source("P01_EPOST_MVNO", fetched[:10])], status=status,
                  calc={"horizon_months": MONTHS_HORIZON, "data_filter": f"데이터 ≥ {data_gb_needed}GB ({int(need_mb)}MB)"},
                  caveats=["우체국에서 판매하는 알뜰폰 요금제만 비교했다. 통신 3사·기타 알뜰폰 요금제(M01)는 아직 포함되지 않는다.",
                           "음성 0은 기본제공/무제한/미제공이 구분되지 않는다. 요금제 상세를 사용자에게 확인시킨다.",
                           "결합할인 해제 금액과 위약금은 사용자 입력값이다."])


# ================================================================== 교통
def calc_transit_refund(monthly_transit_spend: int, user_type: str = "일반", region: str = "수도권") -> dict:
    rules = [r for r in _rules("모두의카드") if user_type in (r["body"].get("user_type") or "")
             and region in (r["body"].get("region") or "")]
    if not rules:
        return result(status="no_data", caveats=[f"{region}·{user_type} 환급 규칙이 없다."])
    r = rules[0]
    if r["usable"] != "ok":
        return result(status="needs_check", sources=[_rule_source(r)],
                      caveats=[f"환급 규칙({r['id']})이 공식 출처로 확인되지 않아({r['usable']}) 환급액을 계산하지 않는다."])
    b = r["body"]
    options = {"비율형": round(monthly_transit_spend * b["refund_rate"])}
    for name, base in (b.get("flat_options") or {}).items():
        options[name] = max(monthly_transit_spend - base, 0)
    best = max(options, key=options.get)
    return result({"options": options, "best": best, "best_refund": options[best]}, sources=[_rule_source(r)],
                  calc={"비율형": f"{monthly_transit_spend:,} × {b['refund_rate']}"},
                  caveats=["정액형 환급 방식은 공식 공지 기준으로 다시 확인한다."])


# ================================================================== 가격
def get_price_baseline(query: str, area: str | None = None, include_online: bool = True) -> dict:
    con = cache.prices_db()
    out, sources, caveats = {}, [], []
    goods = con.execute("SELECT good_id, good_name, total_cnt, total_div_code, base_cnt, unit_div_code FROM kca_product"
                        " WHERE good_name LIKE ? ORDER BY length(good_name) LIMIT 5", (f"%{query}%",)).fetchall()
    offline = []
    for gid, name, total, tdiv, base, udiv in goods:
        day = con.execute("SELECT max(inspect_day) FROM kca_price WHERE good_id=?", (gid,)).fetchone()[0]
        if not day:
            continue
        q = "SELECT price, entp_name, area_name, unit_price FROM v_kca_price WHERE good_id=? AND inspect_day=?"
        args = [gid, day]
        if area:
            q += " AND area_name LIKE ?"
            args.append(f"%{area}%")
        rows = con.execute(q, args).fetchall()
        if not rows:
            continue
        prices = sorted(r[0] for r in rows)
        cheapest = min(rows, key=lambda r: r[0])
        age = (dt.date.today() - dt.datetime.strptime(day, "%Y%m%d").date()).days
        offline.append({"product": name, "inspect_day": day, "stores": len(rows), "min": prices[0],
                        "median": statistics.median(prices), "max": prices[-1],
                        "cheapest_store": cheapest[1], "unit_price": cheapest[3],
                        "unit": f"{int(base)}{udiv}당" if base else None, "stale": age > SOURCES["P02_KCA_PRICE"]["ttl_days"]})
        sources.append(source("P02_KCA_PRICE", f"{day[:4]}-{day[4:6]}-{day[6:]}", good_id=gid))
    out["offline_P02"] = offline
    if include_online:
        item = con.execute("SELECT item_code, item_name FROM online_item WHERE item_name LIKE ? ORDER BY length(item_name) LIMIT 1",
                           (f"%{query}%",)).fetchone() if _has_table(con, "online_item") else None
        if item:
            online = _online_prices(item[0])
            if online:
                online["item"] = item[1]
                out["online_P04"] = online
                sources.append(source("P04_KOSTAT_ONLINE", online["day"], item_code=item[0]))
                caveats.append("온라인 가격은 같은 품목이라도 용량·구성이 제각각이라 가격 범위로만 본다. 단위가격 비교에는 쓰지 않는다.")
    status = "ok" if (offline or out.get("online_P04")) else "no_data"
    if any(o["stale"] for o in offline):
        status = "stale"
        caveats.append("생필품 가격 기준일이 45일을 넘었다.")
    caveats.append("공공 가격은 기준선이다. 실시간 최저가나 시장 전체 최저가를 보장하지 않는다.")
    return result(out, sources=sources, caveats=caveats, status=status)


def _has_table(con, name):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone())


def _online_prices(item_code: str, max_back: int = 20) -> dict | None:
    url = "https://apis.data.go.kr/1240000/bpp_openapi/getPriceInfo"
    d = dt.date.today()
    for _ in range(max_back):
        d -= dt.timedelta(days=1)
        ds = d.strftime("%Y%m%d")
        try:
            body, fetched, stale = cache.cached_get("P04_KOSTAT_ONLINE", url,
                                                    {"pageNo": 1, "numOfRows": 1000, "itemCode": item_code,
                                                     "startDate": ds, "endDate": ds}, key_name="ServiceKey")
        except Exception:
            continue
        rows = cache.common.parse_xml_items(body)
        prices = sorted(p for p in (cache.common.num(r.get("dp") or r.get("sp")) for r in rows) if p)
        if prices:
            q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0], statistics.median(prices), prices[-1]]
            return {"day": d.isoformat(), "products": len(prices), "p25": q[0], "median": statistics.median(prices),
                    "p75": q[-1], "stale": stale}
    return None


def calc_bulk_purchase(options: list[dict], daily_use: float, shelf_life_days: int | None = None,
                       cash_available: int | None = None) -> dict:
    """options: [{name, price, quantity, unit_size, unit, source_type}] (unit_size 는 1개당 용량, 예: 1000 g)."""
    bad = [o.get("name") for o in options if o.get("source_type") not in ALLOWED_PRICE_SOURCES]
    if bad:
        return result(status="needs_check", caveats=[
            f"출처 유형이 허용되지 않은 가격: {bad}. 검색 결과 가격은 계산에 쓰지 않는다. 판매처에서 확인한 가격을 입력한다."])
    if daily_use <= 0:
        return result(status="needs_check", caveats=["하루 사용량이 필요하다."])
    rows = []
    for o in options:
        total = o["quantity"] * o["unit_size"]
        unit_price = o["price"] / total
        days = total / daily_use
        rows.append({"name": o["name"], "price": o["price"], "total_size": total, "unit": o.get("unit"),
                     "_unit_price": unit_price, "unit_price": round(unit_price, 3), "days_to_use_up": round(days, 1),
                     "within_shelf_life": None if shelf_life_days is None else days <= shelf_life_days,
                     "affordable": None if cash_available is None else o["price"] <= cash_available,
                     "source_type": o["source_type"]})
    ok = [r for r in rows if r["within_shelf_life"] is not False and r["affordable"] is not False]
    best = min(ok, key=lambda r: r["_unit_price"]) if ok else None
    base = max(rows, key=lambda r: r["_unit_price"])
    calc = {r["name"]: f"{r['price']:,} ÷ {r['total_size']:,}{r.get('unit') or ''} = {r['unit_price']}" for r in rows}
    caveats = []
    if best:
        saving = round((base["_unit_price"] - best["_unit_price"]) * best["total_size"])  # 반올림 전 값으로 계산
        calc["saving_vs_most_expensive"] = f"({base['unit_price']} − {best['unit_price']}) × {best['total_size']:,} = {saving:,}"
    else:
        saving = None
        caveats.append("유통기한 안에 다 쓰거나 지금 낼 수 있는 선택지가 없다.")
    for r in rows:
        r.pop("_unit_price")
    return result({"options": rows, "best": best and best["name"], "saving_vs_most_expensive": saving},
                  calc=calc, sources=[source("U_PRICE", dt.date.today().isoformat())],
                  caveats=caveats + ["단위가격이 싸도 다 쓰지 못하면 손해다. 소진 기간과 지금 현금을 함께 본다."])


def record_user_price(user_id: str, product_name: str, price: int, checked_date: str, source_type: str,
                      seller: str | None = None, size: str | None = None, unit_size: float | None = None,
                      unit: str | None = None) -> dict:
    if source_type not in ALLOWED_PRICE_SOURCES - {"public_api"}:
        return result(status="needs_check", caveats=[
            "사용자가 판매처에서 확인한 가격만 저장한다 (user_verified / receipt_ocr / capture_ocr). 검색 결과는 저장하지 않는다."])
    con = _user_db()
    con.executescript(USER_PRICE_SCHEMA)
    cur = con.execute("INSERT INTO user_price(user_id, product_name, size, unit_size, unit, price, seller, checked_date,"
                      " source_type, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (user_id, product_name, size, unit_size, unit, price, seller, checked_date, source_type,
                       dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return result({"id": cur.lastrowid}, sources=[source("U_PRICE", checked_date)],
                  caveats=["이 가격은 이 사용자 본인의 비교에만 쓴다."])


# ================================================================== 제도·출처
def check_benefit_programs(region: str | None = None) -> dict:
    rules = [r for r in _rules(section="rules")]
    if region:
        rules = [r for r in rules if not r["body"].get("region") or region in r["body"]["region"]]
    items = [{"id": r["id"], "program": r["program"], "benefit": r["body"].get("benefit"),
              "value": r["body"].get("unit_value") if r["usable"] == "ok" else None,
              "unit": r["body"].get("unit"), "usable": r["usable"], "notes": r["body"].get("notes")} for r in rules]
    return result({"programs": items}, sources=[_rule_source(r) for r in rules],
                  caveats=["usable 이 ok 가 아닌 제도는 수치를 말하지 않고 공식 안내 링크만 준다.",
                           "본인인증이 필요한 조회는 대행하지 않는다."])


def get_source_info(source_id: str) -> dict:
    if source_id not in SOURCES:
        return result(status="no_data", caveats=[f"알 수 없는 출처: {source_id}"])
    return result(source(source_id), sources=[source(source_id)])
