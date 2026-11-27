"""평가 케이스 고르기 (spending_agent.demo.cases)."""
from __future__ import annotations

import json

import pytest

from spending_agent.demo.cases import reply_cases
from spending_agent.demo.generator import build_world, load_persona

PERSONAS = ["P1", "P2", "P3", "P4", "P5"]


@pytest.fixture(scope="module")
def truths() -> dict[str, dict]:
    # truth.json에 쓰인 모양 그대로 (날짜는 문자열)
    return {pid: json.loads(json.dumps(build_world(load_persona(pid)).truth, default=str)) for pid in PERSONAS}


def _ids(cases):
    return [c["id"] for c in cases]


def test_p1_two_weeks_matches_baseline(truths):
    """12/14까지면 기준선 리포트(261126-2030)에서 채점한 P1 케이스 9개와 같다."""
    assert _ids(reply_cases(truths["P1"], "2026-12-14")) == [f"P1-C{i:02d}" for i in range(1, 10)]


def test_p1_full_period_still_excludes_feedback_cases(truths):
    """1/31까지 만들어도 쟁점·월간 피드백 케이스(C10~C14)는 이 구조로 채점하지 않는다."""
    assert _ids(reply_cases(truths["P1"], "2027-01-31")) == [f"P1-C{i:02d}" for i in range(1, 10)]


def test_cases_after_data_end_are_dropped(truths):
    ids = _ids(reply_cases(truths["P1"], "2026-12-06"))
    assert "P1-C05" in ids and "P1-C06" in ids        # 12/6 피드백까지 확인
    assert "P1-C07" not in ids and "P1-C08" not in ids  # 12/13 피드백에서 확인


@pytest.mark.parametrize("pid", PERSONAS)
def test_only_reply_gradable_cases(truths, pid):
    picked = reply_cases(truths[pid], "2027-01-31")
    for c in picked:
        assert "issue" not in c
        assert c["phase"] == "onboarding" or c.get("record") or c.get("check_at") is None
    dropped = [c for c in truths[pid]["rubric"]["cases"] if c not in picked]
    assert dropped and all("issue" in c or (c["phase"] == "live" and not c.get("record")) for c in dropped)
