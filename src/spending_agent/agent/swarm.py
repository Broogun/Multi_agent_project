"""압축 메모리 기반 Swarm 멀티에이전트 (docs/architecture/memory-backed-swarm-plan.md, Phase 1).

에이전트 4명이 고정 순서 없이 `handoff_to_*` 로 바통을 넘긴다. 팀장은 없다.
  counselor  (유저 상담)  유저에게 말하는 유일한 창구. respond_to_user 로 끝낸다.
  analyst    (유저 이해)  함정을 걷어내고 쟁점을 정리하고, 꼭 필요한 질문만 한다. 정기 피드백의 진입점.
  strategist (전략가)    부족분을 채울 제안을 조합한다. 제안 절감액은 도구 결과의 숫자만.
  reviewer   (리뷰어)    부족분을 코드로 검산하고(check_gap_coverage), 모자라거나 위반이면 strategist 로 반려한다.

바깥 하네스(이 모듈의 결정적 부분)가 강제하는 것:
  - pre_run: 규칙 전처리 (반복결제·가격 변화·새 결제, 카드대금 맞춤, 목표 달성도, 이름 없는 이체) → 작업판에 요약만 올린다.
  - 홉 가드: MAX_HOPS, strategist↔reviewer 반려 MAX_BOUNCE. 넘으면 counselor 로 강제 마무리.
  - 리뷰 통과 전 제안 금지, 제안 절감액·부족분은 도구 결과 숫자만, 최종 숫자 검증(verify) 실패 시 1회 재작성.
  - 대화 원문 대신 압축 메모리 + 작업판 + 바통 메모만 프롬프트에 넣는다.

baseline.harness.Agent 프로토콜(feedback, understand)을 구현하므로 싱글 에이전트와 같은 조건으로 비교된다.
기록(유저 이해)에 응답할지는 팀 합의 전이라, 지금은 하네스 기준(말하지 않음)대로 analyst 가 메모리만 갱신한다.
LangGraph StateGraph(Command(goto=...)) 로 옮기는 것은 다음 단계다. 지금은 같은 의미의 홉 루프를 직접 돈다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Callable

from ..baseline.harness import FeedbackCtx, UnderstandCtx
from ..baseline.llm import LLM, ScriptedLLM, Step, ToolCall
from ..baseline.single import COMMON, KIND, STATUSES, UNDERSTAND, WEEKDAYS, parse_json
from ..demo.generator import ISSUE_TYPES
from ..demo.responses import ACTIONS
from .memory import AGENTS, MAX_BOUNCE, MAX_HOPS, Board, HandoffNote, UserMemory, new_memory

# ------------------------------------------------------------------ 역할별 도구 권한
DOMAIN: dict[str, list[str]] = {
    "analyst": ["spending_summary", "list_transactions", "recurring_payments", "unnamed_transfers", "card_bills",
                "goal_progress", "ask_user"],
    "strategist": ["spending_summary", "list_transactions", "recurring_payments", "goal_progress", "forecast_goal",
                   "calculate"],
    "reviewer": ["goal_progress", "forecast_goal", "calculate"],
    "counselor": ["calculate", "ask_user"],
}
CONTROL: dict[str, list[str]] = {
    "analyst": ["update_issue"],
    "strategist": ["propose_action", "drop_action"],
    "reviewer": ["check_gap_coverage", "record_review", "update_issue"],
    "counselor": ["respond_to_user"],
}
_STR, _INT = {"type": "string"}, {"type": "integer"}
CONTROL_SPECS = {
    "update_issue": {
        "description": "작업판의 쟁점을 추가하거나 상태를 바꾼다. 같은 유형·대상이면 덮어쓴다.",
        "parameters": {"type": "object", "properties": {
            "type": _STR, "target": _STR, "status": _STR, "summary": _STR,
            "evidence": {"type": "array", "items": _STR, "description": "근거 거래ID"}},
            "required": ["type", "target", "status"]}},
    "propose_action": {
        "description": "제안 후보를 작업판에 올린다. saving_monthly 는 도구 결과(calculate 등)에 있는 월 절감액이어야 한다.",
        "parameters": {"type": "object", "properties": {
            "target": _STR, "action": _STR, "per_week": _INT, "amount": _INT, "saving_monthly": _INT,
            "basis": {"type": "string", "description": "절감액 근거 (계산식)"},
            "text": {"type": "string", "description": "유저에게 보일 문장"}},
            "required": ["target", "action", "saving_monthly", "text"]}},
    "drop_action": {
        "description": "제안 후보를 뺀다 (0부터 시작하는 번호).",
        "parameters": {"type": "object", "properties": {"index": _INT}, "required": ["index"]}},
    "check_gap_coverage": {
        "description": "코드 검산: 월 부족분(도구 결과에 있는 숫자) 대비 제안 절감 합계와 남은 부족분.",
        "parameters": {"type": "object", "properties": {"monthly_shortfall": _INT}, "required": ["monthly_shortfall"]}},
    "record_review": {
        "description": "리뷰 결론을 남긴다. ok=false 면 사유를 적고 strategist 로 반려한다.",
        "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}, "reason": _STR},
                       "required": ["ok", "reason"]}},
    "respond_to_user": {
        "description": "유저에게 보낼 최종 피드백. 작업판의 쟁점과 (리뷰 통과한) 제안이 함께 나간다.",
        "parameters": {"type": "object", "properties": {
            "message": _STR, "goal_on_track": {"type": ["boolean", "null"]}, "goal_summary": _STR},
            "required": ["message"]}},
}
for _a in AGENTS:
    for _b in AGENTS:
        if _a != _b:
            CONTROL_SPECS[f"handoff_to_{_b}"] = {
                "description": f"{_b} 에게 바통을 넘긴다. 무엇을 확인했고 무엇을 해 주면 되는지 짧게.",
                "parameters": {"type": "object", "properties": {
                    "reason": _STR, "focus": {"type": "array", "items": _STR}}, "required": ["reason"]}}

ROLE = {
    "analyst": """너는 '유저 이해(analyst)'다. 목표: 거래 함정(카드대금·충전·정산 입금·환불)을 걷어내고 진짜 쟁점과 원인을 정리한다.
