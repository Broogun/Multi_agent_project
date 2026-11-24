"""P09 aT 가격 추이 정보 (data.go.kr 15156069, 한국농수산식품유통공사). 단골 농산물 가격 변화용.

    python collectors/at_price_trend.py                  # 어제 조사일
    python collectors/at_price_trend.py --day 20261120

상태: 경로(/info)는 게이트웨이 응답으로 존재 확인. 응답 필드명은 승인 후 검증 필요.
원본 행 전체를 raw(JSON)로 저장한다.
"""
import argparse
import datetime as dt
import json

from common import connect, load_key, http_get, parse_json_items, save_raw, log, now, ApiError

SOURCE_ID = "P09_AT_TREND"
URL = "https://apis.data.go.kr/B552845/priceSequel/info"

SCHEMA = """
CREATE TABLE IF NOT EXISTS at_price_trend (
    exmn_ymd TEXT, se_cd TEXT, ctgry_cd TEXT, item_cd TEXT, vrty_cd TEXT, grd_cd TEXT,
    item_nm TEXT, raw TEXT, fetched_at TEXT,
    PRIMARY KEY (exmn_ymd, se_cd, ctgry_cd, item_cd, vrty_cd, grd_cd)
);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=(dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d"))
    a = ap.parse_args()
    key, con = load_key(), connect(SCHEMA)
    page, total = 1, 0
    while True:
        p = {"serviceKey": key, "pageNo": page, "numOfRows": 1000,
             "cond[exmn_ymd::EQ]": a.day, "returnType": "JSON"}
        status, body = http_get(URL, p)
        raw = save_raw("at_trend", f"{a.day}_{page}.json", body)
        try:
            rows, count = parse_json_items(body)
        except ApiError as e:
            log(con, SOURCE_ID, "info", p, status, "ERR", 0, raw)
            con.commit()
            raise SystemExit(str(e))
        ts = now()
        con.executemany(
            "INSERT OR REPLACE INTO at_price_trend VALUES (?,?,?,?,?,?,?,?,?)",
            [(r.get("exmn_ymd", a.day), r.get("se_cd"), r.get("ctgry_cd"), r.get("item_cd"),
              r.get("vrty_cd"), r.get("grd_cd"), r.get("item_nm"),
              json.dumps(r, ensure_ascii=False), ts) for r in rows],
        )
        log(con, SOURCE_ID, "info", p, status, "00", len(rows), raw)
        total += len(rows)
        if len(rows) < 1000 or (count is not None and total >= count):
            break
        page += 1
    con.commit()
    print(f"aT 가격추이 {a.day}: {total}건")


if __name__ == "__main__":
    main()
