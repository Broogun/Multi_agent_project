"""한국소비자원 생필품 가격 정보 수집기 (data.go.kr 15158701, 공공누리 제1유형 / 출처: 한국소비자원).

사용법:
    python collectors/kca_price.py master            # 판매점·상품·기준코드 갱신
    python collectors/kca_price.py prices            # 데이터가 있는 가장 최근 조사일 가격 수집
    python collectors/kca_price.py prices --day 20261106
    python collectors/kca_price.py all               # master + prices
    python collectors/kca_price.py backfill --weeks 26   # 과거 조사일 일괄 수집 (이미 받은 날 건너뜀)

설계 (3-2 탭 기준):
- 원본 응답은 data/raw/kca/<종류>/<날짜>/ 에 gzip으로 그대로 보관
- 정규화 결과는 data/prices.db (SQLite)
- 모든 호출은 fetch_log 에 기록 (출처·기준일 역추적용)
- 이미 수집한 (조사일, 상품)은 건너뛰므로 중단 후 다시 실행해도 이어서 수집
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import os
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw" / "kca"
DB_PATH = DATA / "prices.db"
BASE = "https://apis.data.go.kr/B551919/ProductPriceInfoService"
SOURCE_ID = "P02_KCA_PRICE"  # 3-1 탭 데이터 소스 ID
STD_CLASSES = {"AR": "지역", "AL": "품목분류", "UT": "단위"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS kca_store (
    entp_id TEXT PRIMARY KEY, entp_name TEXT, entp_type_code TEXT,
    area_code TEXT, area_detail_code TEXT, tel TEXT, post_no TEXT,
    addr_jibun TEXT, addr_road TEXT, x TEXT, y TEXT, raw TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS kca_product (
    good_id TEXT PRIMARY KEY, good_name TEXT, product_entp_code TEXT,
    unit_div_code TEXT, base_cnt REAL, smlcls_code TEXT,
    total_cnt REAL, total_div_code TEXT, raw TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS kca_code (
    class_code TEXT, code TEXT, code_name TEXT, high_code TEXT, updated_at TEXT,
    PRIMARY KEY (class_code, code)
);
CREATE TABLE IF NOT EXISTS kca_price (
    inspect_day TEXT, entp_id TEXT, good_id TEXT, price INTEGER,
    plus_one_yn TEXT, discount_yn TEXT, discount_start TEXT, discount_end TEXT,
    input_dttm TEXT, fetched_at TEXT,
    PRIMARY KEY (inspect_day, entp_id, good_id)
);
CREATE TABLE IF NOT EXISTS kca_price_done (   -- (조사일, 상품) 수집 완료 표시 (0건 포함)
    inspect_day TEXT, good_id TEXT, rows INTEGER, fetched_at TEXT,
    PRIMARY KEY (inspect_day, good_id)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, operation TEXT,
    params TEXT, http_status INTEGER, result_code TEXT, rows INTEGER,
    raw_path TEXT, fetched_at TEXT
);
CREATE VIEW IF NOT EXISTS v_kca_price AS
SELECT p.inspect_day, p.good_id, g.good_name, p.entp_id, s.entp_name, s.entp_type_code,
       s.area_code, a.code_name AS area_name, p.price, g.total_cnt, g.total_div_code,
       g.base_cnt, g.unit_div_code,
       CASE WHEN g.total_cnt > 0 AND g.base_cnt > 0
            THEN ROUND(p.price * g.base_cnt / g.total_cnt, 1) END AS unit_price,  -- 기준량(base_cnt)당 가격
       p.plus_one_yn, p.discount_yn, p.input_dttm, '한국소비자원' AS source
FROM kca_price p
LEFT JOIN kca_product g ON g.good_id = p.good_id
LEFT JOIN kca_store s ON s.entp_id = p.entp_id
LEFT JOIN kca_code a ON a.class_code = 'AR' AND a.code = s.area_code;
"""


# ---------------------------------------------------------------- 공통
def load_key() -> str:
    key = os.environ.get("DATA_GO_KR_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("DATA_GO_KR_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("DATA_GO_KR_KEY 를 .env 또는 환경변수에 넣어주세요.")
    return urllib.parse.unquote(key) if "%" in key else key  # Encoding 키도 허용


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=60)
    con.executescript(SCHEMA)
    return con


