"""요청 시 API 호출 + 캐시 (read-through).

  캐시에 있고 신선함(TTL 이내) → 캐시 응답
  없거나 오래됨 → API 호출 → 원본 저장 → 응답
  API 실패 + 오래된 캐시 있음 → stale 표시와 함께 캐시 응답

TTL은 통합 문서 3-2 탭 신선도 기준 (envelope.SOURCES[...]["ttl_days"]).
같은 요청은 같은 캐시 행을 쓰므로 계산 재현성(같은 입력·같은 기준일 → 같은 결과)도 보장된다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))
import common  # noqa: E402

from .envelope import SOURCES  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cache (
    source_id TEXT, request_key TEXT, params TEXT, fetched_at TEXT, http_status INTEGER, body BLOB,
    PRIMARY KEY (source_id, request_key)
);
"""


def _key(url: str, params: dict) -> str:
    clean = {k: v for k, v in sorted(params.items()) if k.lower() != "servicekey"}
    return hashlib.sha1((url + json.dumps(clean, ensure_ascii=False)).encode()).hexdigest()


def cached_get(source_id: str, url: str, params: dict, key_name: str = "serviceKey",
               ttl_days: float | None = None, now: dt.datetime | None = None) -> tuple[bytes, str, bool]:
    """(body, fetched_at ISO, stale) 반환. 인증키는 캐시 키·로그에 남기지 않는다."""
    now = now or dt.datetime.now()
    ttl = ttl_days if ttl_days is not None else SOURCES.get(source_id, {}).get("ttl_days", 1)
    con = common.connect(SCHEMA)
    k = _key(url, params)
    row = con.execute("SELECT body, fetched_at FROM api_cache WHERE source_id=? AND request_key=?",
                      (source_id, k)).fetchone()
    if row and now - dt.datetime.fromisoformat(row[1]) <= dt.timedelta(days=ttl):
        return row[0], row[1], False
    try:
        status, body = common.http_get(url, {key_name: common.load_key(), **params})
        common.check_gateway_error(body)
    except Exception:
        if row:
            return row[0], row[1], True
        raise
    fetched = now.isoformat(timespec="seconds")
    con.execute("INSERT OR REPLACE INTO api_cache VALUES (?,?,?,?,?,?)",
                (source_id, k, json.dumps({kk: v for kk, v in params.items()}, ensure_ascii=False), fetched, status, body))
    common.log(con, source_id, url.rsplit("/", 1)[-1], params, status, "00", None, None)
    con.commit()
    return body, fetched, False


def prices_db() -> sqlite3.Connection:
    return common.connect()
