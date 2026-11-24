"""응답 검증기: 답변 속 숫자가 Tool 결과나 사용자 입력에 있는지 대조한다 (통합 문서 3-2 탭).

LLM이 만든 숫자를 잡아내는 장치다. 검증되지 않은 숫자가 있으면 그 문장을 돌려준다.
'15만 원', '42.5만', '1,500,000원', '13,500원' 처럼 표기가 달라도 같은 값으로 본다.
열거·순서용 작은 수(10 이하)는 검사하지 않는다.
"""
from __future__ import annotations

import json
import re

NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(만|억|천)?")
SMALL = 10


def _values(text: str) -> set[float]:
    vals = set()
    for m in NUM.finditer(text):
        raw, unit = m.group(1).replace(",", ""), m.group(2)
        try:
            v = float(raw)
        except ValueError:
            continue
        vals.add(v)
        mult = {"만": 10_000, "억": 100_000_000, "천": 1_000}.get(unit or "")
        if mult:
            vals.add(v * mult)
    return vals


def allowed_numbers(tool_results: list, user_texts: list[str]) -> set[float]:
    pool = set()
    for r in tool_results:
        pool |= _values(json.dumps(r, ensure_ascii=False, default=str))
    for t in user_texts:
        pool |= _values(t)
    # 반올림 표기 허용: 425,000 → 42.5만, 1,236,000 → 123.6만 등
    rounded = set()
    for v in pool:
        if v >= 10_000:
            for step in (1_000, 10_000):
                rounded.add(round(v / step) * step)
    return pool | rounded


def check(answer: str, tool_results: list, user_texts: list[str]) -> list[str]:
    """검증되지 않은 숫자가 든 문장 목록. 빈 목록이면 통과."""
    pool = allowed_numbers(tool_results, user_texts)
    bad = []
    for sent in re.split(r"(?<=[.!?。])\s+|\n", answer):
        for m in NUM.finditer(sent):
            raw, unit = m.group(1).replace(",", ""), m.group(2)
            try:
                v = float(raw)
            except ValueError:
                continue
            if v <= SMALL and not unit:
                continue
            mult = {"만": 10_000, "억": 100_000_000, "천": 1_000}.get(unit or "", 1)
            if v not in pool and v * mult not in pool:
                bad.append(sent.strip())
                break
    return bad
