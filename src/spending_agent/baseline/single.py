"""싱글 에이전트 기준선 (아키텍처 명세 11.1 의 A1: LLM 1개 + 결정적 도구).

LLM 하나를 두 가지 방식으로 부른다 (docs/10 §2).
- 피드백 (온보딩·주간·월간): 도구로 내역을 보고, 필요하면 질문하고, 쟁점·목표 달성도·제안·메시지를 JSON 으로 낸다.
- 유저 이해 (기록, 질문 답, 제안 반응): 도구 없이 유저 정보를 갱신한다. 유저에게 말하지 않는다.
호출 사이에는 대화 전체가 아니라 요약 상태(memory)만 넘긴다. 1차 기준선(261126)의 입력 토큰 누적을 피하기 위해서다.
숫자 검증에 걸리면 한 번 고쳐 내게 한다 (하네스 검증기를 쓰므로 멀티 에이전트와 같은 기준).
"""
from __future__ import annotations

import datetime as dt
import json

from ..demo.generator import ISSUE_TYPES
from ..demo.responses import ACTIONS
from .harness import FeedbackCtx, UnderstandCtx
from .llm import LLM, Step

STATUSES = ("새로 생김", "그대로", "개선", "악화", "해결")
WEEKDAYS = "월화수목금토일"
KIND = {"onboarding": "온보딩", "weekly": "주간 피드백", "monthly": "월간 피드백"}

COMMON = """너는 목표 지향 소비 관리 앱의 AI 코치다. 유저의 통합 거래내역과 목표를 보고, 짚어야 할 쟁점을 찾고, 목표에 맞는 작은 변화를 제안한다.
원칙:
1. 유저에게 보이는 숫자(금액·기간·절감액·비율)는 이번 호출의 도구 결과에 있는 숫자만 쓴다. 암산하지 말고 calculate 로 계산한다.
2. 거래내역만으로 알 수 없는 것(이체 용도, 계속 쓸지, 정산 여부)은 추측하지 말고 묻는다. 이미 알 수 있는 것은 묻지 않는다.
3. 유저가 지키고 싶다고 한 소비(보호 지출)는 줄이라고 하지 않는다.
4. 해지·변경 결정은 유저가 한다. 선택지와 근거만 낸다.
5. 이중 집계를 조심한다: 카드대금 이체, 간편결제 충전, 대표 결제 뒤 정산 입금, 환불.
6. 개입할 가치가 없으면 제안하지 않는다."""

FEEDBACK = """지금은 {kind} 시간이다. 도구로 내역을 확인하고, 마지막에 아래 JSON 하나만 답한다 (앞뒤 설명 없이).
- 온보딩: 3개월 내역을 처음 본다. 패턴을 파악하고, 꼭 필요한 질문을 한 뒤, 쟁점·목표 달성 가능성·제안을 낸다.
- 주간: 지난 피드백 이후 바뀐 것을 보고 쟁점 상태를 갱신한다. 새 결제·가격 변화·이름 없는 이체를 확인한다.
- 월간: 한 달을 정리하고 목표 달성도를 도구 결과로 말한다.
질문은 ask_user 로 하고, 이번 피드백에서 최대 {max_q}개다. 제안은 최대 3개, 크기 순서로 낸다.
쟁점 유형: {issue_types}
쟁점 상태: {statuses}
제안 행동: {actions}.
제안 강도는 행동에 맞는 칸만 적고 나머지 칸은 뺀다:
- 줄이기: per_week (주 몇 회 줄일지)
- 저축 늘리기: amount (월 몇 원 더 넣을지) / 상한 정하기: amount (월 상한 금액)
- 바꾸기: to (무엇으로 바꿀지, 짧은 말)
- 목표 조정: deadline (새 마감일 YYYY-MM-DD), 금액도 바꾸면 amount (새 목표 금액)
JSON:
{{"issues": [{{"type": "쟁점 유형", "target": "대상 (가맹점·항목)", "status": "쟁점 상태",
              "evidence": ["근거 거래ID (도구 결과의 거래ID·tx)"], "summary": "한 줄"}}],
 "goal": {{"on_track": true | false | null, "summary": "한 줄"}},
 "suggestions": [{{"target": "대상", "action": "제안 행동", "per_week": 1, "amount": 30000, "to": "바꿀 것",
                  "deadline": "YYYY-MM-DD", "text": "유저에게 보일 문장"}}],
 "message": "유저에게 보낼 메시지 (숫자는 도구 결과에서만)"}}"""

UNDERSTAND = """지금은 유저 이해 시간이다. 유저가 방금 한 일(기록, 질문 답, 제안 반응)을 보고 유저 정보를 갱신한다.
유저에게 말하지 않는다. 도구는 없다. 아래 JSON 하나만 답한다 (앞뒤 설명 없이).
- 유저가 직접 말한 것은 사실, 짐작은 추정으로 구분하고 근거(기록ID·거래ID·피드백 날짜)를 적는다.
- patterns 와 questions_for_next 는 지금까지의 전체 목록으로 다시 적는다 (유효하지 않은 것은 뺀다).
JSON:
{"profile_updates": [{"field": "항목 이름", "value": "내용", "kind": "사실 | 추정", "evidence": "근거"}],
 "patterns": ["파악한 소비 패턴 (최대 10개)"],
 "questions_for_next": [{"target": "대상", "question": "다음 피드백에 물을 질문 (최대 5개)"}]}"""


def parse_json(text: str) -> dict | None:
    if not text or "{" not in text:
        return None
    try:
        out = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


