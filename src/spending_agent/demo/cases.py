"""평가 케이스 고르기 (평가 코드 전용).

`truth.json`의 정답을 다루므로 앱·에이전트 입력 쪽 코드에서 쓰지 않는다 (docs/08-demo-data.md §6).
"""
from __future__ import annotations


def reply_cases(truth: dict, upto: str) -> list[dict]:
    """기록마다 에이전트가 답하는 구조(온보딩 응답 + 기록별 응답)로 채점할 수 있는 케이스.

    - 고르는 것: 온보딩 케이스, 기록(`record`)이 붙은 케이스, 매일 보는 케이스(`check_at` 없음).
    - 빼는 것: 기간 중 쟁점 케이스(`issue`)와 월간 피드백 케이스. 주간·월간 피드백 응답이 있어야 채점할 수 있다.
    - `check_at`이 `upto`(데이터 마지막 날, YYYY-MM-DD)보다 늦은 케이스도 뺀다.
    """
    out = []
    for c in truth["rubric"]["cases"]:
        at = c.get("check_at")
        if at is not None and str(at) > upto:
            continue
        if c["phase"] == "onboarding" or c.get("record") or at is None:
            out.append(c)
    return out