- 작업판의 '규칙 전처리'를 먼저 본다. 새 결제·가격 변화는 쟁점으로 올린다 (새 정기결제, 가격 인상 등).
- 정기적으로 보이는 이체(regular=true)는 묻지 말고 추정으로 둔다. 질문은 숨은 사실(계속 쓸지, 정산인지, 용도 불명) 확인에만 쓴다.
- update_issue 로 쟁점을 정리한 뒤, 대안이 필요하면 handoff_to_strategist, 바꿀 게 없으면 handoff_to_counselor.""",
    "strategist": """너는 '전략가(strategist)'다. 목표: 목표 월 부족분을 실제로 채울 현실적인 제안 조합을 만든다.
- 부족분 = 필요 월 저축액 − 지금 월 저축. calculate 로 계산해 둔다.
- 후보마다 calculate 로 월 절감액을 구하고 propose_action 으로 올린다. 보호 지출·최근 거절한 제안은 넣지 않는다.
- 하나로 모자라면 여러 쟁점(구독·배달 등)을 묶는다. 줄이기는 작게(주 1~2회).
- 다 올렸으면 반드시 handoff_to_reviewer 로 검증을 요청한다.""",
    "reviewer": """너는 '리뷰어(reviewer)'다. 목표: 제안 조합이 부족분을 채우는지 코드로 검산하고 위반을 막는다.
- check_gap_coverage 로 부족분 대비 합계를 본다. 모자라면 record_review(ok=false, 남은 금액) 후 handoff_to_strategist (반려).
- 보호 지출을 건드리거나, 서비스와 무관한 변화를 성과로 말하거나, 근거 없는 제안이 있으면 반려한다.
- 채워졌거나, 더 채울 현실적 후보가 없다는 사유가 있으면 record_review(ok=true) 후 handoff_to_counselor.""",
    "counselor": """너는 '유저 상담(counselor)'이다. 유저에게 말하는 유일한 창구다. 목표: 부담 없이 이해되게 전한다.