def call(op: str, params: dict, key: str, key_name: str = "ServiceKey", retries: int = 3) -> tuple[int, bytes]:
    url = f"{BASE}/{op}?" + urllib.parse.urlencode({key_name: key, **params})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code < 500 and b"LIMITED_NUMBER_OF_SERVICE_REQUESTS" not in body:
                return e.code, body
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{op} 호출 실패: {params}")


def parse(body: bytes) -> tuple[str, list[dict]]:
    root = ET.fromstring(body)
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or ""
    if root.tag == "OpenAPI_ServiceResponse":
        raise RuntimeError(f"API 오류: {root.findtext('.//errMsg')} / {root.findtext('.//returnAuthMsg')}")
    result = root.find("result")
    rows = [{c.tag: (c.text or "").strip() for c in item} for item in (result if result is not None else [])]
    return code, rows


def save_raw(kind: str, name: str, body: bytes) -> str:
    d = RAW / kind / dt.date.today().strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.xml.gz"
    p.write_bytes(gzip.compress(body))
    return str(p.relative_to(ROOT))


def log(con, op, params, status, code, rows, raw_path):
    con.execute(
        "INSERT INTO fetch_log(source_id, operation, params, http_status, result_code, rows, raw_path, fetched_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (SOURCE_ID, op, urllib.parse.urlencode(params), status, code, rows, raw_path, now()),
    )


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 기준 데이터
def collect_master(con, key):
    ts = now()
    status, body = call("getStoreInfoSvc.do", {}, key)
    code, rows = parse(body)
    log(con, "getStoreInfoSvc.do", {}, status, code, len(rows), save_raw("store", "all", body))
    con.executemany(
        "INSERT OR REPLACE INTO kca_store VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r.get("entpId"), r.get("entpName"), r.get("entpTypeCode"), r.get("entpAreaCode"),
          r.get("areaDetailCode"), r.get("entpTelno"), r.get("postNo"),
          " ".join(filter(None, [r.get("plmkAddrBasic"), r.get("plmkAddrDetail")])),
          " ".join(filter(None, [r.get("roadAddrBasic"), r.get("roadAddrDetail")])),
          r.get("xMapCoord"), r.get("yMapCoord"), repr(r), ts) for r in rows],
    )
    print(f"판매점 {len(rows)}곳")

    status, body = call("getProductInfoSvc.do", {}, key)
    code, rows = parse(body)
    log(con, "getProductInfoSvc.do", {}, status, code, len(rows), save_raw("product", "all", body))
    con.executemany(
        "INSERT OR REPLACE INTO kca_product VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(r.get("goodId"), r.get("goodName"), r.get("productEntpCode"), r.get("goodUnitDivCode"),
          num(r.get("goodBaseCnt")), r.get("goodSmlclsCode"), num(r.get("goodTotalCnt")),
          r.get("goodTotalDivCode"), repr(r), ts) for r in rows],
    )
    print(f"상품 {len(rows)}개")

    for cls, label in STD_CLASSES.items():
        params = {"classCode": cls}
        status, body = call("getStandardInfoSvc.do", params, key)
        code, rows = parse(body)
        log(con, "getStandardInfoSvc.do", params, status, code, len(rows), save_raw("code", cls, body))
        con.executemany(
            "INSERT OR REPLACE INTO kca_code VALUES (?,?,?,?,?)",
            [(cls, r.get("code"), r.get("codeName"), r.get("highCode"), ts) for r in rows],
        )
        print(f"기준코드 {cls}({label}) {len(rows)}개")
    con.commit()


# ---------------------------------------------------------------- 가격
def fetch_price(key, day, good_id):
    params = {"goodInspectDay": day, "goodId": good_id}
    status, body = call("getProductPriceInfoSvc", params, key, key_name="serviceKey")
    code, rows = parse(body)
    return params, status, code, rows, body


def fridays_back(n: int, start: dt.date | None = None):
    d = start or dt.date.today()
    d -= dt.timedelta(days=(d.weekday() - 4) % 7)
    for i in range(n):
        yield (d - dt.timedelta(weeks=i)).strftime("%Y%m%d")


def latest_day(con, key, probe_goods: int = 5, weeks: int = 8) -> str:
    """데이터가 있는 가장 최근 금요일. 상품 몇 개로 확인한다."""
    goods = [r[0] for r in con.execute("SELECT good_id FROM kca_product ORDER BY CAST(good_id AS INT) LIMIT ?", (probe_goods,))]
    for day in fridays_back(weeks):
        for g in goods:
            *_, rows, _ = fetch_price(key, day, g)
            if rows:
                return day
    sys.exit(f"최근 {weeks}주 금요일에 가격 데이터가 없습니다.")


