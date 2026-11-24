"""수집기 공통: 키 로딩, HTTP 호출, 원본 보관, DB 연결, 호출 기록."""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
DB_PATH = DATA / "prices.db"

_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, operation TEXT,
    params TEXT, http_status INTEGER, result_code TEXT, rows INTEGER,
    raw_path TEXT, fetched_at TEXT
);
"""


class ApiError(RuntimeError):
    pass


def load_key(name: str = "DATA_GO_KR_KEY") -> str:
    key = os.environ.get(name)
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith(name + "="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit(f"{name} 를 .env 또는 환경변수에 넣어주세요.")
    return urllib.parse.unquote(key) if "%" in key else key  # Encoding 키도 허용


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def connect(schema: str = "") -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(_LOG_SCHEMA + schema)
    return con


def http_get(url: str, params: dict, retries: int = 3) -> tuple[int, bytes]:
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(full, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code < 500 and b"LIMITED_NUMBER_OF_SERVICE_REQUESTS" not in body:
                return e.code, body
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(2 ** attempt)
    raise ApiError(f"호출 실패: {url}")


def check_gateway_error(body: bytes):
    """data.go.kr 게이트웨이 오류(키 미등록, 경로 없음 등)를 사람이 읽을 수 있게 올린다."""
    text = body.decode("utf-8", "replace")
    if "OpenAPI_ServiceResponse" in text or "<successYN>N" in text:
        import re
        msg = re.findall(r'<(?:errMsg|returnAuthMsg)>([^<]+)<', text) + \
            re.findall(r'"(?:errMsg|returnAuthMsg)"\s*:\s*"([^"]+)"', text)
        raise ApiError("API 오류: " + " / ".join(dict.fromkeys(m.strip() for m in msg)))


def parse_xml_items(body: bytes, item_tag: str = "item") -> list[dict]:
    check_gateway_error(body)
    root = ET.fromstring(body)
    return [{c.tag: (c.text or "").strip() for c in el} for el in root.iter(item_tag)]


def parse_json_items(body: bytes) -> tuple[list[dict], int | None]:
    """data.go.kr 표준 JSON(response.body.items.item, totalCount) 형태."""
    check_gateway_error(body)
    d = json.loads(body)
    b = d.get("response", d).get("body", d)
    items = b.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    total = b.get("totalCount")
    return items, int(total) if total not in (None, "") else None


def save_raw(source: str, name: str, body: bytes) -> str:
    d = RAW / source / dt.date.today().strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.gz"
    p.write_bytes(gzip.compress(body))
    return str(p.relative_to(ROOT))


def log(con, source_id, op, params, status, code, rows, raw_path):
    params = {k: v for k, v in params.items() if k.lower() != "servicekey"}
    con.execute(
        "INSERT INTO fetch_log(source_id, operation, params, http_status, result_code, rows, raw_path, fetched_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (source_id, op, urllib.parse.urlencode(params), status, code, rows, raw_path, now()),
    )


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
