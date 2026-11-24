"""거래내역 CSV 수집·정규화 파이프라인 (통합 문서 3-2 탭 "거래내역 처리 규칙").

    python -m pipeline.ingest data/csv/card_a.csv data/csv/bank.csv --user u_demo --self-name 홍길동
    python -m pipeline.ingest report --user u_demo

단계: 업로드(인코딩 판별) → 포맷 인식 → 정규화 → 이중 계산 방지 → 중복 처리 → 품질 리포트
저장: data/user.db (사용자 데이터 S1. 공공 데이터 DB와 분리)

거래 유형(txn_type):
  spend           지출 (합계에 포함)
  refund          환불·승인취소 (음수, 합계에서 차감)
  income          수입
  transfer        이체 (본인 계좌 간 포함, 지출 합계 제외)
  card_settlement 은행의 카드대금 출금 (카드 거래와 이중 계산 → 지출 합계 제외)

중복 처리:
  - 원본 행이 완전히 같으면(같은 포맷, 같은 값) 확정 중복 → 저장하지 않음 (같은 파일 재업로드 대응)
  - 승인번호가 같고 취소 여부도 같으면 확정 중복
  - 원천 ID 없이 (날짜, 금액, 가맹점)만 같으면 '중복 후보'로 표시만 하고 둘 다 보존
    (같은 날 같은 가게에서 두 번 산 정상 거래 보호. 사용자가 확인)
TODO: 원본 파일 암호화 보관 (MVP는 로컬 저장, 배포 전 적용)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import pathlib
import re
import sqlite3
import sys

from .formats import FORMATS, CARD_SETTLEMENT_PATTERNS, TRANSFER_PATTERNS

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "user.db"
ENCODINGS = ["utf-8-sig", "cp949", "euc-kr"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS upload (
    upload_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, file_name TEXT,
    file_sha1 TEXT, encoding TEXT, format_id TEXT, rows_total INTEGER,
    rows_inserted INTEGER, rows_duplicate INTEGER, rows_failed INTEGER, uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS txn (
    txn_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL, upload_id INTEGER, source_kind TEXT, format_id TEXT, source_row INTEGER,
    occurred_at TEXT NOT NULL,          -- ISO 8601 (시간 없으면 날짜만)
    occurred_date TEXT NOT NULL,
    amount INTEGER NOT NULL,            -- 원 단위 정수. 환불은 음수
    txn_type TEXT NOT NULL,             -- spend / refund / income / transfer / card_settlement
    merchant_raw TEXT,
    merchant_norm TEXT,                 -- 가맹점 정규화 사전(M06) 적용 결과 (다음 단계)
    category TEXT,                      -- 카테고리 (다음 단계, 가계부앱은 원본 분류 보존)
    category_source TEXT,               -- source / rule / llm / user
    approval_no TEXT,
    installment_months INTEGER,         -- 1 = 일시불
    linked_txn_id INTEGER,              -- 환불 → 원거래
    dup_candidate_of INTEGER,           -- 중복 후보로 표시된 상대 거래
    dup_status TEXT,                    -- NULL / candidate / confirmed_unique / confirmed_dup (사용자 확인)
    memo TEXT,
    row_fingerprint TEXT,
    raw TEXT,
    UNIQUE (user_id, row_fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_txn_user_date ON txn(user_id, occurred_date);
CREATE INDEX IF NOT EXISTS ix_txn_approval ON txn(user_id, approval_no);

-- 지출 합계용 뷰: 이중 계산 대상과 사용자가 중복으로 확정한 거래를 뺀다
CREATE VIEW IF NOT EXISTS v_spending AS
SELECT * FROM txn
WHERE txn_type IN ('spend', 'refund') AND COALESCE(dup_status, '') != 'confirmed_dup';
"""


class FormatError(Exception):
    pass


# ------------------------------------------------------------------ 읽기·인식
def read_csv(path: pathlib.Path) -> tuple[str, list[str], list[dict]]:
    data = path.read_bytes()
    last = None
    for enc in ENCODINGS:
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError as e:
            last = e
    else:
        raise FormatError(f"인코딩을 판별할 수 없습니다 ({', '.join(ENCODINGS)} 시도): {last}")
    rows = list(csv.reader(io.StringIO(text)))
    # 카드사 파일은 위쪽에 안내 문구가 있을 수 있어 헤더 행을 찾는다
    for i, r in enumerate(rows[:20]):
        cells = [c.strip() for c in r]
        if detect_format(cells):
            header = cells
            body = [dict(zip(header, (c.strip() for c in rr))) for rr in rows[i + 1:] if any(c.strip() for c in rr)]
            return enc, header, body
    raise FormatError(
        "알 수 없는 파일 형식입니다. 첫 행 컬럼: " + ", ".join(rows[0] if rows else [])
        + "\n→ pipeline/formats.py 에 템플릿을 추가하거나 컬럼 지정이 필요합니다."
    )