def collect_prices(con, key, day: str | None, workers: int, limit: int | None):
    if not con.execute("SELECT 1 FROM kca_product LIMIT 1").fetchone():
        collect_master(con, key)
    day = day or latest_day(con, key)
    if dt.datetime.strptime(day, "%Y%m%d").weekday() != 4:
        print(f"주의: {day} 는 금요일이 아닙니다 (조사일은 금요일).")
    goods = [r[0] for r in con.execute(
        "SELECT good_id FROM kca_product WHERE good_id NOT IN "
        "(SELECT good_id FROM kca_price_done WHERE inspect_day = ?) ORDER BY CAST(good_id AS INT)", (day,))]
    if limit:
        goods = goods[:limit]
    print(f"조사일 {day}: 남은 상품 {len(goods)}개 수집 (동시 {workers})")

    total = done = 0
    raw_dir_day = f"price_{day}"
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_price, key, day, g): g for g in goods}
        for f in as_completed(futs):
            g = futs[f]
            try:
                params, status, code, rows, body = f.result()
            except Exception as e:  # 한 상품 실패가 전체를 멈추지 않게
                print(f"  상품 {g} 실패: {e}")
                continue
            raw = save_raw(raw_dir_day, g, body) if rows else None
            ts = now()
            con.executemany(
                "INSERT OR REPLACE INTO kca_price VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(r.get("goodInspectDay"), r.get("entpId"), r.get("goodId"),
                  int(float(r["goodPrice"])) if r.get("goodPrice") else None,
                  r.get("plusoneYn"), r.get("goodDcYn"), r.get("goodDcStartDay"), r.get("goodDcEndDay"),
                  r.get("inputDttm"), ts) for r in rows],
            )
            con.execute("INSERT OR REPLACE INTO kca_price_done VALUES (?,?,?,?)", (day, g, len(rows), ts))
            log(con, "getProductPriceInfoSvc", params, status, code, len(rows), raw)
            total += len(rows)
            done += 1
            if done % 50 == 0:
                con.commit()
                print(f"  {done}/{len(goods)} 상품, 가격 {total}건")
    con.commit()
    print(f"완료: 상품 {done}개, 가격 {total}건 → {DB_PATH.relative_to(ROOT)}")


def has_data(key, day, goods) -> bool:
    for g in goods:
        *_, rows, _ = fetch_price(key, day, g)
        if rows:
            return True
    return False


def backfill(con, key, weeks: int, workers: int):
    """최근 N주 금요일을 거슬러 올라가며 데이터가 있는 조사일만 수집. 이미 끝난 날은 건너뜀."""
    if not con.execute("SELECT 1 FROM kca_product LIMIT 1").fetchone():
        collect_master(con, key)
    n_goods = con.execute("SELECT count(*) FROM kca_product").fetchone()[0]
    probe = [r[0] for r in con.execute(
        "SELECT good_id FROM kca_price GROUP BY good_id ORDER BY count(*) DESC LIMIT 3")] or ["35"]
    for day in fridays_back(weeks):
        done = con.execute("SELECT count(*) FROM kca_price_done WHERE inspect_day=?", (day,)).fetchone()[0]
        if done >= n_goods:
            print(f"{day}: 이미 수집됨")
            continue
        if not done and not has_data(key, day, probe):
            print(f"{day}: 데이터 없음")
            continue
        collect_prices(con, key, day, workers, None)


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["master", "prices", "all", "backfill"])
    ap.add_argument("--weeks", type=int, default=26, help="backfill: 거슬러 올라갈 주 수")
    ap.add_argument("--day", help="조사일 YYYYMMDD (금요일). 생략 시 데이터가 있는 가장 최근 금요일")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="상품 수 제한 (테스트용)")
    a = ap.parse_args()

    key = load_key()
    con = connect()
    if a.cmd in ("master", "all"):
        collect_master(con, key)
    if a.cmd in ("prices", "all"):
        collect_prices(con, key, a.day, a.workers, a.limit)
    if a.cmd == "backfill":
        backfill(con, key, a.weeks, a.workers)


if __name__ == "__main__":
    main()
