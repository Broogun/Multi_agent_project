"""비교 하네스: 같은 페르소나·날짜 진행·도구·유저 시뮬레이터로 에이전트를 돌린다 (docs/10 §2~§3).

에이전트는 `Agent` 프로토콜만 구현하면 된다. 싱글 에이전트(single.py)가 기준선이고, 멀티 에이전트도 같은 프로토콜을
구현해 `run()` 에 넣으면 같은 조건(도구·질문 한도·숫자 검증·두 세계)으로 비교된다.

하루 진행 (docs/10 §2, generator.FEEDBACK_HOUR):
  온보딩(D0 0시) → 그날 21시까지 쓴 기록마다 유저 이해 → 21시에 그날 거래가 들어옴 → 피드백 날이면 피드백
  → 21시 뒤에 쓴 기록마다 유저 이해 (다음 피드백에서 다룬다)
세계 (demo.responses 의 세 세계): 처음에는 ② 앱만(자기 기록 효과)의 거래·기록이 들어온다. 피드백에서 제안이 수락되면
③ 앱 + 피드백(responses.with_self)으로 다시 만들고, 다음 날부터 바뀐 거래·기록이 들어온다. 이미 들어온 것은 바뀌지 않는다.
에이전트 입력에는 truth.json 의 정답·숨은 사실을 넣지 않는다. 숨은 사실은 유저 시뮬레이터가 질문에 답할 때만 쓴다.
"""
from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..agent.tools import verify_response
from ..contracts import ToolResult, Validation
from ..demo import responses
from ..demo.generator import FEEDBACK_HOUR, World, build_world, load_persona
from .simulator import Simulator, strengths
from .tools import ToolBox, View

DAY = dt.timedelta(days=1)
MAX_QUESTIONS = 3        # 피드백 한 번에 (docs/10 §5 "주 3개까지")
MAX_SUGGESTIONS = 3


@dataclass
class FeedbackCtx:
    today: str
    kind: str                         # onboarding | weekly | monthly
    next_feedback: str | None
    tools: ToolBox
    verify: Callable[[dict], Validation]
    fixed_answers: dict | None = None  # 온보딩 때만 (고정 질의에 유저가 답한 것)


@dataclass
class UnderstandCtx:
    today: str
    event: dict                       # {"kind": "record", ...} | {"kind": "replies", ...}


class Agent(Protocol):
    name: str
    model: str

    def feedback(self, ctx: FeedbackCtx) -> dict:
        """피드백 형식 dict (issues, goal, suggestions, message). `_meta` 키에 LLM 호출 수·토큰을 넣을 수 있다."""
        ...

    def understand(self, ctx: UnderstandCtx) -> dict:
        """유저 이해. 유저에게 말하지 않는다."""
        ...


@dataclass
class Run:
    persona: str
    agent: str
    model: str
    until: str
    self_scale: float
    calls: list[dict] = field(default_factory=list)
    feedbacks: list[dict] = field(default_factory=list)
    accepted: list[dict] = field(default_factory=list)
    rebuilds: int = 0


def user_numbers(texts: list[str]) -> ToolResult:
    """유저가 준 숫자(고정 답변, 기록, 질문 답)와 일정은 검증 허용 목록에 넣는다."""
    return {"status": "ok", "value": {"user_and_schedule": texts}, "calc": {}, "sources": [], "as_of": None, "caveats": []}


def verify_feedback(out: dict, results: list[ToolResult]) -> Validation:
    texts = [str(out.get("message", ""))] + [str(s.get("text", "")) for s in out.get("suggestions", [])]
    own = []          # 에이전트가 고른 제안 강도(주 n회·금액·새 마감일)는 사실이 아니라 선택이라 허용한다
    for s in out.get("suggestions", []):
        st = strengths(s) if isinstance(s, dict) else {}
        own += [f"{st[k]:g}" for k in ("per_week", "amount") if k in st] + ([st["deadline"]] if "deadline" in st else [])
    return verify_response("\n".join(texts), results + [user_numbers(own)])


def _normalize(out: dict) -> tuple[dict, list[str]]:
    notes = []
    out = dict(out or {})
    out.setdefault("issues", [])
    out.setdefault("suggestions", [])
    out.setdefault("message", "")
    for k in ("issues", "suggestions"):
        if not isinstance(out[k], list):
            notes.append(f"{k} 형식 오류")
            out[k] = []
    if len(out["suggestions"]) > MAX_SUGGESTIONS:
        notes.append(f"제안 {len(out['suggestions'])}개 → {MAX_SUGGESTIONS}개만 받음")
        out["suggestions"] = out["suggestions"][:MAX_SUGGESTIONS]
    out["suggestions"] = [s for s in out["suggestions"] if isinstance(s, dict)]
    return out, notes


def _same_past(old: World, new: World, delivered: set[str], through: dt.date) -> None:
    for k in delivered:
        if old.batches[k] != new.batches[k]:
            raise RuntimeError(f"서비스 있음 세계를 다시 만들었더니 이미 들어온 {k} 거래가 바뀌었다")
    for d, recs in old.records.items():
        if d <= through.isoformat() and recs != new.records.get(d):
            raise RuntimeError(f"서비스 있음 세계를 다시 만들었더니 이미 쓴 {d} 기록이 바뀌었다")