class SingleAgent:
    name = "single"

    def __init__(self, llm: LLM, max_steps: int = 16, max_retries: int = 1):
        self.llm, self.model = llm, llm.model
        self.max_steps, self.max_retries = max_steps, max_retries
        self.memory: dict = {"fixed_answers": None, "profile": {}, "patterns": [], "questions_for_next": [],
                             "last_feedback": None, "suggestions": [], "qa": []}

    # -------------------------------------------------------------- 호출 방식 2가지
    def feedback(self, ctx: FeedbackCtx) -> dict:
        if ctx.fixed_answers:
            self.memory["fixed_answers"] = ctx.fixed_answers
        system = f"{COMMON}\n\n" + FEEDBACK.format(
            kind=KIND[ctx.kind], max_q=ctx.tools.max_questions, issue_types=", ".join(ISSUE_TYPES),
            statuses=", ".join(STATUSES), actions=", ".join(ACTIONS))
        day = dt.date.fromisoformat(ctx.today)
        user = (f"[오늘] {ctx.today} ({WEEKDAYS[day.weekday()]}) {KIND[ctx.kind]}. 다음 피드백: {ctx.next_feedback or '없음'}\n"
                f"[유저 정보 (요약 상태)]\n{self._memory_text()}")
        out, meta = self._loop(system, user, ctx.tools, ctx.verify)
        out = out or {"issues": [], "suggestions": [], "message": ""}
        self.memory["last_feedback"] = {"date": ctx.today, "issues": out.get("issues", []),
                                        "goal": out.get("goal")}
        return {**out, "_meta": meta}

    def understand(self, ctx: UnderstandCtx) -> dict:
        ev = ctx.event
        if ev["kind"] == "replies":            # 코드가 먼저 기록해 둔다 (LLM 이 놓쳐도 남게)
            if ev.get("fixed_answers"):
                self.memory["fixed_answers"] = ev["fixed_answers"]
            self.memory["qa"] += [{"date": ev["feedback_date"], **q} for q in ev["qa"]]
            self.memory["suggestions"] += [{"date": ev["feedback_date"], **r} for r in ev["reactions"]]
        system = f"{COMMON}\n\n{UNDERSTAND}"
        user = (f"[지금] {ctx.today}\n[유저 정보 (요약 상태)]\n{self._memory_text()}\n"
                f"[방금 일어난 일]\n{json.dumps(ev, ensure_ascii=False, default=str)}")
        out, meta = self._loop(system, user, None, None)
        out = out or {}
        self._apply(out, ev)
        return {**out, "_meta": meta}

    # -------------------------------------------------------------- 내부
    def _memory_text(self) -> str:
        m = self.memory
        view = {"고정 답변": m["fixed_answers"], "유저 정보": m["profile"], "소비 패턴": m["patterns"],
                "다음에 물을 질문": m["questions_for_next"], "지난 피드백": m["last_feedback"],
                "제안과 유저 반응": m["suggestions"][-15:], "질문과 답": m["qa"][-20:]}
        return json.dumps(view, ensure_ascii=False, default=str)

    def _apply(self, out: dict, ev: dict) -> None:
        ref = ev.get("record", {}).get("record_id") or ev.get("feedback_date")
        for u in out.get("profile_updates", []) or []:
            if isinstance(u, dict) and u.get("field"):
                self.memory["profile"][str(u["field"])] = {"value": u.get("value"), "kind": u.get("kind"),
                                                          "evidence": u.get("evidence") or ref}
        if isinstance(out.get("patterns"), list):
            self.memory["patterns"] = [str(x) for x in out["patterns"]][:10]
        if isinstance(out.get("questions_for_next"), list):
            self.memory["questions_for_next"] = out["questions_for_next"][:5]

    def _loop(self, system: str, user: str, tools, verify) -> tuple[dict | None, dict]:
        specs = tools.specs() if tools else []
        transcript: list[dict] = [{"role": "user", "text": user}]
        meta = {"llm_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "llm_seconds": 0.0,
                "retries": 0, "parse_errors": 0}
        retries = 0
        for _ in range(self.max_steps):
            st: Step = self.llm.step(system, transcript, specs)
            meta["llm_calls"] += 1
            meta["input_tokens"] += st.usage.get("input_tokens", 0)
            meta["output_tokens"] += st.usage.get("output_tokens", 0)
            meta["llm_seconds"] = round(meta["llm_seconds"] + st.seconds, 2)
            transcript.append({"role": "assistant", "text": st.text, "calls": st.calls, "native": st.native})
            if st.calls and tools:
                for c in st.calls:
                    meta["tool_calls"] += 1
                    transcript.append({"role": "tool", "call_id": c.id, "name": c.name,
                                       "output": tools.dump(tools.call(c.name, c.args))})
                continue
            out = parse_json(st.text)
            if out is None:
                meta["parse_errors"] += 1
                if retries < self.max_retries:
                    retries += 1
                    transcript.append({"role": "user", "text": "JSON 형식이 아니다. 정해진 JSON 하나만 다시 답하라."})
                    continue
                return None, meta
            if verify:
                v = verify(out)
                if not v.get("ok") and retries < self.max_retries:
                    retries += 1
                    meta["retries"] += 1
                    transcript.append({"role": "user", "text": (
                        f"메시지·제안 문장에 이번 도구 결과에 없는 숫자가 있다: {', '.join(v.get('bad', []))}. "
                        "도구(calculate 등)로 계산해 결과를 쓰거나, 그 숫자를 빼고 JSON 을 다시 답하라.")})
                    continue
            return out, meta
        return None, meta
