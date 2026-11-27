"""P1 단일 에이전트 기준선(eval_single_agent_p1.py)을 그 구조로 채점할 수 있는 케이스만 골라 실행한다.

데모 데이터를 1/31까지 늘리면서 truth.json에 기간 중 쟁점 케이스와 월간 피드백 케이스가 늘었다 (P1: 9 → 14개).
원래 스크립트는 온보딩과 기록마다 답하는 구조라, 늘어난 케이스는 맞는 응답이 없어 온보딩 응답으로 채점된다.
원래 스크립트는 그대로 두고, 여기서 채점 케이스만 걸러서 실행한다 (spending_agent.demo.cases.reply_cases).
데이터를 12/14까지 만들면 기준선 리포트(261126-2030)와 같은 9개가 남는다.

준비: uv run python -m spending_agent.demo build P1 && uv run python -m spending_agent.demo advance P1 --days 14
실행: uv run --with openai --with python-dotenv python -X utf8 scripts/eval_single_agent_p1_reply_cases.py data/demo/P1 .env [model]
인자와 결과 저장 위치(eval/runs/)는 원래 스크립트와 같다.
"""
from __future__ import annotations

import eval_single_agent_p1 as base  # 같은 폴더의 원래 스크립트. 불러올 때 sys.argv로 데이터와 .env를 읽는다

from spending_agent.demo.cases import reply_cases


def main() -> None:
    upto = max(d.name for d in (base.DEMO / "daily").iterdir())
    keep = reply_cases(base.truth, upto)
    skipped = [c["id"] for c in base.CASES if c not in keep]
    base.CASES[:] = keep  # 원래 스크립트의 main()이 이 목록을 채점한다
    print(f"채점 케이스 {len(keep)}개 (데이터 {upto}까지). 뺀 케이스: {', '.join(skipped) or '없음'}\n")
    base.main()


if __name__ == "__main__":
    main()
