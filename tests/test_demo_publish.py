"""데모 데이터 Supabase 공유: 테이블 행 구성, 정답 분리, 스키마 SQL. 실제 DB 테스트는 live 로 분리한다."""
from __future__ import annotations

import datetime as dt
import os
import re

import pytest

from spending_agent.demo.generator import build_world, load_persona, personas
from spending_agent.demo.publish import COLUMNS, SCHEMA_SQL, table_rows

NOW = dt.datetime(2026, 11, 26, tzinfo=dt.UTC)


@pytest.fixture(scope="module", params=personas())
def rows(request):
    w = build_world(load_persona(request.param))
    return w, table_rows(w, NOW, "test")


def test_every_table_has_rows_matching_columns(rows):
    _, tables = rows
    assert set(tables) == set(COLUMNS)
    for table, items in tables.items():
        assert items, table
        assert all(len(r) == len(COLUMNS[table]) for r in items), table


def test_counts_match_generated_world(rows):
    w, tables = rows
    assert len(tables["demo.transactions"]) == sum(len(b) for b in w.batches.values())
    assert len(tables["demo.records"]) == len(w.persona["records"])
    assert len(tables["demo_eval.cases"]) == len(w.persona["cases"])
    assert len(tables["demo_eval.tx_truth"]) == len(w.truth["facts"]["events"])
    keys = [(r[0], r[1]) for r in tables["demo.transactions"]]
    assert len(keys) == len(set(keys))                                   # 기본키 (persona_id, tx_id)


def test_app_facing_schema_has_no_answers():
    """demo 스키마(앱이 받는 모습)에는 실제 용도·숨은 사실 같은 정답이 없다."""
    demo_cols = {c for t, cols in COLUMNS.items() if t.startswith("demo.") for c in cols}
    assert not demo_cols & {"true_category", "truth", "hidden_facts", "expect", "must_not", "group_id", "kind"}


def test_batches_and_visibility(rows):
    w, tables = rows
    for r in tables["demo.transactions"]:
        batch, visible_on, tx_date = r[2], r[3], r[4]
        assert tx_date <= visible_on
        assert (visible_on < w.d0) if batch == "onboarding" else (batch == visible_on.isoformat())


def test_schema_sql_creates_every_table_and_closes_public_api():
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    for table in COLUMNS:
        assert re.search(rf"create table if not exists {re.escape(table)}\b", sql), table
    assert "revoke all on schema demo, demo_eval from anon, authenticated" in sql
    assert "grant" not in sql.lower()                                    # 공개 키에 권한을 주지 않는다


@pytest.mark.live
def test_shared_db_matches_generator():
    """SUPABASE_DB_URL 이 있을 때만 (CI 제외): 공유 DB 건수가 지금 생성기 결과와 같은지 읽기만 한다.
    테스트가 공유 데이터를 고치지 않는다. 다르면 `publish` 를 다시 실행한다."""
    psycopg = pytest.importorskip("psycopg")
    from spending_agent import config

    url = config.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DB_URL")
    if not url:
        pytest.skip("SUPABASE_DB_URL 없음")
    stale = []
    with psycopg.connect(url, connect_timeout=15) as conn:
        for pid in personas():
            expected = table_rows(build_world(load_persona(pid)), NOW, None)
            for table, items in expected.items():
                n = conn.execute(f"select count(*) from {table} where persona_id = %s", (pid,)).fetchone()[0]
                if n != len(items):
                    stale.append(f"{pid} {table}: DB {n} / 생성기 {len(items)}")
    assert not stale, "공유 DB가 생성기와 다르다. `uv run python -m spending_agent.demo publish` 를 다시 실행: " + "; ".join(stale)