- 작업판의 쟁점·제안·리뷰 결과를 바탕으로 respond_to_user 로 끝낸다. 새 전략을 만들지 말고 숫자를 지어내지 않는다.
- 분석이 비었으면 handoff_to_analyst, 제안이 리뷰 전이면 handoff_to_reviewer.""",
}

RULES = """[지금] {today} ({wd}) {kind}. 다음 피드백: {nxt}
질문 한도: 이번 일에서 최대 {max_q}개 (ask_user). 제안은 최대 3개.
쟁점 유형: {issue_types}
쟁점 상태: {statuses}
제안 행동: {actions}. 줄이기는 per_week, 저축 늘리기는 amount 를 적는다."""


def _numbers(obj) -> set[int]:
    """도구 결과에 나온 정수 (1,234 같은 표기 포함)."""
    text = json.dumps(obj, ensure_ascii=False, default=str)
    out = set()
    for x in re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", text):
        v = float(x.replace(",", ""))
        if v.is_integer():                     # 0.75 의 75 처럼 소수 조각은 허용하지 않는다
            out.add(int(v))
    return out


def _dry_policy(system: str, transcript: list[dict], tools: list[dict]) -> Step:
    """연습용 가짜 LLM (API 안 부름): analyst 가 목표 달성도를 한 번 보고 counselor 로 넘기고, counselor 가 응답한다."""
    names = {t["name"] for t in tools}
    if not names:
        return Step(text=json.dumps({"profile_updates": [], "patterns": [], "questions_for_next": []}))
    if "respond_to_user" in names:
        return Step(calls=[ToolCall("dry-r", "respond_to_user", {"message": "내역을 확인했어요."})])
    if "goal_progress" in names and not any(t["role"] == "tool" for t in transcript):
        return Step(calls=[ToolCall("dry-g", "goal_progress", {})])
    return Step(calls=[ToolCall("dry-h", "handoff_to_counselor", {"reason": "연습 실행"})])


def dry_run_llm() -> ScriptedLLM:
    return ScriptedLLM(_dry_policy, model="dry")


class SwarmAgent:
    name = "swarm"

    def __init__(self, llm: LLM | Callable[[str], LLM], max_steps: int = 10, max_retries: int = 1):
        """llm: 하나를 공유하거나, 역할 이름 → LLM 함수 (역할별로 모델을 바꿀 때)."""
        self._llm = llm if callable(llm) and not hasattr(llm, "step") else (lambda _role: llm)
        self.model = getattr(self._llm("counselor"), "model", "")
        self.max_steps, self.max_retries = max_steps, max_retries
        self.memory: UserMemory = new_memory()

    # ================================================================ 하네스 프로토콜
    def feedback(self, ctx: FeedbackCtx) -> dict:
        if ctx.fixed_answers:
            self.memory["fixed_answers"] = ctx.fixed_answers
        meta = {"llm_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "llm_seconds": 0.0,
                "retries": 0, "parse_errors": 0, "hops": 0, "bounces": 0, "by_agent": {}}
        board = Board()
        n0 = len(ctx.tools.log)
        board.precomputed = self._pre_run(ctx)
        board.issues = {f'{i.get("type")}|{i.get("target")}': dict(i) for i in self.memory["active_issues"]}
        out = self._swarm(ctx, board, meta)
        meta["tool_calls"] = len(ctx.tools.log) - n0          # 전처리 포함 실제 도구 호출 수
        meta["trace"] = board.trace
        meta["forced"] = board.forced
        self.memory["active_issues"] = out.get("issues", [])[:20]
        self.memory["last_goal"] = out.get("goal")
        return {**out, "_meta": meta}

    def understand(self, ctx: UnderstandCtx) -> dict:
        ev = ctx.event
        if ev["kind"] == "replies":
            if ev.get("fixed_answers"):
                self.memory["fixed_answers"] = ev["fixed_answers"]
            self.memory["qa"] = (self.memory["qa"] + [{"date": ev["feedback_date"], **q} for q in ev["qa"]])[-20:]
            self.memory["recent_decisions"] = (self.memory["recent_decisions"]
                                               + [{"date": ev["feedback_date"], **r} for r in ev["reactions"]])[-15:]
        meta = {"llm_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "llm_seconds": 0.0,
                "parse_errors": 0, "by_agent": {}}
        system = f"{COMMON}\n\n너는 '유저 이해(analyst)'다.\n{UNDERSTAND}"
        user = (f"[지금] {ctx.today}\n[압축 유저 메모리]\n{self._memory_text()}\n"
                f"[방금 일어난 일]\n{json.dumps(ev, ensure_ascii=False, default=str)}")
        transcript = [{"role": "user", "text": user}]
        out = None
        for _ in range(1 + self.max_retries):
            st = self._step("analyst", system, transcript, [], meta)
            transcript.append({"role": "assistant", "text": st.text, "calls": st.calls, "native": st.native})
            out = parse_json(st.text)
            if out is not None:
                break
            meta["parse_errors"] += 1
            transcript.append({"role": "user", "text": "JSON 형식이 아니다. 정해진 JSON 하나만 다시 답하라."})
        out = out or {}
        self._apply_understanding(out)
        return {**out, "_meta": meta}

    # ================================================================ 하네스: 규칙 전처리
    def _pre_run(self, ctx: FeedbackCtx) -> dict:
        box = ctx.tools
        rec = box.call("recurring_payments", {}).get("value") or {}
        bills = box.call("card_bills", {}).get("value") or {}
        goal = box.call("goal_progress", {}).get("value") or {}
        unnamed = box.call("unnamed_transfers", {}).get("value") or {}
        recurring = rec.get("recurring", [])
        new = [{"merchant": x["merchant"], "first_seen": x["first_seen"], "amount": x["charges"][-1]["amount"],
                "tx": x["charges"][-1]["tx"]}
               for x in rec.get("new_since_onboarding", []) + [r for r in recurring if r.get("started_after_onboarding")]]
        changes = [{"merchant": x["merchant"], **c} for x in recurring for c in x.get("price_changes", [])]
        groups = []
        for g in unnamed.get("groups", [])[:8]:
            months = {d[:7] for d in g["dates"]}
            days = g["days_of_month"]
            # 정기 = 서로 다른 달 2번 이상, 달마다 1~2번, 비슷한 날짜. 한 번에 몰린 정산 입금은 정기가 아니다
            regular = len(months) >= 2 and g["count"] <= 2 * len(months) and max(days) - min(days) <= 3
            groups.append({k: g[k] for k in ("direction", "amount", "count", "days_of_month")} | {"regular": regular})
        return {
            "새 결제(온보딩 이후 처음)": new[:8],
            "가격 변화": changes[:8],
            "반복 결제": [{"merchant": x["merchant"], "category": x["category"], "months": len(x["months"]),
                       "last_amount": x["charges"][-1]["amount"]} for x in recurring][:15],
            "카드대금": [{k: b.get(k) for k in ("bill_month", "amount", "matches", "basis") if k in b}
                      for b in bills.get("bills", [])][-3:],
            "목표": {k: goal.get(k) for k in ("goal_amount", "current_saved", "monthly_required", "progress",
                                              "required_by_now", "on_track") if k in goal},
            "이름 없는 이체": groups,
        }

    # ================================================================ 홉 루프 (가드 포함)
    def _swarm(self, ctx: FeedbackCtx, board: Board, meta: dict) -> dict:
        active, note = "analyst", None
        hops = bounces = retries = 0
        while True:
            kind, val = self._hop(active, ctx, board, note, meta)
            if kind == "final":
                v = ctx.verify(val)
                if not v.get("ok") and retries < self.max_retries:
                    retries += 1
                    meta["retries"] += 1
                    active, note = "counselor", HandoffNote(
                        from_agent="harness", to_agent="counselor", focus=[],
                        reason=f"메시지·제안 문장에 도구 결과에 없는 숫자가 있다: {', '.join(v.get('bad', []))}. 빼거나 도구 결과로 바꿔 다시 respond_to_user.")
                    continue
                return val
            if kind == "stall":
                if active != "counselor" and not board.forced:
                    board.forced = f"{active} 가 바통을 넘기지 않고 멈췄다"
                    active, note = "counselor", self._force_note(active, board.forced)
                    continue
                return self._fallback(board)
            to, args = val
            if to not in AGENTS or to == active:
                to = "counselor"
            hops += 1
            meta["hops"] = hops
            if active == "reviewer" and to == "strategist":
                bounces += 1
                meta["bounces"] = bounces
            board.trace.append(HandoffNote(from_agent=active, to_agent=to, reason=str(args.get("reason", ""))[:200],
                                           focus=[str(x) for x in (args.get("focus") or [])][:5]))
            limit = (f"바통 {hops}번으로 한도({MAX_HOPS}) 도달" if hops >= MAX_HOPS and to != "counselor" else
                     f"반려 {bounces}번으로 한도({MAX_BOUNCE}) 초과" if bounces > MAX_BOUNCE else None)
            if limit:
                if board.forced:                     # 이미 강제 마무리 중인데 또 넘김 → 지금 작업판으로 끝낸다
                    return self._fallback(board)
                board.forced = limit
                active, note = "counselor", self._force_note(active, limit)
                continue
            active, note = to, board.trace[-1]

    def _force_note(self, frm: str, why: str) -> HandoffNote:
        return HandoffNote(from_agent="harness", to_agent="counselor", focus=[],
                           reason=f"{why}. 지금 작업판으로 마무리한다. 리뷰를 통과하지 않은 제안은 보내지 말고 respond_to_user 하라.")

    def _fallback(self, board: Board) -> dict:
        return {"issues": list(board.issues.values()), "goal": {"on_track": None, "summary": ""},
                "suggestions": self._approved(board), "message": ""}

    @staticmethod
    def _approved(board: Board) -> list[dict]:
        if not board.review.get("ok"):
            return []
        keys = ("target", "action", "per_week", "amount", "text")
        return [{k: p[k] for k in keys if p.get(k) is not None} for p in board.proposals][:3]

    # ================================================================ 한 에이전트의 한 번 차례
    def _hop(self, role: str, ctx: FeedbackCtx, board: Board, note: HandoffNote | None, meta: dict):
        specs = [s for s in ctx.tools.specs() if s["name"] in DOMAIN[role]]
        names = CONTROL[role] + [f"handoff_to_{b}" for b in AGENTS if b != role]
        specs += [{"name": n, **CONTROL_SPECS[n]} for n in names]
        day = dt.date.fromisoformat(ctx.today)
        system = (f"{COMMON}\n\n{ROLE[role]}\n\n" + RULES.format(
            today=ctx.today, wd=WEEKDAYS[day.weekday()], kind=KIND[ctx.kind], nxt=ctx.next_feedback or "없음",
            max_q=ctx.tools.max_questions, issue_types=", ".join(ISSUE_TYPES), statuses=", ".join(STATUSES),
            actions=", ".join(ACTIONS)))
        user = (f"[압축 유저 메모리]\n{self._memory_text()}\n[작업판]\n{json.dumps(board.view(), ensure_ascii=False, default=str)}\n"
                f"[받은 바통]\n{json.dumps(note, ensure_ascii=False) if note else '없음 (네가 처음이다)'}")
        transcript: list[dict] = [{"role": "user", "text": user}]
        for _ in range(self.max_steps):
            st = self._step(role, system, transcript, specs, meta)
            transcript.append({"role": "assistant", "text": st.text, "calls": st.calls, "native": st.native})
            if not st.calls:
                transcript.append({"role": "user", "text": "도구로 일하라. 끝낼 때는 handoff_to_* 나 respond_to_user 를 부른다."})
                continue
            for c in st.calls:
                if c.name.startswith("handoff_to_") and c.name in names:
                    return "handoff", (c.name.removeprefix("handoff_to_"), c.args or {})
                if c.name == "respond_to_user" and role == "counselor":
                    r = self._respond(board, c.args or {})
                    if r.get("ok"):
                        return "final", r["out"]
                    out = r
                elif c.name in CONTROL[role]:
                    out = getattr(self, f"_c_{c.name}")(ctx, board, c.args or {})
                elif c.name in DOMAIN[role]:
                    out = ctx.tools.call(c.name, c.args or {})
                else:
                    out = {"ok": False, "error": f"{role} 은(는) {c.name} 을 쓸 수 없다"}
                transcript.append({"role": "tool", "call_id": c.id, "name": c.name, "output": ctx.tools.dump(out)})
        return "stall", None

    def _step(self, role: str, system: str, transcript: list[dict], specs: list[dict], meta: dict) -> Step:
        st = self._llm(role).step(system, transcript, specs)
        meta["llm_calls"] += 1
        meta["input_tokens"] += st.usage.get("input_tokens", 0)
        meta["output_tokens"] += st.usage.get("output_tokens", 0)
        meta["llm_seconds"] = round(meta["llm_seconds"] + st.seconds, 2)
        a = meta["by_agent"].setdefault(role, {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0})
        a["llm_calls"] += 1
        a["input_tokens"] += st.usage.get("input_tokens", 0)
        a["output_tokens"] += st.usage.get("output_tokens", 0)
        return st

    # ================================================================ 제어 도구 (결정적)
    def _known(self, ctx: FeedbackCtx) -> set[int]:
        return _numbers(ctx.tools.results())

    def _c_update_issue(self, ctx, board: Board, a: dict) -> dict:
        if a.get("status") not in STATUSES:
            return {"ok": False, "error": f"status 는 {', '.join(STATUSES)} 중 하나"}
        key = f'{a.get("type")}|{a.get("target")}'
        board.issues[key] = {"type": a.get("type"), "target": a.get("target"), "status": a["status"],
                             "evidence": [str(x) for x in (a.get("evidence") or [])][:10],
                             "summary": str(a.get("summary", ""))[:200]}
        return {"ok": True, "issues": len(board.issues)}

    def _c_propose_action(self, ctx, board: Board, a: dict) -> dict:
        if a.get("action") not in ACTIONS:
            return {"ok": False, "error": f"action 은 {', '.join(ACTIONS)} 중 하나"}
        saving = a.get("saving_monthly")
        if not isinstance(saving, int) or saving <= 0 or saving not in self._known(ctx):
            return {"ok": False, "error": "saving_monthly 는 이번 도구 결과(calculate 등)에 있는 양수여야 한다. 먼저 계산하라."}
        if len(board.proposals) >= 3:
            return {"ok": False, "error": "제안은 최대 3개. drop_action 으로 하나 빼고 올려라."}
        keys = ("target", "action", "per_week", "amount", "saving_monthly", "basis", "text")
        board.proposals.append({k: a[k] for k in keys if a.get(k) is not None})
        board.review, board.gap = {}, {}             # 조합이 바뀌면 검산·검증을 다시 받는다
        return {"ok": True, "proposals": len(board.proposals)}

    def _c_drop_action(self, ctx, board: Board, a: dict) -> dict:
        i = a.get("index")
        if not isinstance(i, int) or not 0 <= i < len(board.proposals):
            return {"ok": False, "error": "없는 번호"}
        board.proposals.pop(i)
        board.review, board.gap = {}, {}
        return {"ok": True, "proposals": len(board.proposals)}

    def _c_check_gap_coverage(self, ctx, board: Board, a: dict) -> dict:
        short = a.get("monthly_shortfall")
        if not isinstance(short, int) or short < 0 or short not in self._known(ctx):
            return {"ok": False, "error": "monthly_shortfall 은 이번 도구 결과에 있는 숫자여야 한다 (calculate 로 구하라)."}
        total = sum(int(p.get("saving_monthly", 0)) for p in board.proposals)
        remaining = max(0, short - total)
        board.gap = {"monthly_shortfall": short, "proposed_saving_sum": total, "remaining_gap": remaining,
                     "covered": remaining == 0,
                     "calc": f"{short:,} − ({' + '.join(f'{int(p.get('saving_monthly', 0)):,}' for p in board.proposals) or '0'})"
                             f" = 남은 부족분 {remaining:,}"}
        ctx.tools.log.append({"name": "check_gap_coverage", "args": a,
                              "output": {"status": "ok", "value": board.gap, "calc": {"gap": board.gap["calc"]},
                                         "sources": [], "as_of": ctx.today, "caveats": []}})
        return {"ok": True, **board.gap}

    def _c_record_review(self, ctx, board: Board, a: dict) -> dict:
        ok = bool(a.get("ok"))
        if ok and board.proposals and not board.gap:
            return {"ok": False, "error": "check_gap_coverage 로 검산한 뒤에 통과시켜라."}
        board.review = {"ok": ok, "reason": str(a.get("reason", ""))[:300]}
        return {"ok": True, "review": board.review}

    def _respond(self, board: Board, a: dict) -> dict:
        if board.proposals and not board.review.get("ok") and not board.forced:
            return {"ok": False, "error": "리뷰를 통과하지 않은 제안이 있다. handoff_to_reviewer 로 검증받아라."}
        goal = {"on_track": a.get("goal_on_track"), "summary": str(a.get("goal_summary", ""))}
        out = {"issues": list(board.issues.values()), "goal": goal, "suggestions": self._approved(board),
               "message": str(a.get("message", ""))}
        return {"ok": True, "out": out}

    # ================================================================ 메모리
    def _memory_text(self) -> str:
        m = self.memory
        view = {"고정 답변": m["fixed_answers"], "유저 정보": m["profile"], "소비 패턴": m["patterns"],
                "다음에 물을 질문": m["queued_questions"], "활성 쟁점": m["active_issues"], "지난 목표 판단": m["last_goal"],
                "제안과 유저 반응": m["recent_decisions"], "질문과 답": m["qa"]}
        return json.dumps(view, ensure_ascii=False, default=str)

    def _apply_understanding(self, out: dict) -> None:
        for u in out.get("profile_updates", []) or []:
            if isinstance(u, dict) and u.get("field"):
                self.memory["profile"][str(u["field"])] = {"value": u.get("value"), "kind": u.get("kind"),
                                                          "evidence": u.get("evidence")}
        if isinstance(out.get("patterns"), list):
            self.memory["patterns"] = [str(x) for x in out["patterns"]][:10]
        if isinstance(out.get("questions_for_next"), list):
            self.memory["queued_questions"] = out["questions_for_next"][:5]
