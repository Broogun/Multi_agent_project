"""L2 수동 DB(data_manual/*.yaml) 검증 후 SQLite 적재.

    python collectors/manual_db.py           # 검증 + 적재
    python collectors/manual_db.py --check   # 검증만 (CI·PR 리뷰용)

검증 규칙:
  - id, source_url(키 존재), source_grade, as_of, review_by 필수
  - id 중복 금지
  - review_by 가 지났으면 경고 (신선도 기준 초과 → Tool은 "확인 필요" 반환)
  - source_grade 가 official 이 아니면 경고 (계산에 쓰기 전 공식 출처 확인 필요)
"""
import argparse
import datetime as dt
import json
import sys

import yaml

from common import ROOT, connect, now

MANUAL = ROOT / "data_manual"
REQUIRED = ["id", "source_grade", "as_of", "review_by"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_rule (
    id TEXT PRIMARY KEY, file TEXT, section TEXT, program TEXT,
    source_url TEXT, source_grade TEXT, as_of TEXT, review_by TEXT,
    valid_from TEXT, valid_to TEXT, status TEXT, body TEXT, loaded_at TEXT
);
CREATE VIEW IF NOT EXISTS v_manual_rule_active AS
SELECT *,
  CASE
    WHEN status = 'closed' OR (valid_to IS NOT NULL AND valid_to < date('now','localtime')) THEN 'inactive'
    WHEN review_by < date('now','localtime') THEN 'stale'
    WHEN source_grade != 'official' THEN 'needs_official_source'
    ELSE 'ok'
  END AS usable
FROM manual_rule;
"""


def load_all():
    entries, errors, warns = [], [], []
    today = dt.date.today()
    seen = set()
    for f in sorted(MANUAL.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for section, items in doc.items():
            for e in items or []:
                where = f"{f.name}:{section}:{e.get('id', '?')}"
                missing = [k for k in REQUIRED if not e.get(k)] + ([] if "source_url" in e else ["source_url"])
                if missing:
                    errors.append(f"{where} 필수 항목 누락: {missing}")
                    continue
                if e["id"] in seen:
                    errors.append(f"{where} id 중복")
                seen.add(e["id"])
                if dt.date.fromisoformat(str(e["review_by"])) < today:
                    warns.append(f"{where} 검토 만료일 지남 ({e['review_by']})")
                if e["source_grade"] != "official":
                    warns.append(f"{where} 공식 출처 아님 ({e['source_grade']})")
                entries.append((f.name, section, e))
    return entries, errors, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    entries, errors, warns = load_all()
    for w in warns:
        print("경고:", w)
    for e in errors:
        print("오류:", e)
    if errors:
        sys.exit(1)
    print(f"검증 통과: {len(entries)}개 규칙, 경고 {len(warns)}개")
    if a.check:
        return
    con = connect(SCHEMA)
    con.execute("DELETE FROM manual_rule")  # YAML이 원본이므로 매번 전체 재적재
    ts = now()
    con.executemany(
        "INSERT INTO manual_rule VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(e["id"], f, s, e.get("program"), e.get("source_url"), e["source_grade"],
          str(e["as_of"]), str(e["review_by"]),
          str(e["valid_from"]) if e.get("valid_from") else None,
          str(e["valid_to"]) if e.get("valid_to") else None,
          e.get("status"), json.dumps(e, ensure_ascii=False, default=str), ts)
         for f, s, e in entries],
    )
    con.commit()
    for row in con.execute("SELECT usable, count(*) FROM v_manual_rule_active GROUP BY usable"):
        print(f"  {row[0]}: {row[1]}")


if __name__ == "__main__":
    main()