def detect_format(header: list[str]) -> dict | None:
    hs = set(header)
    for f in FORMATS:
        if all(c in hs for c in f["required"]):
            return f
    return None


# ------------------------------------------------------------------ 정규화
def parse_amount(v: str | None) -> int | None:
    if v is None:
        return None
    s = re.sub(r"[,\s원₩]", "", str(v))
    if s in ("", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = int(round(float(s)))
    except ValueError:
        raise FormatError(f"금액 형식 오류: {v!r}")
    return -n if neg else n


def parse_datetime(v: str, fmts: list[str]) -> tuple[str, str]:
    v = v.strip()
    for f in fmts:
        try:
            d = dt.datetime.strptime(v, f)
            has_time = "%H" in f
            return (d.isoformat(timespec="seconds") if has_time else d.date().isoformat()), d.date().isoformat()
        except ValueError:
            continue
    raise FormatError(f"날짜 형식 오류: {v!r}")


def parse_installment(v: str | None) -> int:
    if not v:
        return 1
    if "일시불" in v:
        return 1
    m = re.search(r"(\d+)", v)
    return max(1, int(m.group(1))) if m else 1


def classify_bank(merchant: str, memo: str, out_amt: int, in_amt: int, self_names: list[str]) -> str:
    text = f"{merchant} {memo}"
    if out_amt and any(p in text for p in CARD_SETTLEMENT_PATTERNS):
        return "card_settlement"
    if out_amt and "자동이체" in text and not any(n and n in text for n in self_names):
        return "spend"  # 월세·통신비·공과금 자동이체는 지출 ('이체' 패턴보다 먼저 판단)
    if any(n and n in text for n in self_names):
        return "transfer"  # 본인 계좌 간 이체
    if in_amt:
        return "transfer" if any(p in text for p in TRANSFER_PATTERNS) and "급여" not in text else "income"
    if any(p in text for p in TRANSFER_PATTERNS):
        return "transfer"
    return "spend"  # 자동이체 공과금·통신비 등 계좌에서 직접 나간 지출


def normalize_row(fmt: dict, row: dict, self_names: list[str]) -> dict:
    c = fmt["columns"]
    get = lambda k: row.get(c[k]) if k in c else None
    if "datetime" in c:
        occurred_at, day = parse_datetime(get("datetime"), fmt["date_formats"])
    else:
        occurred_at, day = parse_datetime(get("date"), fmt["date_formats"])
        if get("time"):
            try:
                t = dt.datetime.strptime(get("time").strip(), "%H:%M").time()
                occurred_at = dt.datetime.combine(dt.date.fromisoformat(day), t).isoformat(timespec="seconds")
            except ValueError:
                pass
    merchant = (get("merchant") or "").strip()
    out = {
        "occurred_at": occurred_at, "occurred_date": day, "merchant_raw": merchant,
        "approval_no": (get("approval_no") or "").strip() or None,
        "installment_months": parse_installment(get("installment")),
        "memo": (get("memo") or "").strip() or None, "category": None, "category_source": None,
    }
    kind = fmt["kind"]
    if kind == "card":
        amt = parse_amount(get("amount"))
        cancelled = (get("cancel") or "").strip() in fmt.get("cancel_values", [])
        if cancelled or amt < 0:
            out.update(amount=-abs(amt), txn_type="refund")
        else:
            out.update(amount=amt, txn_type="spend")
    elif kind == "bank":
        o, i = parse_amount(get("out")) or 0, parse_amount(get("in")) or 0
        t = classify_bank(merchant, out["memo"] or "", o, i, self_names)
        out.update(amount=o if o else i, txn_type=t)
    elif kind == "ledger":
        amt = parse_amount(get("amount"))
        t = fmt["type_map"].get((get("type") or "").strip(), "spend")
        if t == "spend":
            # 가계부앱은 지출을 음수로 내보낸다. 지출 타입인데 양수면 환불로 본다
            out.update(amount=abs(amt), txn_type="spend") if amt <= 0 else out.update(amount=-amt, txn_type="refund")
        else:
            out.update(amount=abs(amt), txn_type=t)
        if get("category"):
            out.update(category=get("category").strip(), category_source="source")
    out["installment_months"] = out["installment_months"] if kind == "card" else 1
    return out


def fingerprint(fmt_id: str, row: dict) -> str:
    payload = fmt_id + "|" + json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def merchant_key(m: str) -> str:
    return re.sub(r"[\s()\[\]_\-·.*㈜]|주식회사", "", m or "").lower()


# ------------------------------------------------------------------ 적재
def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def ingest_file(con, path: pathlib.Path, user_id: str, self_names: list[str]) -> dict:
    enc, header, rows = read_csv(path)
    fmt = detect_format(header)
    now = dt.datetime.now().isoformat(timespec="seconds")
    cur = con.execute(
        "INSERT INTO upload(user_id, file_name, file_sha1, encoding, format_id, rows_total, uploaded_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (user_id, path.name, hashlib.sha1(path.read_bytes()).hexdigest(), enc, fmt["id"], len(rows), now),
    )
    upload_id = cur.lastrowid
    inserted = dup = 0
    failed = []
    for idx, raw in enumerate(rows, start=1):
        try:
            n = normalize_row(fmt, raw, self_names)
        except (FormatError, TypeError, ValueError) as e:
            failed.append((idx, str(e)))
            continue
        fp = fingerprint(fmt["id"], raw)
        if con.execute("SELECT 1 FROM txn WHERE user_id=? AND row_fingerprint=?", (user_id, fp)).fetchone():
            dup += 1
            continue
        if n["approval_no"] and con.execute(
            "SELECT 1 FROM txn WHERE user_id=? AND approval_no=? AND txn_type=? AND amount=?",
            (user_id, n["approval_no"], n["txn_type"], n["amount"]),
        ).fetchone():
            dup += 1
            continue
        con.execute(
            "INSERT INTO txn(user_id, upload_id, source_kind, format_id, source_row, occurred_at, occurred_date,"
            " amount, txn_type, merchant_raw, category, category_source, approval_no, installment_months,"
            " memo, row_fingerprint, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, upload_id, fmt["kind"], fmt["id"], idx, n["occurred_at"], n["occurred_date"],
             n["amount"], n["txn_type"], n["merchant_raw"], n["category"], n["category_source"],
             n["approval_no"], n["installment_months"], n["memo"], fp, json.dumps(raw, ensure_ascii=False)),
        )
        inserted += 1
    con.execute("UPDATE upload SET rows_inserted=?, rows_duplicate=?, rows_failed=? WHERE upload_id=?",
                (inserted, dup, len(failed), upload_id))
    link_refunds(con, user_id)
    mark_dup_candidates(con, user_id)
    con.commit()
    return {"file": path.name, "format": fmt["name"], "encoding": enc, "rows": len(rows),
            "inserted": inserted, "duplicate": dup, "failed": failed}