def served_world(p: dict, scale: float, accepted: list[dict]) -> World:
    """에이전트가 실제로 본 세계: 수락이 없으면 ② 앱만, 있으면 ③ 앱 + 피드백. 채점(score.py)도 이 세계로 한다."""
    return build_world(responses.with_self(p, scale, [dict(a, day=dt.date.fromisoformat(a["day"])) for a in accepted])[0])


def run(pid: str, agent: Agent, until: dt.date | None = None,
        self_scale: float = responses.SELF_SCALES["중간"]) -> Run:
    p = load_persona(pid)
    sim = Simulator(p, build_world(p).truth["user_sim"])
    w = served_world(p, self_scale, [])             # ② 앱만
    kinds = {pt["date"]: pt["kind"] for pt in w.feedback}
    dates = sorted(kinds)
    last = min(until or w.end - DAY, w.end - DAY)
    rec = Run(pid, agent.name, getattr(agent, "model", ""), last.isoformat(), self_scale)
    visible, delivered = list(w.batches["onboarding"]), {"onboarding"}
    told: list[str] = []          # 유저가 준 텍스트 (숫자 검증 허용)

    def call(mode: str, at: str, fn, ctx, **extra) -> dict:
        t0 = time.time()
        out = fn(ctx) or {}
        meta = out.pop("_meta", {}) if isinstance(out, dict) else {}
        rec.calls.append({"at": at, "mode": mode, **extra, "seconds": round(time.time() - t0, 2), "meta": meta,
                          "output": out})
        return out

    def understand(at: str, event: dict, ref: str) -> None:
        call("understand", at, agent.understand, UnderstandCtx(at[:10], event), ref=ref)

    def feedback(day: dt.date, kind: str, through: dt.date) -> None:
        nonlocal w
        today = day.isoformat()
        box = ToolBox(View(p, w, list(visible), through, day), ask=sim.answer, max_questions=MAX_QUESTIONS)
        checks: list[Validation] = []
        fixed = sim.fixed_answers() if kind == "onboarding" else None
        if fixed:
            told.append(str(fixed))
        nxt = next((d for d in dates if d > today), None)
        sched = [today] + ([nxt] if nxt else [])

        def check(out: dict) -> Validation:
            v = verify_feedback(out, box.results() + [user_numbers(told + sched + [str(q) for q in box.questions])])
            checks.append(v)
            return v

        ctx = FeedbackCtx(today, kind, nxt, box, check, fixed)
        at = f"{today}T{0 if kind == 'onboarding' else FEEDBACK_HOUR:02d}:00:00"
        out = call("feedback", at, agent.feedback, ctx, kind=kind)
        out, notes = _normalize(out)
        told.extend(q["answer"] for q in box.questions)
        final = check(out)
        reactions = [{"suggestion": s, **sim.react(s, day)} for s in out["suggestions"]]
        new = [{"response": r["response"], "day": today, **{k: r[k] for k in responses.STRENGTHS if k in r}}
               for r in reactions if r["decision"] == "수락"]
        rec.feedbacks.append({"date": today, "kind": kind, "output": out, "notes": notes, "tools": box.log,
                              "questions": box.questions, "validation_first": checks[0], "validation_final": final,
                              "reactions": reactions, "accepted": new})
        rec.calls[-1]["output"] = out
        if new:                                     # ③ 앱 + 피드백으로 다시 만든다
            rec.accepted += new
            w2 = served_world(p, self_scale, rec.accepted)
            _same_past(w, w2, delivered, day)
            w, rec.rebuilds = w2, rec.rebuilds + 1
        event = {"kind": "replies", "feedback_date": today,
                 "qa": [{"target": q["target"], "question": q["question"], "answer": q["answer"]} for q in box.questions],
                 "reactions": [{"target": r["suggestion"].get("target"), "action": r["suggestion"].get("action"),
                                "decision": r["decision"]} for r in reactions]}
        if fixed:
            event["fixed_answers"] = fixed
        if event["qa"] or event["reactions"] or fixed:
            understand(at, event, f"replies-{today}")

    def record(r: dict) -> None:
        told.append(r["text"])
        tx = next((x for x in visible if x["거래ID"] == r["transaction_id"]), None) if r["transaction_id"] else None
        understand(r["written_at"], {"kind": "record", "record": r, "transaction": tx}, r["record_id"])

    feedback(w.d0, "onboarding", w.d0 - DAY)
    day = w.d0
    while day <= last:
        today = day.isoformat()
        cutoff = dt.datetime.combine(day, dt.time(FEEDBACK_HOUR)).isoformat()
        recs = sorted(w.records.get(today, []), key=lambda r: r["written_at"])
        for r in (r for r in recs if r["written_at"] <= cutoff):
            record(r)
        visible += w.batches[today]
        delivered.add(today)
        if kinds.get(today) in ("weekly", "monthly"):
            feedback(day, kinds[today], day)
        for r in (r for r in recs if r["written_at"] > cutoff):
            record(r)
        day += DAY
    return rec
