"""반응 규칙과 자기 기록 효과: 평가용 세 세계를 만든다 (docs/10 §3·§4, 초안).

  ① 서비스 없음      지금 데이터 그대로 (기준 세계)
  ② 앱만             ① + 자기 기록 효과 (`[[self_effects]]`: 기록·목표 확인이 계기. LLM과 상관없음)
  ③ 앱 + 피드백      ② + 제안 반응 (`[[responses]]`: 피드백의 제안을 수락하면 바뀜)

LLM 점수는 ③ − ②(피드백 효과)로 본다. ③ − ①은 서비스 전체 효과로 참고만 한다 (자기 기록 효과 크기가 가정이라서).
②·③은 평가할 때만 만든다. 공유 데이터(①)는 바뀌지 않는다. 바뀐 소비는 그 계기 다음 날부터만 나타난다.

제안 (LLM 출력의 suggestions 한 개)
  target (대상 이름), action (ACTIONS), 강도: per_week (줄이기: 주 몇 회), amount (저축 늘리기·상한 정하기: 월 원,
  목표 조정: 새 목표 금액), deadline (목표 조정: YYYY-MM-DD), to (바꾸기: 무엇으로)

반응 규칙 (TOML `[[responses]]`)
  id, target = {merchants = [가맹점 id], words = [대상 이름]}, facts = [숨은 사실 id], protected = 보호 지출이면 true
  accept = [{action, max_per_week | max_amount | min_amount | max_deadline | to = [단어]}]  받아들이는 행동과 한도
  reject = [{action | "*", why, to?, protected?}]  accept 에 안 맞을 때 거절하는 행동과 이유
  effect = 하나 또는 목록. 수락하면 바뀌는 것:
    {habit}               빈도를 제안한 주 n회만큼 줄임      {habit, factor}  빈도 × factor (고정)
    {habit, price}        가격 × price (예: 저가 테이크아웃)
    {monthly, change}     stop 해지 · add 금액 + 제안 금액 · set 금액 = amount(규칙에 적음)
    {new_monthly, day, category}  새 정기 이체 (금액 = 제안 금액, 예: 월급날 자동이체)
    {cap}                 이 용도 결제를 월 상한(제안 금액) 안에서만 함
    {reimburse, after_hour, pay_day}  이 습관 결제 중 after_hour 시 이후 것을 다음 달 pay_day 에 돌려받음
    {club = "prepaid"}    회식비를 미리 걷음 (못 받는 정산이 없어짐)
    {goal = true}         목표 금액·마감일을 제안대로 바꿈
  start_days (제안 며칠 뒤부터, 기본 1), not_before (이 날 전에는 안 바뀜), hold_days (habit 효과 유지 기간,
  없으면 계속), keep_after (유지 뒤 남는 폭, 0~1, 기본 1)

자기 기록 효과 (TOML `[[self_effects]]`, 크기는 가정)
  trigger = {records = [실제 용도], count, window_days}  window_days 안에 이 용도 기록이 count 번 쌓이면
          | {goal_behind = true}                       월간 시점(말일)에 끝난 달 순저축이 필요 월 저축액보다 적으면
  effect = {habits = [습관 id], ratio}  빈도 × (1 − ratio × 크기 배율), hold_days, keep_after (기본 0: 원래대로), why
"""
from __future__ import annotations

import copy
import datetime as dt

from spending_agent.demo.generator import (
    _active,
    _boost,
    _days,
    _last_day,
    _months,
    build_world,
    feedback_points,
    goal_progress,
    history_start,
)

# 초안: LLM에 주는 일반 목록
ACTIONS = ("해지", "줄이기", "끊기", "바꾸기", "상한 정하기", "저축 늘리기", "목표 조정", "정산 받기", "경비 청구", "수입 늘리기")
DECISIONS = ("수락", "거절", "보류")
STRENGTHS = ("per_week", "amount", "deadline", "to")     # 제안에서 반응·반영으로 넘기는 값
SELF_SCALES = {"없음": 0.0, "작게": 0.5, "중간": 1.0}   # 자기 기록 효과 크기 배율 (가정이라 바꿔 가며 돌린다)
EFFECT_KEYS = ("habit", "monthly", "new_monthly", "cap", "reimburse", "club", "goal")
DAY = dt.timedelta(days=1)
FOREVER = dt.date(2099, 12, 31)