def link_refunds(con, user_id):
    """환불을 원거래와 연결: 같은 승인번호 → 없으면 같은 가맹점·같은 금액의 이전 지출(60일 이내)."""
    for r in con.execute("SELECT * FROM txn WHERE user_id=? AND txn_type='refund' AND linked_txn_id IS NULL",
                         (user_id,)).fetchall():
        orig = None
        if r["approval_no"]:
            orig = con.execute("SELECT txn_id FROM txn WHERE user_id=? AND approval_no=? AND txn_type='spend'",
                               (user_id, r["approval_no"])).fetchone()
        if not orig:
            orig = con.execute(
                "SELECT txn_id FROM txn WHERE user_id=? AND txn_type='spend' AND amount=? AND merchant_raw=?"
                " AND occurred_date <= ? AND occurred_date >= date(?, '-60 day') ORDER BY occurred_date DESC LIMIT 1",
                (user_id, -r["amount"], r["merchant_raw"], r["occurred_date"], r["occurred_date"])).fetchone()
        if orig:
            con.execute("UPDATE txn SET linked_txn_id=? WHERE txn_id=?", (orig[0], r["txn_id"]))


def mark_dup_candidates(con, user_id):
    """원천 ID로 구분되지 않는 (날짜, 금액, 가맹점) 일치 건을 중복 후보로 표시. 삭제하지 않는다.
    같은 승인번호가 서로 다른 거래(둘 다 승인번호 있음)는 별개 거래이므로 제외."""
    rows = con.execute(
        "SELECT txn_id, occurred_date, amount, merchant_raw, approval_no FROM txn"
        " WHERE user_id=? AND txn_type IN ('spend','refund') AND dup_status IS NULL ORDER BY txn_id", (user_id,)
    ).fetchall()
    seen: dict[tuple, sqlite3.Row] = {}
    for r in rows:
        k = (r["occurred_date"], r["amount"], merchant_key(r["merchant_raw"]))
        prev = seen.get(k)
        if prev is not None and not (prev["approval_no"] and r["approval_no"]):
            con.execute("UPDATE txn SET dup_candidate_of=?, dup_status='candidate' WHERE txn_id=?",
                        (prev["txn_id"], r["txn_id"]))
        else:
            seen[k] = r


