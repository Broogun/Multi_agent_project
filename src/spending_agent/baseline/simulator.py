"""규칙형 유저 시뮬레이터 (docs/10 §6). LLM 이 아니다.

- 고정 질의 답변: truth.user_sim.fixed_answers 를 온보딩 때 유저가 답한 것으로 넘긴다. 목표는 들어 있지 않다.
- 질문: 질문의 대상(target)과 문장에서 키워드를 뽑아 숨은 사실과 맞춰 보고, 하나로 정해지면 준비된 답을 돌려준다.
  안 맞거나 여러 개가 비슷하면 "잘 모르겠어요".
  숨은 사실의 키워드 = 주제(topic)의 날짜·금액·낱말 + 그 사실을 쓰는 반응 규칙의 가맹점 이름·대상 낱말.
  페르소나 파일의 hidden_facts 에 targets = [...] 가 있으면 강한 키워드로 더한다.
  목표 대화 사실(about = goal: 무엇·얼마·언제, goal_flex: 바꿀 여지)은 targets 만 강한 키워드로 쓴다. 점수가 같으면
  질문에 '미루·낮추' 같은 말이 있으면 goal_flex, 없으면 goal 로 정한다 ("목표가 뭐예요?"가 모르겠어요로 끝나지 않게).
- 목표 생성 버튼 (create_goal): 에이전트의 목표안을 유저가 확인하고, 받아들일 수 있는 범위(responses.goal_range)를
  벗어난 칸만 고친 뒤 만든다. 목표안이 없거나 형식이 틀리면 유저가 원하는 목표를 직접 넣는다.
- 제안: demo.responses.react (반응 규칙)로 수락·거절·보류. 강도 4가지(responses.STRENGTHS)와 제안한 날을 넘긴다. 거절 이유는 에이전트에게 넘기지 않는다 (숨은 사실이 새지 않게).
"""
from __future__ import annotations

import datetime as dt
import re

from ..demo import responses

UNKNOWN = "잘 모르겠어요."
FLEX_WORDS = ("미루", "미룰", "미뤄", "늦추", "늦출", "늦춰", "조정", "여지", "낮추", "낮출", "낮춰", "줄여도", "바꿀 수", "바꿔도")
STRONG, WEAK, MIN_SCORE = 3, 1, 2
# 주제 문장에서 뜻이 약한 낱말 (어느 사실에나 붙을 수 있음)
STOP = {"이어지는", "원대와", "두", "개", "첫", "결제", "원", "이후", "증가", "감소", "자주", "가는", "이유", "잦음",
        "들어오는", "뒤에", "소액", "가끔", "나가는", "없음", "바꿀", "여지", "이랑", "그리고", "금액", "늘리기"}


def _amount_tokens(n: int) -> set[str]:
    """200,000 → {"200000", "20만"}. 만 단위는 내림이라 "13만 원대" 같은 표현과도 맞는다."""
    return {str(n), f"{n // 10000}만"} if n >= 10000 else {str(n)}


def tokens(text: str) -> set[str]:
    """날짜(M/D, D일), 금액(숫자, N만), 낱말(2자 이상)로 나눈다."""
    t = text.replace("→", " ")
    out: set[str] = set()
    for mo, d in re.findall(r"(\d{1,2})월\s*(\d{1,2})일", t):
        out |= {f"{int(mo)}/{int(d)}", f"{int(d)}일"}
    for mo, d in re.findall(r"(?<![\d,])(\d{1,2})/(\d{1,2})(?![\d,])", t):
        out |= {f"{int(mo)}/{int(d)}", f"{int(d)}일"}
    out |= {f"{int(d)}일" for d in re.findall(r"(?<![\d/])(\d{1,2})일", t)}
    out |= {f"{int(m)}월" for m in re.findall(r"(?<![\d/])(\d{1,2})월", t)}
    out |= {f"{int(n)}만" for n in re.findall(r"(\d+)\s*만", t)}
    for n in re.findall(r"\d[\d,]{3,}", t):
        out |= _amount_tokens(int(n.replace(",", "")))
    for w in re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]+", t):
        if w not in STOP:
            out.add(w)
    return out