def _effects(r: dict) -> list[dict]:
    eff = r.get("effect", [])
    return eff if isinstance(eff, list) else [eff]


def _entry(r: dict, a: dict) -> tuple[list[dict], dict]:
    """수락 항목의 효과와 설정 (항목에 effect·hold_days 등이 있으면 규칙 것보다 먼저)."""
    effects = _effects(a) if "effect" in a else _effects(r)
    keys = ("start_days", "not_before", "hold_days", "keep_after")
    return effects, {k: a.get(k, r.get(k)) for k in keys if k in a or k in r}


def _date(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


# ------------------------------------------------------------------ 형식 검사
def check(p: dict) -> None:
    """[[responses]]·[[self_effects]] 형식 검사. 틀리면 ValueError."""
    facts = {h["id"] for h in p.get("hidden_facts", [])}
    habits = {h["id"] for h in p.get("habits", [])}
    monthly = {m["id"] for m in p.get("monthly", [])}
    for key in ("responses", "self_effects"):
        ids = [r["id"] for r in p.get(key, [])]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{p['id']}: {key} id 중복")

    def fail(r, msg):
        raise ValueError(f"{p['id']} {r['id']}: {msg}")

    for r in p.get("responses", []):
        target = r.get("target", {})
        if not (target.get("merchants") or target.get("words")):
            fail(r, "target 에 merchants 나 words 가 없다")
        if bad := [m for m in target.get("merchants", []) if m not in p.get("merchants", {})]:
            fail(r, f"모르는 가맹점 {bad}")
        if bad := [f for f in r.get("facts", []) if f not in facts]:
            fail(r, f"모르는 숨은 사실 {bad}")
        for a in r.get("accept", []):
            if a["action"] not in ACTIONS:
                fail(r, f"모르는 행동 {a['action']}")
            if a["action"] == "줄이기" and a.get("max_per_week", 0) <= 0:
                fail(r, "줄이기에는 max_per_week(주 n회)가 있어야 한다")
            if a["action"] in ("저축 늘리기", "상한 정하기") and a.get("max_amount", 0) <= 0:
                fail(r, f"{a['action']}에는 max_amount 가 있어야 한다")
            if a["action"] == "목표 조정" and "max_deadline" not in a:
                fail(r, "목표 조정에는 max_deadline 이 있어야 한다")
        for a in r.get("reject", []):
            if a["action"] != "*" and a["action"] not in ACTIONS:
                fail(r, f"모르는 행동 {a['action']}")
            if not a.get("why"):
                fail(r, "거절에는 이유(why)가 있어야 한다")
            if "during" in a and (len(a["during"]) != 2 or _date(a["during"][0]) > _date(a["during"][1])):
                fail(r, "during 은 [시작일, 끝일]")
        for a in r.get("accept", []):                  # 주 n회 줄이기의 한도는 실제 주간 빈도보다 작아야 한다
            for eff in _entry(r, a)[0]:
                if "max_per_week" in a and "habit" in eff and not ({"factor", "price"} & eff.keys()) \
                        and eff["habit"] in habits and a["max_per_week"] >= weekly(p, eff["habit"]):
                    fail(r, f"max_per_week {a['max_per_week']} 가 {eff['habit']} 주간 빈도 "
                            f"{weekly(p, eff['habit']):.2f} 이상이다")
        if not r.get("accept") and not r.get("reject"):
            fail(r, "accept 도 reject 도 없다")
        if not r.get("accept") and "effect" in r:
            fail(r, "수락할 행동이 없는데 effect 가 있다")
        if any(not _entry(r, a)[0] for a in r.get("accept", [])):
            fail(r, "수락 항목에 effect 가 없다 (항목이나 규칙에 적는다)")
        for eff in _effects(r) + [e for a in r.get("accept", []) if "effect" in a for e in _effects(a)]:
            kinds = [k for k in EFFECT_KEYS if k in eff]
            if len(kinds) != 1:
                fail(r, f"effect 하나에는 {EFFECT_KEYS} 중 하나만 있어야 한다: {eff}")
            if "habit" in eff and eff["habit"] not in habits:
                fail(r, f"모르는 습관 {eff['habit']}")
            if "monthly" in eff and (eff["monthly"] not in monthly or eff.get("change") not in ("stop", "add", "set")
                                     or (eff["change"] == "set" and "amount" not in eff)):
                fail(r, "monthly effect 는 있는 고정 id 와 change = stop | add | set(amount) 가 필요하다")
            if "new_monthly" in eff and (eff["new_monthly"] in monthly or "day" not in eff or "category" not in eff):
                fail(r, "new_monthly 는 새 id 와 day, category 가 필요하다")
            if "reimburse" in eff and (eff["reimburse"] not in habits or "after_hour" not in eff or "pay_day" not in eff):
                fail(r, "reimburse 는 있는 습관 id 와 after_hour, pay_day 가 필요하다")
            if "club" in eff and (eff["club"] != "prepaid" or "club" not in p):
                fail(r, "club effect 는 prepaid 이고 페르소나에 club 규칙이 있어야 한다")
        if not 0 <= r.get("keep_after", 1) <= 1:
            fail(r, "keep_after 는 0~1")

    for s in p.get("self_effects", []):
        t, eff = s.get("trigger", {}), s.get("effect", {})
        if "records" in t:
            if t.get("count", 0) < 1 or t.get("window_days", 0) < 1:
                fail(s, "records 계기에는 count, window_days 가 있어야 한다")
        elif t.get("goal_behind") is not True:
            fail(s, "trigger 는 records 또는 goal_behind = true")
        if not eff.get("habits") or (bad := [h for h in eff["habits"] if h not in habits]):
            fail(s, f"effect.habits 가 비었거나 모르는 습관이 있다 {eff.get('habits')}")
        if not 0 < eff.get("ratio", 0) < 1:
            fail(s, "effect.ratio 는 0~1")
        if s.get("hold_days", 0) < 1 or not 0 <= s.get("keep_after", 0) <= 1:
            fail(s, "hold_days ≥ 1, keep_after 0~1")
        if not s.get("why"):
            fail(s, "why(이 효과를 둔 이유)가 있어야 한다")


# ------------------------------------------------------------------ 반응
def _names(p: dict, r: dict) -> list[str]:
    out = list(r["target"].get("words", []))
    for m in r["target"].get("merchants", []):
        out += [p["merchants"][m]["name"], p["merchants"][m]["card_name"]]
    return out


def _matches(p: dict, r: dict, target: str) -> bool:
    t = target.strip()
    return len(t) >= 2 and any(n in t or t in n for n in _names(p, r))


def _to_ok(entry: dict, suggestion: dict) -> bool:
    """entry 에 to(단어 목록)가 있으면 제안의 to 가 그중 하나를 담아야 한다."""
    return "to" not in entry or any(w in str(suggestion.get("to", "")) for w in entry["to"])


def _within(a: dict, s: dict) -> bool:
    if "max_per_week" in a and not 0 < s.get("per_week", 0) <= a["max_per_week"]:
        return False
    if "max_amount" in a and not 0 < s.get("amount", 0) <= a["max_amount"]:
        return False
    if "min_amount" in a and s.get("amount", a["min_amount"]) < a["min_amount"]:
        return False
    if "max_deadline" in a and ("deadline" not in s or _date(s["deadline"]) > _date(a["max_deadline"])):
        return False
    return _to_ok(a, s)


def _during(a: dict, day: dt.date | None) -> bool:
    return day is not None and _date(a["during"][0]) <= _date(day) <= _date(a["during"][1])


def react(p: dict, suggestion: dict, day: dt.date | None = None) -> dict:
    """제안 {target, action, per_week?, amount?, deadline?, to?} → {decision, response, why, protected?, 강도...}.
    대상이 규칙 하나에만 맞아야 판단한다 (여러 개면 무엇을 말하는지 몰라 보류).
    day(제안한 날)를 주면 기간이 붙은 거절(during, 예: 시험기간)을 수락보다 먼저 본다. 안 주면 그 거절은 건너뛴다."""
    action = suggestion["action"]
    hits = [r for r in p.get("responses", []) if _matches(p, r, suggestion["target"])]
    if len(hits) > 1:                                  # 여러 규칙에 걸리면 이 행동을 다루는 규칙으로 좁힌다
        hits = [r for r in hits if any(a["action"] in (action, "*") for a in r.get("accept", []) + r.get("reject", []))]
    if len(hits) != 1:
        return {"decision": "보류", "response": None, "why": "맞는 대상 없음" if not hits else "대상이 여러 개"}
    r = hits[0]

    def rejected(a):
        return {"decision": "거절", "response": r["id"], "why": a["why"],
                "protected": a.get("protected", r.get("protected", False))}

    for a in r.get("reject", []):
        if "during" in a and a["action"] in (action, "*") and _to_ok(a, suggestion) and _during(a, day):
            return rejected(a)
    for i, a in enumerate(r.get("accept", [])):
        if a["action"] == action and _within(a, suggestion):
            strength = {k: suggestion[k] for k in STRENGTHS if k in suggestion}
            rid = f"{r['id']}#{i}" if "effect" in a else r["id"]   # 항목마다 효과가 다르면 몇 번째 항목인지 붙인다
            return {"decision": "수락", "response": rid, "why": None, **strength}
    for a in r.get("reject", []):
        if "during" not in a and a["action"] in (action, "*") and _to_ok(a, suggestion):
            return rejected(a)
    return {"decision": "보류", "response": r["id"], "why": "이 행동에 대한 규칙 없음"}


# ------------------------------------------------------------------ 반영
def weekly(p: dict, habit_id: str) -> float:
    """습관의 주간 빈도 기댓값 (온보딩 기간, 기간 배율 포함). '주 n회 줄이기'를 빈도 배율로 바꿀 때 쓴다."""
    h = next(x for x in p["habits"] if x["id"] == habit_id)
    days = list(_days(history_start(p), p["d0"]))
    return sum(min(1.0, h["prob"][d.weekday()] * _boost(h, d)) for d in days) / (len(days) / 7)


def _change(items: list[dict], tag: str, start: dt.date, factor: float, hold_days: int | None,
            keep_after: float) -> None:
    """기간별 배율 목록(boosts·price_changes)에 start 부터 factor 를 넣는다. 같은 tag 의 앞선 변화는
    start 전날로 끝낸다 (다시 받으면 새로 센다). 유지 기간 뒤에는 바뀐 폭의 keep_after 만 남는다."""
    for b in [b for b in items if b.get("tag") == tag]:
        if b["from"] >= start:
            items.remove(b)
        elif b["until"] >= start:
            b["until"] = start - DAY
    hold_end = start + DAY * (hold_days - 1) if hold_days else FOREVER
    items.append({"from": start, "until": hold_end, "factor": factor, "tag": tag})
    if hold_end < FOREVER and keep_after > 0:
        items.append({"from": hold_end + DAY, "until": FOREVER, "factor": 1 - (1 - factor) * keep_after, "tag": tag})


def _habit(q: dict, habit_id: str) -> dict:
    return next(h for h in q["habits"] if h["id"] == habit_id)


def apply(p: dict, accepted: list[dict]) -> dict:
    """수락한 제안 [{response, day(제안한 날), 강도...}] → 바뀐 페르소나 (원본은 그대로)."""
    q = copy.deepcopy(p)
    q["_modified"] = True          # ②·③ 세계: 제안으로 근거 거래가 안 생긴 쟁점은 오류가 아니라 '막은 쟁점'으로 남긴다
    rules = {r["id"]: r for r in q.get("responses", [])}
    end = q["d0"] + dt.timedelta(days=q["live_days"])
    for acc in sorted(accepted, key=lambda a: _date(a["day"])):
        rid, _, idx = acc["response"].partition("#")
        r = rules[rid]
        effects, s = _entry(r, r["accept"][int(idx)] if idx else {})
        start = _date(acc["day"]) + DAY * s.get("start_days", 1)
        if "not_before" in s:
            start = max(start, _date(s["not_before"]))
        hold, keep, tag = s.get("hold_days"), s.get("keep_after", 1.0), f"response:{acc['response']}"
        for eff in effects:
            if "habit" in eff and "price" in eff:
                _change(_habit(q, eff["habit"]).setdefault("price_changes", []), tag, start, eff["price"], hold, keep)
            elif "habit" in eff:                       # 주 n회 줄이기 → 빈도 × (1 − n / 주간 빈도), 또는 고정 배율
                factor = eff.get("factor", max(0.0, 1 - acc.get("per_week", 0) / weekly(p, eff["habit"])))
                _change(_habit(q, eff["habit"]).setdefault("boosts", []), tag, start, factor, hold, keep)
            elif "monthly" in eff:
                rule = next(m for m in q["monthly"] if m["id"] == eff["monthly"])
                base = next(m for m in p["monthly"] if m["id"] == eff["monthly"])
                if eff["change"] == "stop":            # 해지: 그날부터 결제 안 됨
                    rule["until"] = min(rule.get("until", FOREVER), start - DAY)
                    continue
                for y, m in _months(start, end):       # add: 원래 금액 + 제안 금액, set: 규칙의 금액
                    if dt.date(y, m, min(rule["day"], _last_day(y, m))) >= start:
                        ym = f"{y}-{m:02d}"
                        old = base.get("amount_by_month", {}).get(ym, base.get("amount", 0))
                        rule.setdefault("amount_by_month", {})[ym] = (old + acc["amount"] if eff["change"] == "add"
                                                                       else eff["amount"])
            elif "new_monthly" in eff:                 # 새 정기 이체 (다시 받으면 새 금액으로)
                q["monthly"] = [m for m in q["monthly"] if m["id"] != eff["new_monthly"]]
                q["monthly"].append({"id": eff["new_monthly"], "kind": "transfer_out", "day": eff["day"],
                                     "amount": acc["amount"], "category": eff["category"], "from": start,
                                     "hours": eff.get("hours", [9, 12])})
            elif "cap" in eff:
                q["caps"] = [c for c in q.get("caps", []) if c["category"] != eff["cap"]]
                q["caps"].append({"category": eff["cap"], "from": start, "amount": acc["amount"]})
            elif "reimburse" in eff:
                if not any(x["habit"] == eff["reimburse"] for x in q.get("reimbursements", [])):
                    q.setdefault("reimbursements", []).append(
                        {"habit": eff["reimburse"], "from": start, "after_hour": eff["after_hour"],
                         "pay_day": eff["pay_day"], "export": eff.get("export")})
            elif "club" in eff:
                q["club"]["prepaid_from"] = min(q["club"].get("prepaid_from", FOREVER), start)
            elif "goal" in eff:
                goal = q["onboarding"]["fixed_answers"]["goal"]
                goal["deadline"] = _date(acc["deadline"])
                goal["amount"] = acc.get("amount", goal["amount"])
    return q


# ------------------------------------------------------------------ 자기 기록 효과
def _next_trigger(p: dict, w, after: dt.date, fired: list[dict]) -> tuple[dict, dt.date] | None:
    """after 다음 날부터 가장 먼저 걸리는 자기 기록 효과. 효과가 이어지는 동안에는 같은 효과가 다시 걸리지 않는다."""
    d0, end = p["d0"], p["d0"] + dt.timedelta(days=p["live_days"])
    by_id = {e.id: e for e in w.events}
    recs = sorted((_date(str(r["written_at"])[:10]), by_id[r["about_event"]].category)
                  for r in w.truth["facts"]["records"] if r.get("about_event") in by_id)
    goal_days = [dt.date.fromisoformat(pt["date"]) for pt in feedback_points(d0, end) if pt["kind"] == "monthly"]
    best = None
    for s in p.get("self_effects", []):
        last = max((f["day"] for f in fired if f["id"] == s["id"]), default=None)
        free_from = after + DAY                        # 효과는 last+1 ~ last+hold_days. 끝난 다음 날부터 다시 걸릴 수 있다
        if last:
            free_from = max(free_from, last + DAY * (s["hold_days"] + 1))
        t = s["trigger"]
        if "records" in t:                             # 마지막으로 걸린 뒤의 기록만 센다
            days = [d for d, c in recs if c in t["records"] and (last is None or d > last)]
            hit = next((d for d in days if d >= free_from
                        and sum(d - DAY * (t["window_days"] - 1) <= x <= d for x in days) >= t["count"]), None)
        else:
            hit = None
            for d in goal_days:
                g = goal_progress(w.persona, w.events, d0, d) if d >= free_from else None
                if g and g["months_done"] and g["months_done"][-1]["net"] < g["monthly_required"]:
                    hit = d
                    break
        if hit and hit + DAY < end and (best is None or hit < best[1]):
            best = (s, hit)
    return best


def with_self(p: dict, scale: float, accepted: list[dict] = ()) -> tuple[dict, list[dict]]:
    """제안 반영(apply) 뒤 자기 기록 효과를 날짜 순서대로 쌓는다. → (바뀐 페르소나, 걸린 효과 [{id, day}])
    계기가 걸린 날까지의 거래·기록은 다음 효과를 더해도 바뀌지 않으므로 앞에서부터 하나씩 정하면 된다."""
    q, fired, after = apply(p, list(accepted)), [], p["d0"] - DAY
    if scale <= 0:
        return q, fired
    while hit := _next_trigger(p, build_world(q), after, fired):
        s, day = hit
        for h in s["effect"]["habits"]:
            _change(_habit(q, h).setdefault("boosts", []), f"self:{s['id']}", day + DAY,
                    1 - s["effect"]["ratio"] * scale, s["hold_days"], s.get("keep_after", 0.0))
        fired.append({"id": s["id"], "day": day})
        after = day - DAY                              # 같은 날 다른 효과도 걸릴 수 있다 (바뀌는 건 다음 날부터)
    return q, fired


# ------------------------------------------------------------------ 세 세계 비교
def _habit_spend(q: dict, events, months: set[str], upto: dt.date) -> int:
    """습관 지출의 실제 합 (환불 뺌). goal_progress 와 같은 기준 (D0 이후, upto 까지 들어온 것, 거래한 달)."""
    habits, by_id, out = {h["id"] for h in q.get("habits", [])}, {e.id: e for e in events}, 0
    for e in events:
        if e.at.date() < q["d0"] or e.visible_on > upto or f"{e.at:%Y-%m}" not in months:
            continue
        src = by_id.get(e.ref, e) if e.kind == "cancel" else e
        if src.kind == "purchase" and src.id.rsplit("-", 1)[0] in habits:
            out += -e.amount if e.kind == "cancel" else e.amount
    return out


def _habit_expected(q: dict, months: set[str], upto: dt.date) -> float:
    """습관 지출의 기댓값: 날마다 (발생 확률 × 평균 금액 × 가격 배율 × (1 − 반품 확률)). 난수 운을 뺀 값."""
    total = 0.0
    for h in q.get("habits", []):
        mean = sum(h["amount"]) / 2 * (1 - h.get("cancel_prob", 0))
        for d in _days(q["d0"], upto + DAY):
            if f"{d:%Y-%m}" not in months or not _active(h, d):
                continue
            price = 1.0
            for c in h.get("price_changes", []):
                price *= c["factor"] if c["from"] <= d <= c["until"] else 1.0
            total += min(1.0, h["prob"][d.weekday()] * _boost(h, d)) * mean * price
    return total


def compare(p: dict, accepted: list[dict], scale: float = SELF_SCALES["중간"], upto: dt.date | None = None) -> dict:
    """세 세계의 끝난 달 (수입 − 지출) 합. 기준은 generator.goal_progress 와 같다 (저축 이체는 지출이 아님).
    목표를 조정한 제안이 있으면 ③의 필요액은 바뀐 목표로 계산한다.
    expected_*: 습관 지출만 실제 거래 대신 기댓값으로 바꾼 값. 습관 결제 한 건이 큰 페르소나(P5 쇼핑)는 어느 결제가
    빠지느냐에 따라 실제 값이 크게 흔들리므로, LLM 끼리 비교할 때는 이 값을 본다 (나머지는 실제 값 그대로)."""
    upto = upto or p["d0"] + dt.timedelta(days=p["live_days"] - 1)
    app, app_fired = with_self(p, scale)
    served, served_fired = with_self(p, scale, accepted)
    gs, ns, es = [], [], []
    for q in (p, app, served):
        events = build_world(q).events
        g = goal_progress(q, events, q["d0"], upto)
        months = {m["month"] for m in g["months_done"]}
        n = sum(m["net"] for m in g["months_done"])
        gs.append(g)
        ns.append(n)
        es.append(round(n + _habit_spend(q, events, months, upto) - _habit_expected(q, months, upto)))
    (n1, n2, n3), (e1, e2, e3) = ns, es
    return {"through": upto.isoformat(), "scale": scale, "without": n1, "app": n2, "served": n3,
            "feedback_effect": n3 - n2, "self_effect": n2 - n1, "total": n3 - n1,
            "expected_feedback_effect": e3 - e2, "expected_self_effect": e2 - e1,
            "required_by_now": gs[0]["required_by_now"], "served_required_by_now": gs[2]["required_by_now"],
            "fired": {"app": app_fired, "served": served_fired},
            "calc": (f"끝난 달 (수입 − 지출) 합: 없음 {n1:,} / 앱만 {n2:,} / 앱+피드백 {n3:,}. "
                     f"피드백 효과 {n3:,} − {n2:,} = {n3 - n2:,}, 자기 기록 효과 {n2:,} − {n1:,} = {n2 - n1:,}. "
                     f"습관 지출을 기댓값으로 바꾸면 피드백 효과 {e3 - e2:,}, 자기 기록 효과 {e2 - e1:,}")}