# ------------------------------------------------------------------ 품질 리포트
def report(con, user_id) -> dict:
    q = lambda s, *a: con.execute(s, (user_id, *a)).fetchone()
    period = q("SELECT min(occurred_date), max(occurred_date), count(*) FROM txn WHERE user_id=?")
    by_type = dict(con.execute("SELECT txn_type, count(*) FROM txn WHERE user_id=? GROUP BY 1", (user_id,)).fetchall())
    spend = q("SELECT COALESCE(sum(amount),0) FROM v_spending WHERE user_id=? AND COALESCE(dup_status,'') != 'candidate'")[0]
    cand_amt = q("SELECT COALESCE(sum(amount),0) FROM v_spending WHERE user_id=? AND dup_status='candidate'")[0]
    uncategorized = q("SELECT count(*) FROM txn WHERE user_id=? AND txn_type='spend' AND category IS NULL")[0]
    spend_cnt = by_type.get("spend", 0) or 1
    return {
        "period": f"{period[0]} ~ {period[1]}", "transactions": period[2], "by_type": by_type,
        "net_spending": spend, "dup_candidate_amount": cand_amt,
        "excluded_card_settlement": q("SELECT COALESCE(sum(amount),0) FROM txn WHERE user_id=? AND txn_type='card_settlement'")[0],
        "uncategorized_ratio": round(uncategorized / spend_cnt, 3),
        "dup_candidates": q("SELECT count(*) FROM txn WHERE user_id=? AND dup_status='candidate'")[0],
        "refunds_unlinked": q("SELECT count(*) FROM txn WHERE user_id=? AND txn_type='refund' AND linked_txn_id IS NULL")[0],
    }


def print_report(r):
    print(f"\n[품질 리포트] 기간 {r['period']}, 거래 {r['transactions']}건")
    print("  유형별:", ", ".join(f"{k} {v}" for k, v in sorted(r["by_type"].items())))
    print(f"  순지출 합계(환불 차감, 정산·이체·중복 후보 제외): {r['net_spending']:,}원"
          f"  (+ 확인 대기 중복 후보 {r['dup_candidate_amount']:,}원)")
    print(f"  제외한 카드대금 정산: {r['excluded_card_settlement']:,}원")
    print(f"  미분류 비율: {r['uncategorized_ratio']:.0%}  (가맹점 사전·분류는 다음 단계)")
    print(f"  중복 후보(사용자 확인 필요): {r['dup_candidates']}건, 원거래 못 찾은 환불: {r['refunds_unlinked']}건")


def main(argv=None):
    ap = argparse.ArgumentParser(description="거래내역 CSV 업로드·정규화")
    ap.add_argument("files", nargs="+", help="CSV 파일들, 또는 'report'")
    ap.add_argument("--user", required=True)
    ap.add_argument("--self-name", action="append", default=[], help="본인 이름·본인 계좌 별칭 (본인 이체 판별)")
    a = ap.parse_args(argv)
    con = connect()
    if a.files != ["report"]:
        for f in a.files:
            try:
                r = ingest_file(con, pathlib.Path(f), a.user, a.self_name)
            except FormatError as e:
                print(f"[실패] {f}: {e}", file=sys.stderr)
                continue
            print(f"[{r['file']}] {r['format']} / {r['encoding']} / {r['rows']}행 → "
                  f"저장 {r['inserted']}, 중복 {r['duplicate']}, 실패 {len(r['failed'])}")
            for idx, msg in r["failed"][:5]:
                print(f"    {idx}행: {msg}")
    print_report(report(con, a.user))


if __name__ == "__main__":
    main()