class Simulator:
    def __init__(self, persona: dict, user_sim: dict):
        self.p, self.sim = persona, user_sim
        self.keys: dict[str, tuple[set[str], set[str]]] = {}
        merchants = persona.get("merchants", {})
        names = {m: {x["name"], x["card_name"]} for m, x in merchants.items()}
        self.about = {h["id"]: h.get("about") for h in user_sim.get("hidden_facts", [])}
        for h in user_sim.get("hidden_facts", []):
            strong = set(h.get("targets", []))
            if h.get("about"):                    # 목표 대화 사실은 targets 만 (규칙 낱말 '목표' 등이 다른 사실과 겹친다)
                self.keys[h["id"]] = (strong, tokens(h["topic"]))
                continue
            for r in persona.get("responses", []):
                if h["id"] in r.get("facts", []):
                    strong |= set(r["target"].get("words", []))
                    for m in r["target"].get("merchants", []):
                        strong |= names.get(m, set())
            strong |= {n for ns in names.values() for n in ns if n in h["topic"]}
            self.keys[h["id"]] = (strong, tokens(h["topic"]))

    def fixed_answers(self) -> dict:
        return self.sim["fixed_answers"]

    def match(self, target: str, question: str) -> tuple[str | None, int]:
        text = f"{target} {question}"
        toks = tokens(text)
        scores = {}
        for fid, (strong, weak) in self.keys.items():
            s = sum(STRONG for k in strong if k in text) + sum(WEAK for k in weak if k in toks)
            if s:
                scores[fid] = s
        if not scores:
            return None, 0
        best = max(scores.values())
        top = [f for f, s in scores.items() if s == best]
        if len(top) > 1:                          # 목표 질문끼리·목표와 다른 사실이 비기면 질문의 말로 고른다
            want = "goal_flex" if any(w in text for w in FLEX_WORDS) else "goal"
            top = [f for f in top if self.about.get(f) == want] or top
        return (top[0], best) if best >= MIN_SCORE and len(top) == 1 else (None, best)

    def answer(self, target: str, question: str) -> dict:
        fid, score = self.match(target, question)
        facts = {h["id"]: h for h in self.sim.get("hidden_facts", [])}
        return {"answer": facts[fid]["answer"] if fid else UNKNOWN, "fact": fid, "score": score}

    def create_goal(self, proposal) -> dict:
        """목표 생성 버튼. → {goal: {name, amount, deadline(YYYY-MM-DD), current_saved}, edits: [...], source}
        유저는 범위(responses.goal_range) 밖인 칸만 고친다: 금액은 [최소, 원하는 금액], 마감은 오늘 뒤 ~ 최대 마감,
        모아 둔 돈은 실제 값(고정 답변). source = agent (목표안으로 만듦) | user (목표안이 없어 유저가 직접 넣음)."""
        intent, saved = self.sim["goal_intent"], self.sim["fixed_answers"]["saved"]
        rng = responses.goal_range(self.p)
        want = {"name": intent["name"], "amount": intent["amount"], "deadline": _iso_date(str(intent["deadline"])),
                "current_saved": saved}
        g = proposal if isinstance(proposal, dict) else {}
        amount, deadline = _number(g.get("amount")), _iso_date(g.get("deadline"))
        if amount is None or deadline is None or deadline <= self.p["d0"].isoformat():
            return {"goal": want, "edits": [{"field": "*", "from": proposal, "to": None, "why": "목표안이 없거나 형식이 틀림"}],
                    "source": "user"}
        out = {"name": _text(g.get("name")) or intent["name"], "amount": int(amount), "deadline": deadline,
               "current_saved": _number(g.get("current_saved"))}
        edits = []

        def fix(field, value, why):
            edits.append({"field": field, "from": out[field], "to": value, "why": why})
            out[field] = value
        if out["amount"] > rng["max_amount"]:
            fix("amount", rng["max_amount"], "원하는 금액보다 크다")
        elif rng["min_amount"] is not None and out["amount"] < rng["min_amount"]:
            fix("amount", rng["min_amount"], "이 금액 아래로는 안 된다")
        elif out["amount"] <= saved:
            fix("amount", intent["amount"], "이미 모은 돈보다 작다")
        if out["deadline"] > rng["max_deadline"].isoformat():
            fix("deadline", rng["max_deadline"].isoformat(), "이보다 늦출 수 없다")
        if out["current_saved"] != saved:
            fix("current_saved", saved, "실제로 모아 둔 돈")
        return {"goal": out, "edits": edits, "source": "agent"}

    def react(self, suggestion: dict, day: dt.date | None = None) -> dict:
        """day = 제안한 날. 기간이 붙은 거절(예: P2 시험기간 카페 줄이기)은 이 날짜로 본다."""
        s = {"target": str(suggestion.get("target", "")), "action": str(suggestion.get("action", "")),
             **strengths(suggestion)}
        return responses.react(self.p, s, day)


def _number(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v):
    return v.strip() if isinstance(v, str) and v.strip() else None


def _iso_date(v):
    try:
        return dt.date.fromisoformat(v.strip()).isoformat() if isinstance(v, str) else None
    except ValueError:
        return None


# 제안 강도 (responses.STRENGTHS) 마다 형식 검사. 형식이 틀린 값은 없는 것으로 본다 (그러면 대개 수락되지 않는다).
STRENGTH_FORMATS = {"per_week": _number, "amount": _number, "deadline": _iso_date, "to": _text}


def strengths(suggestion: dict) -> dict:
    """제안에서 강도만 골라 낸다: per_week·amount 는 숫자, deadline 은 YYYY-MM-DD, to 는 글자."""
    out = {k: STRENGTH_FORMATS[k](suggestion.get(k)) for k in responses.STRENGTHS}
    return {k: v for k, v in out.items() if v is not None}
