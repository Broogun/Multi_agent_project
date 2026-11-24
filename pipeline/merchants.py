"""가맹점 정규화(M06) 적용: txn.merchant_norm, category, tags 를 채운다.

    python -m pipeline.merchants --user u_demo        # 미분류 거래에 사전 적용 + 결과 요약

규칙 우선 (3-2 탭): 사전 → (다음 단계) LLM 제안 → 사용자 확인 후 사전에 반영.
가계부앱처럼 원본 분류(category_source='source')가 있으면 카테고리는 덮어쓰지 않고 브랜드만 채운다.
"""
from __future__ import annotations

import argparse
import functools
import json
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DICT_PATH = ROOT / "data_manual" / "merchants.yaml"


def norm_text(s: str) -> str:
    return re.sub(r"[\s()\[\]_\-·.*㈜,/&']|주식회사", "", (s or "")).lower()


@functools.lru_cache(maxsize=1)
def load_dict() -> tuple[str, list[dict]]:
    d = yaml.safe_load(DICT_PATH.read_text(encoding="utf-8"))
    entries = []
    for m in d["merchants"]:
        m = dict(m)
        m["_patterns"] = [norm_text(p) for p in m["patterns"]]
        entries.append(m)
    return str(d["version"]), entries


def match(merchant_raw: str) -> dict | None:
    key = norm_text(merchant_raw)
    if not key:
        return None
    for m in load_dict()[1]:
        for p in m["_patterns"]:
            # 짧은 영문 패턴(cu 등)은 단어 시작에서만 매칭해 오탐을 줄인다
            if len(p) <= 3 and p.isascii():
                if key.startswith(p):
                    return m
            elif p in key:
                return m
    return None


def ensure_columns(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(txn)")}
    if "tags" not in cols:
        con.execute("ALTER TABLE txn ADD COLUMN tags TEXT")          # JSON 배열
    if "dict_version" not in cols:
        con.execute("ALTER TABLE txn ADD COLUMN dict_version TEXT")


def apply(con, user_id: str, force: bool = False) -> dict:
    ensure_columns(con)
    version, _ = load_dict()
    where = "" if force else " AND (dict_version IS NULL OR dict_version != ?)"
    args = (user_id,) if force else (user_id, version)
    rows = con.execute(
        f"SELECT txn_id, merchant_raw, category_source FROM txn WHERE user_id=?"
        f" AND txn_type IN ('spend','refund'){where}", args).fetchall()
    matched = 0
    for txn_id, raw, cat_src in rows:
        m = match(raw)
        if m:
            matched += 1
            keep_cat = cat_src in ("source", "user")
            con.execute(
                "UPDATE txn SET merchant_norm=?, tags=?, dict_version=?"
                + ("" if keep_cat else ", category=?, category_source='rule'") + " WHERE txn_id=?",
                (m["brand"], json.dumps(m.get("tags", []), ensure_ascii=False), version)
                + (() if keep_cat else (m["category"],)) + (txn_id,))
        else:
            con.execute("UPDATE txn SET dict_version=? WHERE txn_id=?", (version, txn_id))
    con.commit()
    unmatched = con.execute(
        "SELECT merchant_raw, count(*) FROM txn WHERE user_id=? AND txn_type IN ('spend','refund')"
        " AND merchant_norm IS NULL GROUP BY 1 ORDER BY 2 DESC", (user_id,)).fetchall()
    return {"checked": len(rows), "matched": matched, "unmatched": unmatched, "version": version}


def main(argv=None):
    from . import ingest
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--force", action="store_true", help="이미 적용한 거래도 다시 적용")
    a = ap.parse_args(argv)
    con = ingest.connect()
    r = apply(con, a.user, a.force)
    print(f"사전 v{r['version']}: {r['checked']}건 검사, {r['matched']}건 매칭")
    if r["unmatched"]:
        print("미매칭 가맹점 (사전 추가 후보):")
        for m, n in r["unmatched"][:20]:
            print(f"  {m}  ({n}건)")


if __name__ == "__main__":
    main()
