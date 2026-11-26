"""데모 데이터를 Supabase(Postgres)에 올린다. 팀원끼리 같은 데이터를 보기 위한 열람용이다.

- demo 스키마: personas, transactions, records (앱이 받는 것과 같은 모습)
- demo_eval 스키마: truth(jsonb 전체), cases, tx_truth, tx_compare 뷰 (평가 정답, 앱·에이전트 입력 금지)
두 스키마 모두 공개 키(anon·authenticated)로는 열지 않는다. 대시보드(Table Editor)와 DB 연결로만 본다.

연결: .env 의 SUPABASE_DB_URL (대시보드 Connect → Session pooler 연결 문자열).
비밀번호가 들어 있으니 저장소·대화·커밋에 올리지 않는다. 팀원에게는 DM으로만 전달한다 (docs/05 §6).

실행:
    uv sync --group dev --group supabase
    uv run python -m spending_agent.demo publish            # 5명 전체
    uv run python -m spending_agent.demo publish P4 --reset # 스키마를 지우고 다시 만든 뒤 P4만
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from .. import config
from .generator import World, build_world, load_persona, personas

SCHEMA_SQL = Path(__file__).with_name("schema.sql")
DAY = dt.timedelta(days=1)

COLUMNS: dict[str, tuple[str, ...]] = {
    "demo.personas": ("persona_id", "user_id", "gender", "age", "occupation", "housing", "summary", "d0",
                      "history_from", "history_to", "live_to", "generated_at", "source_rev"),
    "demo.transactions": ("persona_id", "tx_id", "batch", "visible_on", "tx_date", "tx_time", "type", "category_main",
                          "category_sub", "description", "amount", "payment_method", "memo"),
    "demo.records": ("persona_id", "record_id", "user_id", "written_at", "text", "amount", "transaction_id"),
    "demo_eval.truth": ("persona_id", "generated_at", "truth"),
    "demo_eval.cases": ("persona_id", "case_id", "phase", "level", "target", "composite", "signal", "expect",
                        "must_not", "why"),
    "demo_eval.tx_truth": ("persona_id", "tx_id", "event_id", "kind", "channel", "true_category", "amount", "group_id",
                           "ref_event", "installment", "visible_on"),
}
JSON_COLUMNS = {("demo_eval.truth", "truth"), ("demo_eval.cases", "expect"), ("demo_eval.cases", "must_not")}
RESET_SQL = "drop schema if exists demo_eval cascade; drop schema if exists demo cascade;"


def table_rows(w: World, generated_at: dt.datetime, source_rev: str | None) -> dict[str, list[tuple]]:
    """World → 테이블별 행. DB 없이 테스트할 수 있게 순수 함수로 둔다."""
    p, pid = w.persona, w.persona["id"]
    prof = p.get("profile", {})
    facts = w.truth["facts"]
    visible = {e["tx_id"]: dt.date.fromisoformat(e["visible_on"]) for e in facts["events"]}
    out: dict[str, list[tuple]] = {
        "demo.personas": [(pid, p["user_id"], prof.get("gender"), prof.get("age"), prof.get("occupation"),
                           prof.get("housing"), prof.get("summary"), w.d0, w.start, w.d0 - DAY, w.end - DAY,
                           generated_at, source_rev)],
        "demo.transactions": [
            (pid, r["거래ID"], batch, visible[r["거래ID"]], dt.date.fromisoformat(r["날짜"]),
             dt.time.fromisoformat(r["시간"]), r["타입"], r["대분류"], r["소분류"] or None, r["내용"], r["금액"],
             r["결제수단"], r["메모"] or None)
            for batch, rows in w.batches.items() for r in rows],
        "demo.records": [
            (pid, r["record_id"], r["user_id"], dt.datetime.fromisoformat(r["written_at"]), r["text"], r["amount"],
             r["transaction_id"])
            for day in w.records.values() for r in day],
        "demo_eval.truth": [(pid, generated_at, w.truth)],
        "demo_eval.cases": [
            (pid, c["id"], c["phase"], c["level"], c["target"], bool(c.get("composite")), c["signal"], c["expect"],
             c["must_not"], c.get("why"))
            for c in w.truth["rubric"]["cases"]],
        "demo_eval.tx_truth": [
            (pid, e["tx_id"], e["event_id"], e["kind"], e["channel"], e["true_category"], e["amount"], e["group"],
             e["ref"], e["installment"], dt.date.fromisoformat(e["visible_on"]))
            for e in facts["events"]],
    }
    return out


def _dumps(obj) -> str:                  # 목표 마감일 같은 date 값이 들어 있다
    return json.dumps(obj, ensure_ascii=False, default=str)


def _source_rev() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT, capture_output=True,
                              text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def publish(pids: list[str] | None = None, reset: bool = False, url: str | None = None) -> dict[str, dict[str, int]]:
    """페르소나별로 기존 행을 지우고 다시 넣는다 (한 트랜잭션). 테이블별 건수를 돌려준다."""
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as e:
        raise SystemExit("psycopg 가 없습니다. `uv sync --group dev --group supabase` 를 먼저 실행하세요.") from e
    url = url or config.get("SUPABASE_DB_URL", required=True)
    pids = pids or personas()
    generated_at, rev = dt.datetime.now(dt.UTC), _source_rev()
    worlds = [build_world(load_persona(pid)) for pid in pids]       # DB에 붙기 전에 생성 오류부터 잡는다
    counts: dict[str, dict[str, int]] = {}
    with psycopg.connect(url, connect_timeout=15) as conn, conn.cursor() as cur:
        if reset:
            cur.execute(RESET_SQL)
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        for w in worlds:
            pid = w.persona["id"]
            for table in ("demo_eval.truth", "demo_eval.cases", "demo_eval.tx_truth", "demo.personas"):
                cur.execute(f"delete from {table} where persona_id = %s", (pid,))   # personas 는 cascade
            tables = table_rows(w, generated_at, rev)
            for table, rows in tables.items():
                cols = COLUMNS[table]
                json_idx = [i for i, c in enumerate(cols) if (table, c) in JSON_COLUMNS]
                rows = [tuple(Jsonb(v, dumps=_dumps) if i in json_idx else v for i, v in enumerate(r)) for r in rows]
                cur.executemany(f"insert into {table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
                                rows)
            counts[pid] = {t: len(r) for t, r in tables.items()}
    return counts
