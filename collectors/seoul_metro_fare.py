"""P10 서울교통공사 실시간 운임 (data.go.kr 15143846). 구간 운임 확인용 (후순위).

    python collectors/seoul_metro_fare.py --from 서울역 --to 시청
    python collectors/seoul_metro_fare.py --all      # 전체 구간 (양이 많을 수 있음)

상태: 기본 주소(B553766/fare2)는 명세에서 확인했지만 오퍼레이션 경로는 아직 미확인.
활용신청 승인 후 data.go.kr 페이지의 '상세기능'에서 경로를 확인해 OPERATION 을 채운다.
"""
import argparse
import json

from common import connect, load_key, http_get, parse_json_items, save_raw, log, now, ApiError

SOURCE_ID = "P10_SEOUL_METRO_FARE"
BASE = "https://apis.data.go.kr/B553766/fare2"
OPERATION = None  # TODO: 승인 후 확인 (예: "getFare..."). None 이면 실행 시 안내 후 종료

SCHEMA = """
CREATE TABLE IF NOT EXISTS metro_fare (
    dptre_stn_cd TEXT, dptre_stn_nm TEXT, arvl_stn_cd TEXT, arvl_stn_nm TEXT,
    gnrl_card_fare REAL, gnrl_cash_fare REAL, yung_card_fare REAL,
    raw TEXT, fetched_at TEXT,
    PRIMARY KEY (dptre_stn_cd, arvl_stn_cd)
);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src")
    ap.add_argument("--to", dest="dst")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if OPERATION is None:
        raise SystemExit("오퍼레이션 경로 미확인: 활용신청 승인 후 OPERATION 값을 채워야 합니다.")
    if not (a.all or a.src or a.dst):
        raise SystemExit("--from/--to 또는 --all 을 지정하세요.")
    key, con = load_key(), connect(SCHEMA)
    page, total = 1, 0
    while True:
        p = {"serviceKey": key, "pageNo": page, "numOfRows": 1000, "dataType": "JSON"}
        if a.src:
            p["dptreStnNm"] = a.src
        if a.dst:
            p["arvlStnNm"] = a.dst
        status, body = http_get(f"{BASE}/{OPERATION}", p)
        raw = save_raw("metro_fare", f"{page}.json", body)
        try:
            rows, count = parse_json_items(body)
        except ApiError as e:
            raise SystemExit(str(e))
        con.executemany(
            "INSERT OR REPLACE INTO metro_fare VALUES (?,?,?,?,?,?,?,?,?)",
            [(r.get("dptreStnCd"), r.get("dptreStnNm"), r.get("arvlStnCd"), r.get("arvlStnNm"),
              r.get("gnrlCardFare"), r.get("gnrlCashFare"), r.get("yungCardFare"),
              json.dumps(r, ensure_ascii=False), now()) for r in rows],
        )
        log(con, SOURCE_ID, OPERATION, p, status, "00", len(rows), raw)
        total += len(rows)
        if len(rows) < 1000 or (count is not None and total >= count):
            break
        page += 1
    con.commit()
    print(f"운임 {total}건")


if __name__ == "__main__":
    main()
