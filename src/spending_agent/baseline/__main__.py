"""기준선 CLI. 사용법은 패키지 설명(__init__.py) 참고."""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
from pathlib import Path

from ..config import ROOT
from ..demo.responses import SELF_SCALES
from .harness import run
from .llm import OpenAIResponses, dry_run_llm
from .score import score
from .single import SingleAgent

RUNS = ROOT / "eval" / "runs"
DEFAULT_MODEL = "gpt-6.1-terra"        # 1차 기준선(261126)과 같은 모델
DEFAULT_JUDGE = "gpt-6.2-sol"


def _llm(kind: str, model: str):
    return dry_run_llm() if kind == "dry" else OpenAIResponses(model)


def _agent(kind: str, llm_kind: str, model: str):
    if kind == "swarm":                       # 에이전트 구조만 바꾸고 하네스·도구·채점은 같다
        from ..agent import swarm
        return swarm.SwarmAgent(swarm.dry_run_llm() if llm_kind == "dry" else OpenAIResponses(model))
    return SingleAgent(_llm(llm_kind, model))


def summary(s: dict) -> str:
    i, n, q, g, c = s["issues"], s["numbers"], s["questions"], s["suggestions"], s["cost"]
    lines = [
        (f"쟁점: 필수 {i['required']}개 중 {i['hit']}개 짚음 (재현율 {i['recall']}), 끝난 쟁점 해결 갱신 "
         f"{i['closed_updated']}/{i['closed_to_update']}, 정답과 안 맞은 쟁점 {i['unmatched_agent_issues']}개"
         + (f", 제안으로 막은 쟁점 {', '.join(i['prevented'])}" if i.get("prevented") else "")),
        f"숫자 검증: 피드백 {n['feedbacks']}번 중 첫 답 통과 {n['first_ok']}, 최종 통과 {n['final_ok']}",
        f"질문: {q['asked']}개, 숨은 사실을 맞힌 질문 {q['hit_fact']}개 (밝혀진 사실 {len(q['facts'])}/{q['facts_total']})",
        f"제안: {g['total']}개 (수락 {g['수락']} · 거절 {g['거절']} · 보류 {g['보류']}), 보호 지출 위반 {g['protected_violations']}",
        (f"절약 (자기 기록 효과 배율 {s['saving']['scale']}): 피드백 효과 기댓값 "
         f"{s['saving']['expected_feedback_effect']:,}원 (실제 거래 {s['saving']['feedback_effect']:,}원, "
         f"서비스 전체 {s['saving']['total']:,}원) — {s['saving']['calc']}") if s["saving"]
        else "절약: 반응 규칙 없음 (페르소나 파일에 [[responses]] 추가 필요)",
        (f"비용: LLM {c['llm_calls']}회 · 도구 {c['tool_calls']}회 · 입력 {c['input_tokens']:,} · 출력 {c['output_tokens']:,} 토큰"
         f" · 호출당 입력 최대 {c['max_input_per_call']:,} · {c['seconds']}초"),
    ]
    for case in s.get("cases", []):
        lines.append(f"  {case['id']}: {case.get('verdict')} — {case.get('reason')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m spending_agent.baseline", description="싱글 에이전트 기준선")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="페르소나 하나를 온보딩부터 끝까지 돌리고 채점한다")
    r.add_argument("persona")
    r.add_argument("--llm", choices=["dry", "openai"], required=True, help="dry: API 안 부르는 연습용")
    r.add_argument("--agent", choices=["single", "swarm"], default="single",
                   help="single: 싱글 에이전트 기준선, swarm: 압축 메모리 Swarm (agent.swarm)")
    r.add_argument("--model", default=os.environ.get("BASELINE_MODEL", DEFAULT_MODEL))
    r.add_argument("--until", type=dt.date.fromisoformat, help="이 날짜까지만 (예: 시연 기간 2026-12-14)")
    r.add_argument("--self-scale", choices=list(SELF_SCALES), default="중간",
                   help="자기 기록 효과 크기 (가정, demo.responses.SELF_SCALES)")
    s = sub.add_parser("score", help="저장된 실행 기록을 다시 채점한다")
    s.add_argument("path", type=Path)
    for p in (r, s):
        p.add_argument("--judge", action="store_true", help="케이스별 LLM 심판도 돌린다 (API 호출)")
        p.add_argument("--judge-model", default=DEFAULT_JUDGE)
    a = ap.parse_args(argv)

    judge = OpenAIResponses(a.judge_model) if a.judge else None
    if a.cmd == "run":
        rec = dataclasses.asdict(run(a.persona, _agent(a.agent, a.llm, a.model), a.until, SELF_SCALES[a.self_scale]))
        result = score(rec, judge)
        RUNS.mkdir(parents=True, exist_ok=True)
        path = RUNS / f"baseline_{a.persona}_{rec['agent']}_{rec['model']}_{dt.datetime.now():%y%m%d-%H%M%S}.json"
        path.write_text(json.dumps({**rec, "score": result}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    else:
        rec = json.loads(a.path.read_text(encoding="utf-8"))
        result, path = score(rec, judge), a.path
        rec["score"] = result
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"{rec['persona']} · {rec['agent']} · {rec['model']} · ~{rec['until']}\n{summary(result)}\n저장: {path}")


if __name__ == "__main__":
    main()
