"""P04 통계청(국가데이터처) 온라인 수집 가격 (data.go.kr 15080757). 온라인 가격 기준선용.

    python collectors/kostat_online_price.py items                 # 품목 목록
    python collectors/kostat_online_price.py prices --days 7       # 최근 7일 전 품목 가격
    python collectors/kostat_online_price.py prices --start 20261101 --end 20261120 --item A01101

상태: 경로(getPriceItemList, getPriceInfo)는 게이트웨이 응답으로 존재 확인.
응답 필드 (2026-11-24 실제 응답으로 확인): 품목 ic=코드, in=이름 / 가격 pi=상품ID, pn=상품명, sp=정가,
dp=할인가, bp=혜택, sd=날짜. 최근 며칠은 아직 없을 수 있다 (resultCode 21).
"""
import argparse
import datetime as dt
import hashlib
import json

from common import connect, load_key, http_get, parse_xml_items, save_raw, log, now, num, ApiError

SOURCE_ID = "P04_KOSTAT_ONLINE"
BASE = "https://apis.data.go.kr/1240000/bpp_openapi"

SCHEMA = """
CREATE TABLE IF NOT EXISTS online_item (
    item_code TEXT PRIMARY KEY, item_name TEXT, raw TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS online_price (
    item_code TEXT, price_day TEXT, product_name TEXT, price REAL, row_hash TEXT,
    raw TEXT, fetched_at TEXT,
    PRIMARY KEY (item_code, price_day, row_hash)
);
"""


def pick(r: dict, *names):
    for n in names:
        if r.get(n) not in (None, ""):
            return r[n]
    return None


def paged(op, params, key, key_name):
    page, out = 1, []
    while True:
        p = {key_name: key, "pageNo": page, "numOfRows": 1000, **params}
        status, body = http_get(f"{BASE}/{op}", p)
        raw = save_raw("kostat_online", f"{op}_{params.get('itemCode', 'all')}_{page}.xml", body)
        rows = parse_xml_items(body)
        yield p, status, rows, raw
        if len(rows) < 1000:
            break
        page += 1


def collect_items(con, key):
    n, ts = 0, now()
    for p, status, rows, raw in paged("getPriceItemList", {}, key, "serviceKey"):
        con.executemany(
            "INSERT OR REPLACE INTO online_item VALUES (?,?,?,?)",
            [(pick(r, "ic", "itemCode", "item_code"), pick(r, "in", "itemName", "item_nm"),
              json.dumps(r, ensure_ascii=False), ts) for r in rows],
        )
        log(con, SOURCE_ID, "getPriceItemList", p, status, "00", len(rows), raw)
        n += len(rows)
    con.commit()
    print(f"품목 {n}개")


def collect_prices(con, key, start, end, items):
    if not items:
        items = [r[0] for r in con.execute("SELECT item_code FROM online_item WHERE item_code IS NOT NULL")]
        if not items:
            collect_items(con, key)
            items = [r[0] for r in con.execute("SELECT item_code FROM online_item WHERE item_code IS NOT NULL")]
    total = 0
    for code in items:
        try:
            for p, status, rows, raw in paged("getPriceInfo", {"itemCode": code, "startDate": start, "endDate": end},
                                              key, "ServiceKey"):
                ts = now()
                con.executemany(
                    "INSERT OR REPLACE INTO online_price VALUES (?,?,?,?,?,?,?)",
                    [(code, pick(r, "sd", "priceDate", "date"),
                      pick(r, "pn", "productName", "goodsName"),
                      num(pick(r, "dp", "sp", "price")),  # dp=할인가, sp=정가 (raw에 둘 다 보존)
                      hashlib.sha1(json.dumps(r, sort_keys=True).encode()).hexdigest()[:16],
                      json.dumps(r, ensure_ascii=False), ts) for r in rows],
                )
                log(con, SOURCE_ID, "getPriceInfo", p, status, "00", len(rows), raw)
                total += len(rows)
        except ApiError as e:
            print(f"  품목 {code}: {e}")
            if "서비스키" in str(e):
                break
        con.commit()
    print(f"가격 {total}건 ({start}~{end}, 품목 {len(items)}개)")


def latest_day(key, probe_item="A01101", max_back=30) -> str:
    """데이터가 있는 가장 최근 날짜 (공개까지 약 10일 지연)."""
    d = dt.date.today()
    for _ in range(max_back):
        d -= dt.timedelta(days=1)
        ds = d.strftime("%Y%m%d")
        _, body = http_get(f"{BASE}/getPriceInfo", {"ServiceKey": key, "pageNo": 1, "numOfRows": 1,
                                                    "itemCode": probe_item, "startDate": ds, "endDate": ds})
        if b"<totalCount>" in body and b"<totalCount>0<" not in body:
            return ds
    raise ApiError(f"최근 {max_back}일 안에 데이터가 없습니다.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["items", "prices"])
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--days", type=int, default=1, help="end 포함 며칠치 (품목 전체 하루 ≈ 20만 건)")
    ap.add_argument("--item", action="append", help="품목코드 (여러 번 지정 가능)")
    a = ap.parse_args()
    key, con = load_key(), connect(SCHEMA)
    try:
        if a.cmd == "items":
            collect_items(con, key)
        else:
            end = a.end or latest_day(key)
            start = a.start or (dt.datetime.strptime(end, "%Y%m%d") - dt.timedelta(days=a.days - 1)).strftime("%Y%m%d")
            collect_prices(con, key, start, end, a.item)
    except ApiError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
