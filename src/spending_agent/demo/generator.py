"""페르소나(TOML) → 이벤트 → 통합 거래내역 행 → 배치.

입력 경로 B 가정: 유저가 자산관리 앱(마이데이터)에서 내보낸 통합 거래내역 파일을 올린다.
카드·계좌·간편결제를 합치는 일은 앱이 이미 했다고 보고, 이벤트 1건 = 내역 1행으로 만든다.
통합 내역에도 남는 현실 문제는 그대로 넣는다: 이름 없는 이체, 앱 자동 분류(틀릴 수 있음),
더치페이 정산 입금, 현금 인출, 환불 행, 신용카드 대금 출금, 할부, 늦게 들어오는 거래.

난수는 (페르소나, 날짜, 규칙)마다 따로 고정한다. 그래서 같은 페르소나는 항상 같은 결과가 나오고,
운영 기간을 늘려도 이전 날짜의 거래는 바뀌지 않는다.

운영 단계 (docs/10): 거래는 매일 쌓이고, 유저 기록(유저 이벤트)마다 유저 이해가 돈다.
유저에게 말하는 것은 피드백 시점(온보딩, 일요일 저녁 주간, 말일 저녁 월간)뿐이다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import tomllib
from dataclasses import dataclass
from pathlib import Path

PERSONA_DIR = Path(__file__).parent / "personas"
EXPORT_COLUMNS = ("거래ID", "날짜", "시간", "타입", "대분류", "소분류", "내용", "금액", "결제수단", "메모")
DAY = dt.timedelta(days=1)
CARD_CHANNELS = ("card", "easypay_card")

# 상대 이름이 없는 이체·현금은 앱이 용도를 알 수 없다는 가정 → (타입, 대분류, 소분류, 내용)
NON_PURCHASE_ROWS = {
    "transfer_in": ("수입", "미분류", "", "입금"),
    "transfer_out": ("이체", "미분류", "", "출금"),
    "atm": ("지출", "현금", "ATM출금", "ATM 출금"),
}

FEEDBACK_HOUR = 21   # 주간·월간 피드백은 저녁에 돈다. 이 시각 뒤에 쓴 기록은 다음 피드백에서 다룬다
# 쟁점 유형 (초안, docs/10 §5). 페르소나별 정답이 아니라 어느 유저에게나 쓸 수 있는 일반 목록이다
ISSUE_TYPES = ("정기결제 중복", "정기결제 가격 변경", "새 정기결제", "해지 안 된 정기결제", "습관 지출 증가",
               "요일·시간 집중", "일시적 변화", "일회성 큰 지출", "할부", "대표결제·정산", "이름 없는 이체",
               "앱 분류 확인", "현금 사용", "수입 변화", "정기 이체 중단", "잔액 부족", "목표 진척")
# 시점별 쟁점 상태 (서비스 없는 기준 세계). 아직: 신호 없음 / 선택: 신호가 생겼지만 짚지 않아도 됨 /
# 필수: 짚어야 함 / 종료: 끝남 (앞에서 짚었다면 해결로 갱신해야 함)
STATUSES = ("아직", "선택", "필수", "종료")


@dataclass
class Event:
    """실제로 일어난 일 1건 (정답). 통합 내역에는 앱이 본 모습으로만 나간다."""
    id: str
    at: dt.datetime
    kind: str               # purchase | cancel | transfer_in | transfer_out | atm | charge | card_bill
    amount: int
    channel: str            # card | easypay_card | easypay_balance | account
    category: str           # 실제 용도 (truth 전용)
    merchant: str | None = None
    delay_days: int | None = None
    ref: str | None = None  # cancel → 원래 이벤트 id
    group: str | None = None  # 정산(1/n) 묶음 id
    installment: int = 0    # 할부 개월 (신용카드)
    export: dict | None = None  # 이체 행을 앱이 다르게 보여줄 때 (예: 급여)

    @property
    def visible_on(self) -> dt.date:
        return self.at.date() + dt.timedelta(days=self.delay_days or 0)


@dataclass
class World:
    persona: dict
    start: dt.date
    d0: dt.date
    end: dt.date                          # 운영 마지막 날의 다음 날
    events: list[Event]
    rows: dict[str, dict]                 # event_id → 통합 내역 행
    batches: dict[str, list[dict]]        # "onboarding" | YYYY-MM-DD → 행
    records: dict[str, list[dict]]        # YYYY-MM-DD → 유저 기록
    truth: dict
    feedback: list[dict]                  # 피드백 시점 [{date, kind}] (에이전트도 알아도 되는 일정)


def load_persona(pid: str) -> dict:
    with open(PERSONA_DIR / f"{pid}.toml", "rb") as f:
        return tomllib.load(f)


def personas() -> list[str]:
    return sorted(f.stem for f in PERSONA_DIR.glob("*.toml"))


def history_start(p: dict) -> dt.date:
    """온보딩 내역 첫날 (D0 의 history_months 달 전)."""
    d0 = p["d0"]
    y, m = divmod(d0.year * 12 + d0.month - 1 - p.get("history_months", 3), 12)
    return dt.date(y, m + 1, min(d0.day, 28))


def build_world(p: dict) -> World:
    d0 = p["d0"]
    start, end = history_start(p), d0 + dt.timedelta(days=p["live_days"])
    credit = p["accounts"]["card"].get("kind") == "신용"

    groups: dict[str, dict] = {}
    events = (_monthly(p, start, end, groups) + _habits(p, start, end) + _irregular(p, start, end)
              + _club_rule(p, start, end, groups) + _scripted(p, groups))
    events = _caps(p, events)                 # 아래 둘은 반응 규칙(서비스 있음 세계)으로만 생긴다
    events += _reimbursements(p, events)
    by_id = {e.id: e for e in events}
    if len(by_id) != len(events):
        raise ValueError(f"{p['id']}: 이벤트 id 중복")
    for e in events:
        if e.kind == "cancel":
            o = by_id[e.ref]
            e.amount, e.channel, e.category, e.merchant = o.amount, o.channel, o.category, o.merchant
        if e.delay_days is None:
            e.delay_days = 1 if e.at.hour >= p["sync"]["late_hour"] else 0
    events = [e for e in events if e.at.date() < end]
    events = _add_charges(p, events, credit)
    bills: list[dict] = []
    if credit:
        events += _card_bills(p, events, start, end, bills)
    events.sort(key=_order)
    by_id = {e.id: e for e in events}

    balance = _check_balances(p, events, by_id, credit)
    rows = {e.id: _export_row(p, e, by_id) for e in events}
    for b in bills:
        b["bill_tx"] = rows[b.pop("event_id")]["거래ID"]
    batches = _batches(events, rows, d0, end)
    points = feedback_points(d0, end)
    records, record_meta = _records(p, events, by_id, rows, points, d0, end)
    issues = _issues(p, by_id, rows, points)
    truth = _truth(p, events, by_id, rows, groups, record_meta, balance, bills, start, d0, end, points, issues)
    return World(p, start, d0, end, events, rows, batches, records, truth, points)


# ---------------------------------------------------------------- 이벤트 생성
def _rng(p: dict, key: str) -> random.Random:
    return random.Random(f"{p['id']}:{p['seed']}:{key}")


def _days(start: dt.date, end: dt.date):
    d = start
    while d < end:
        yield d
        d += DAY


def _months(start: dt.date, end: dt.date):
    y, m = start.year, start.month
    while dt.date(y, m, 1) < end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _add_months(y: int, m: int, n: int) -> str:
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y}-{m + 1:02d}"


def _last_day(y: int, m: int) -> int:
    return ((dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)) - DAY).day


def _active(rule: dict, day: dt.date) -> bool:
    return ("from" not in rule or day >= rule["from"]) and ("until" not in rule or day <= rule["until"])


def _boost(rule: dict, day: dt.date) -> float:
    """빈도 배율: 기간(from~until) 또는 매달 날짜 범위(days_of_month)."""
    factor = 1.0
    for b in rule.get("boosts", []):
        if "days_of_month" in b:
            hit = b["days_of_month"][0] <= day.day <= b["days_of_month"][1]
        else:
            hit = b["from"] <= day <= b["until"]
        factor *= b["factor"] if hit else 1.0
    return factor


def _price(rule: dict, day: dt.date, amount: int) -> int:
    """가격 변화 (price_changes: 반응 규칙으로만 생김, 예: 저가 테이크아웃으로 바꾸기). 난수는 쓰지 않는다."""
    factor = 1.0
    for c in rule.get("price_changes", []):
        factor *= c["factor"] if c["from"] <= day <= c["until"] else 1.0
    if factor == 1.0:
        return amount
    step = rule.get("step", 100)
    return max(step, round(amount * factor / step) * step)


def _time(rng: random.Random, day: dt.date, hours: list[int]) -> dt.datetime:
    minute = rng.randint(hours[0] * 60, hours[1] * 60 - 1)
    return dt.datetime.combine(day, dt.time(minute // 60, minute % 60, rng.randint(0, 59)))


def _amount(rng: random.Random, spec: list[int], step: int = 100) -> int:
    return rng.randint(spec[0] // step, spec[1] // step) * step


def _channel(rng: random.Random, rule: dict) -> str:
    if "channels" in rule:
        names, weights = zip(*rule["channels"].items())
        return rng.choices(names, weights)[0]
    return rule.get("channel", "account")


def _monthly(p: dict, start: dt.date, end: dt.date, groups: dict) -> list[Event]:
    """수입·고정비·정기결제: 매달 같은 날. 시작일(from)·해지일(until)·월별 금액 변경을 지원한다."""
    out = []
    for rule in p.get("monthly", []):
        for y, m in _months(start, end):
            ym = f"{y}-{m:02d}"
            if ym in rule.get("skip_months", []):
                continue
            day = dt.date(y, m, min(rule["day"], _last_day(y, m)))
            if rule.get("weekend") == "prev_business_day":
                while day.weekday() >= 5:
                    day -= DAY
            if not start <= day < end or not _active(rule, day):
                continue
            rng = _rng(p, f"{day}:{rule['id']}")
            at = _time(rng, day, rule.get("hours", [9, 18]))
            amount = rule.get("amount_by_month", {}).get(ym) or _monthly_amount(p, rule, day)
            eid, channel = f"{rule['id']}-{y}{m:02d}", rule.get("channel", "account")
            if "shared" in rule:                     # 내가 대표로 내고 나머지가 1/n 을 보내준다
                s = rule["shared"]
                unpaid = sum(rng.random() >= s["repay_prob"] for _ in range(s["people"] - 1))
                out += _split(p, eid, at, rule.get("merchant"), s["people"], amount, unpaid, None, rng, groups, s,
                              channel, rule["category"])
                continue
            out.append(Event(eid, at, rule["kind"], amount, channel, rule["category"], rule.get("merchant"),
                             export=rule.get("export")))
    return out


def _monthly_amount(p: dict, rule: dict, day: dt.date) -> int:
    if "pay" not in rule:
        return rule["amount"]
    pay = rule["pay"]                      # 전월 근무시간 × 시급 − 원천징수(10원 미만 절사)
    prev = day.replace(day=1) - DAY
    work_month = f"{prev.year}-{prev.month:02d}"
    if work_month not in pay["hours_by_month"]:
        raise ValueError(f"{p['id']} {rule['id']}: {work_month} 근무시간 없음")
    gross = pay["hours_by_month"][work_month] * pay["wage"]
    return gross - gross * pay["withholding_permille"] // 1000 // 10 * 10


def _maybe_cancel(p: dict, rule: dict, e: Event) -> list[Event]:
    """반품: 결제 뒤 며칠 안에 취소 (cancel_prob 이 있는 규칙만)."""
    if "cancel_prob" not in rule:
        return []
    rng = _rng(p, f"{e.id}:cancel")
    if rng.random() >= rule["cancel_prob"]:
        return []
    day = e.at.date() + dt.timedelta(days=rng.randint(*rule["cancel_delay"]))
    return [Event(f"{e.id}-c", _time(rng, day, [9, 22]), "cancel", 0, "", "", ref=e.id)]


def _habits(p: dict, start: dt.date, end: dt.date) -> list[Event]:
    """습관 지출: 요일별 확률 × 기간 배율로 하루 최대 1건."""
    out = []
    for rule in p.get("habits", []):
        for day in _days(start, end):
            if not _active(rule, day):
                continue
            rng = _rng(p, f"{day}:{rule['id']}")
            if rng.random() >= min(1.0, rule["prob"][day.weekday()] * _boost(rule, day)):
                continue
            at, amount = _time(rng, day, rule["hours"]), _amount(rng, rule["amount"], rule.get("step", 100))
            e = Event(f"{rule['id']}-{day:%Y%m%d}", at, "purchase", _price(rule, day, amount), _channel(rng, rule),
                      rule["category"], rng.choice(rule["merchants"]))
            out += [e, *_maybe_cancel(p, rule, e)]
    return out


def _irregular(p: dict, start: dt.date, end: dt.date) -> list[Event]:
    """비정기 지출: 달마다 n회, 날짜는 랜덤."""
    out = []
    for rule in p.get("irregular", []):
        for y, m in _months(start, end):
            rng = _rng(p, f"{y}-{m:02d}:{rule['id']}")
            days = [dt.date(y, m, d) for d in range(1, _last_day(y, m) + 1)]
            for i, day in enumerate(sorted(rng.sample(days, rng.randint(*rule["per_month"])))):
                at = _time(rng, day, rule["hours"])
                amount = _amount(rng, rule["amount"], rule.get("step", 100))
                channel, merchant = _channel(rng, rule), rng.choice(rule["merchants"]) if "merchants" in rule else None
                if start <= day < end and _active(rule, day):
                    e = Event(f"{rule['id']}-{day:%Y%m%d}-{i}", at, rule.get("kind", "purchase"), amount,
                              channel, rule["category"], merchant)
                    out += [e, *_maybe_cancel(p, rule, e)]
    return out


def _club_rule(p: dict, start: dt.date, end: dt.date, groups: dict) -> list[Event]:
    """동아리 회식: 대표 결제 후 부원들이 1/n 을 이체로 돌려준다 (일부는 안 보냄)."""
    rule, out = p.get("club"), []
    if not rule:
        return out
    for day in _days(start, end):
        if day.weekday() != rule["weekday"] or not _active(rule, day):
            continue
        rng = _rng(p, f"{day}:club")
        if rng.random() >= rule["prob"]:
            continue
        people = rng.randint(*rule["people"])
        total = _amount(rng, [rule["per_person"][0] * people, rule["per_person"][1] * people])
        unpaid = sum(rng.random() >= rule["repay_prob"] for _ in range(people - 1))
        if "prepaid_from" in rule and day >= rule["prepaid_from"]:   # 회식 전에 미리 걷기 (반응 규칙)
            unpaid = 0
        out += _split(p, f"club-{day:%Y%m%d}", _time(rng, day, rule["hours"]), rng.choice(rule["merchants"]),
                      people, total, unpaid, None, rng, groups, rule)
    return out


def _split(p, gid, at, merchant, people, total, unpaid, delay, rng, groups, repay: dict,
           channel: str = "card", category: str = "외식") -> list[Event]:
    """대표 결제 1건 + 나머지 사람들의 1/n 정산 입금 (repay: repay_delay, repay_channels)."""
    share = -(-total // (people * 100)) * 100                  # 1인 몫, 100원 단위 올림
    out = [Event(gid, at, "purchase", total, channel, category, merchant, delay, group=gid)]
    for i in sorted(rng.sample(range(people - 1), people - 1 - unpaid)):
        t = _time(rng, at.date() + dt.timedelta(days=rng.randint(*repay["repay_delay"])), [9, 24])
        if t <= at:
            t = at + dt.timedelta(minutes=rng.randint(15, 90))
        out.append(Event(f"{gid}-r{i}", t, "transfer_in", share, _channel(rng, {"channels": repay["repay_channels"]}),
                         "정산입금", group=gid))
    groups[gid] = {"payment": gid, "people": people, "total": total, "share": share, "unpaid": unpaid}
    return out


def _scripted(p: dict, groups: dict) -> list[Event]:
    """시나리오에 직접 적은 이벤트 (환불, 할부, 운영 기간의 기록 대상 거래 등)."""
    out = []
    for s in p.get("events", []):
        if s.get("type") == "club":
            prepaid = "prepaid_from" in p["club"] and s["at"].date() >= p["club"]["prepaid_from"]
            out += _split(p, s["id"], s["at"], s["merchant"], s["people"], s["total"], 0 if prepaid else s["unpaid"],
                          s.get("delay_days"), _rng(p, s["id"]), groups, p["club"])
        elif s["kind"] == "cancel":
            out.append(Event(s["id"], s["at"], "cancel", 0, "", "", ref=s["ref"], delay_days=s.get("delay_days")))
        else:
            out.append(Event(s["id"], s["at"], s["kind"], s["amount"], s.get("channel", "account"), s["category"],
                             s.get("merchant"), s.get("delay_days"), installment=s.get("installment_months", 0)))
    return out


def _caps(p: dict, events: list[Event]) -> list[Event]:
    """월 상한 (caps: 반응 규칙으로만 생김). from 이후 이 용도 결제가 그달 합계를 상한 위로 올리면 그 결제는 안 한다."""
    drop: set[str] = set()
    for c in p.get("caps", []):
        spent: dict[str, int] = {}
        for e in sorted((e for e in events if e.kind == "purchase" and e.category == c["category"]), key=_order):
            ym = f"{e.at:%Y-%m}"
            if e.at.date() >= c["from"] and spent.get(ym, 0) + e.amount > c["amount"]:
                drop.add(e.id)
                continue
            spent[ym] = spent.get(ym, 0) + e.amount
    return [e for e in events if e.id not in drop and not (e.kind == "cancel" and e.ref in drop)]


def _reimbursements(p: dict, events: list[Event]) -> list[Event]:
    """경비 환급 (reimbursements: 반응 규칙으로만 생김). from 이후 after_hour 시 이후의 이 습관 결제를 모아
    다음 달 pay_day 에 입금받는다."""
    out = []
    for r in p.get("reimbursements", []):
        by_month: dict[tuple[int, int], int] = {}
        for e in events:
            if (e.kind == "purchase" and e.id.startswith(f"{r['habit']}-") and e.at.date() >= r["from"]
                    and e.at.hour >= r["after_hour"]):
                by_month[(e.at.year, e.at.month)] = by_month.get((e.at.year, e.at.month), 0) + e.amount
        for (y, m), amount in sorted(by_month.items()):
            ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
            day = dt.date(ny, nm, min(r["pay_day"], _last_day(ny, nm)))
            out.append(Event(f"reimburse-{r['habit']}-{y}{m:02d}", dt.datetime.combine(day, dt.time(10, 0)),
                             "transfer_in", amount, "account", "경비 환급", export=r.get("export")))
    return out


def _order(e: Event):
    return e.at, e.id


def _effects(e: Event, orig: Event, credit: bool) -> tuple[int, int]:
    """(계좌 잔액 변화, 간편결제 머니 잔액 변화). 페르소나 금액이 현실적인지 확인하는 데만 쓴다.
    신용카드 결제는 계좌에서 바로 빠지지 않고 카드대금(card_bill)으로 나중에 빠진다."""
    a = e.amount
    if e.kind == "purchase":
        if e.channel in CARD_CHANNELS:
            return (0 if credit else -a), 0
        return 0, -a
    if e.kind == "cancel":
        bank, money = _effects(orig, orig, credit)
        return -bank, -money
    if e.kind == "transfer_in":
        return (a, 0) if e.channel == "account" else (0, a)
    if e.kind in ("transfer_out", "atm", "card_bill"):
        return -a, 0
    if e.kind == "charge":
        return -a, a
    raise ValueError(e.kind)


def _add_charges(p: dict, events: list[Event], credit: bool) -> list[Event]:
    """머니 잔액이 모자라면 결제 1분 전에 계좌에서 자동 충전."""
    ep, by_id = p["accounts"]["easypay"], {e.id: e for e in events}
    money, out = ep.get("opening_balance", 0), []
    for e in sorted(events, key=_order):
        if e.kind == "purchase" and e.channel == "easypay_balance" and money < e.amount:
            amount = max(ep["min_charge"], -(-(e.amount - money) // 10000) * 10000)
            out.append(Event(f"{e.id}-charge", e.at - dt.timedelta(minutes=1), "charge", amount, "account", "충전",
                             delay_days=e.delay_days))
            money += amount
        money += _effects(e, by_id.get(e.ref, e), credit)[1]
        out.append(e)
    return out


def _card_bills(p: dict, events: list[Event], start: dt.date, end: dt.date, out: list[dict]) -> list[Event]:
    """신용카드 대금: 결제일에 전월 사용분(일시불 + 할부 회차 − 취소)이 계좌에서 빠진다.
    첫 달 대금은 내역보다 앞선 달 사용분이라 first_bill 값을 쓴다 (내역에서 근거를 찾을 수 없음)."""
    card, stmt = p["accounts"]["card"], {}

    def bucket(ym: str) -> dict:
        return stmt.setdefault(ym, {"lump_sum": 0, "installments": 0, "cancels": 0})

    for e in events:
        if e.channel not in CARD_CHANNELS or e.kind not in ("purchase", "cancel"):
            continue
        y, m = e.at.year, e.at.month
        if e.kind == "cancel":
            bucket(_add_months(y, m, 1))["cancels"] -= e.amount
        elif e.installment > 1:
            per = e.amount // e.installment
            for i in range(e.installment):
                bucket(_add_months(y, m, i + 1))["installments"] += e.amount - per * (e.installment - 1) if i == 0 else per
        else:
            bucket(_add_months(y, m, 1))["lump_sum"] += e.amount
    bills = []
    for y, m in _months(start, end):
        day, ym = dt.date(y, m, min(card["bill_day"], _last_day(y, m))), f"{y}-{m:02d}"
        if not start <= day < end:
            continue
        if ym == f"{start.year}-{start.month:02d}":
            parts = {"first_bill": card["first_bill"]}
            calc = f"{_add_months(y, m, -1)} 사용분 (내역 이전) = {card['first_bill']:,}"
        else:
            parts = stmt.get(ym, {"lump_sum": 0, "installments": 0, "cancels": 0})
            calc = (f"일시불 {parts['lump_sum']:,} + 할부 회차 {parts['installments']:,} − 취소 {-parts['cancels']:,}"
                    f" = {sum(parts.values()):,}")
        amount = sum(parts.values())
        if amount <= 0:
            continue
        eid = f"card_bill-{y}{m:02d}"
        bills.append(Event(eid, _time(_rng(p, f"{day}:card_bill"), day, [6, 9]), "card_bill", amount, "account",
                           "카드대금", delay_days=0))
        out.append({"month": ym, "event_id": eid, "amount": amount, **parts, "calc": calc})
    return bills


def _check_balances(p: dict, events: list[Event], by_id: dict[str, Event], credit: bool) -> dict:
    """계좌·머니 잔액이 음수가 되면 페르소나 금액이 비현실적이다."""
    bank, money = p["accounts"]["bank"]["opening_balance"], p["accounts"]["easypay"].get("opening_balance", 0)
    lowest = (bank, None)
    for e in events:
        db, dm = _effects(e, by_id.get(e.ref, e), credit)
        bank, money = bank + db, money + dm
        if bank < 0 or money < 0:
            raise ValueError(f"{p['id']} {e.id}: 잔액이 음수가 됨 (페르소나 금액 조정 필요)")
        if bank < lowest[0]:
            lowest = (bank, e.at.isoformat())
    return {"bank_lowest": lowest[0], "bank_lowest_at": lowest[1], "bank_end": bank, "money_end": money}


# ---------------------------------------------------------------- 통합 내역 행
def tx_id(event_id: str) -> str:
    return "T" + hashlib.sha1(event_id.encode()).hexdigest()[:10].upper()


def _method(p: dict, channel: str) -> str:
    acc = p["accounts"]
    return {"card": acc["card"]["label"], "easypay_card": f"{acc['easypay']['label']}({acc['card']['label']})",
            "easypay_balance": acc["easypay"]["money_label"], "account": acc["bank"]["label"]}[channel]


def _export_row(p: dict, e: Event, by_id: dict[str, Event]) -> dict:
    """앱이 본 모습. 가맹점 결제는 앱 자동 분류(merchants.app_category)를, 나머지는 용도를 모르는 채로 쓴다."""
    money = p["accounts"]["easypay"]["money_label"]
    orig = by_id[e.ref] if e.kind == "cancel" else e
    method = _method(p, e.channel)
    if orig.kind == "purchase":
        m = p["merchants"][orig.merchant]
        name = m["card_name"] if orig.channel == "card" else m["name"]   # 카드 결제는 상호가 그대로 찍힌다
        kind, (main, sub) = "지출", m["app_category"]
        desc, amount = (f"{name}(취소)", e.amount) if e.kind == "cancel" else (name, -e.amount)
        if orig.installment > 1:
            method += f" 할부 {orig.installment}개월"
    elif e.kind == "charge":
        kind, main, sub, desc, amount = "이체", "충전", "", f"{money} 충전", -e.amount
    elif e.kind == "card_bill":
        kind, main, sub, desc, amount = "이체", "카드대금", "", f"{p['accounts']['card']['label']} 결제대금", -e.amount
    elif e.kind == "transfer_in" and e.channel == "easypay_balance":
        kind, main, sub, desc, amount = "수입", "미분류", "", f"{money} 받기", e.amount
    else:
        kind, main, sub, desc = NON_PURCHASE_ROWS[e.kind]
        if e.export:                                           # 예: 회사 급여이체는 적요에 '급여'가 찍힌다
            desc, (main, sub) = e.export["desc"], e.export["category"]
        amount = e.amount if e.kind == "transfer_in" else -e.amount
    return {"거래ID": tx_id(e.id), "날짜": f"{e.at:%Y-%m-%d}", "시간": f"{e.at:%H:%M}", "타입": kind, "대분류": main,
            "소분류": sub, "내용": desc, "금액": amount, "결제수단": method, "메모": ""}


def _batches(events, rows, d0, end) -> dict[str, list[dict]]:
    batches: dict[str, list[dict]] = {"onboarding": []} | {d.isoformat(): [] for d in _days(d0, end)}
    for e in events:                                        # 이미 시간순
        key = "onboarding" if e.visible_on < d0 else e.visible_on.isoformat()
        if key in batches:
            batches[key].append(rows[e.id])
    return batches


def feedback_points(d0: dt.date, end: dt.date) -> list[dict]:
    """피드백 시점: 온보딩(D0) → 주간(일요일 저녁) · 월간(말일 저녁). 같은 날이면 월간 하나로 합친다."""
    out = [{"date": d0.isoformat(), "kind": "onboarding"}]
    for d in _days(d0 + DAY, end):
        if d.day == _last_day(d.year, d.month):
            out.append({"date": d.isoformat(), "kind": "monthly"})
        elif d.weekday() == 6:
            out.append({"date": d.isoformat(), "kind": "weekly"})
    return out


def _cutoff(point: dict) -> dt.datetime:
    """그 피드백이 보는 마지막 순간. 온보딩은 D0가 시작될 때, 주간·월간은 그날 저녁."""
    hour = 0 if point["kind"] == "onboarding" else FEEDBACK_HOUR
    return dt.datetime.combine(dt.date.fromisoformat(point["date"]), dt.time(hour))


def next_feedback(points: list[dict], at: dt.datetime) -> str | None:
    """이 시각의 유저 이벤트를 처음 다루는 피드백 날짜."""
    return next((pt["date"] for pt in points if at <= _cutoff(pt)), None)


def _habit_records(p, events, rows, skip: set[str], d0: dt.date, end: dt.date) -> list[dict]:
    """기록 습관: 이런 거래는 이 확률로, 들어온 뒤 며칠 안에 골라서 기록한다. 문장은 페르소나 파일의 틀에서 고른다
    (LLM으로 만들지 않는다). 거래에서 나오므로, 소비가 바뀌면 기록도 따라 바뀐다."""
    out, used = [], set()
    for rule in p.get("record_habits", []):
        for e in events:
            if e.kind != "purchase" or e.category not in rule["categories"] or e.id in skip:
                continue
            rng = _rng(p, f"{e.id}:record:{rule['id']}")
            if rng.random() >= rule["prob"]:
                continue
            day = e.visible_on + dt.timedelta(days=rng.randint(*rule.get("delay_days", [1, 1])))
            written = _time(rng, day, rule["hours"])
            text = rng.choice(rule["texts"]).format(merchant=rows[e.id]["내용"], amount=f"{e.amount:,}")
            if d0 <= day < end and _active(rule, day) and (rule["id"], day) not in used:   # 같은 습관은 하루 1건
                used.add((rule["id"], day))
                out.append({"id": f"R-{tx_id(e.id)}", "written_at": written, "about": e.id, "text": text,
                            "source": "habit", "rule": rule["id"]})
    return out


def _records(p, events, by_id, rows, points, d0, end) -> tuple[dict[str, list[dict]], list[dict]]:
    """유저 기록 = 시나리오에 직접 쓴 기록 + 기록 습관으로 만든 기록.
    기록 시점에 이미 들어온 거래만 transaction_id 로 연결된다 (앱에서 고를 수 있는 것)."""
    scripted = [{**r, "source": "scripted"} for r in p.get("records", [])]
    items = scripted + _habit_records(p, events, rows, {r["about"] for r in scripted if "about" in r}, d0, end)
    if len({r["id"] for r in items}) != len(items):
        raise ValueError(f"{p['id']}: 기록 id 중복")
    out: dict[str, list[dict]] = {}
    meta = []
    for r in sorted(items, key=lambda r: r["written_at"]):
        written, e = r["written_at"], by_id.get(r.get("about", ""))
        linked = e is not None and e.visible_on < written.date()
        out.setdefault(written.date().isoformat(), []).append(
            {"record_id": r["id"], "user_id": p["user_id"], "written_at": written.isoformat(), "text": r["text"],
             "amount": r.get("amount"), "transaction_id": rows[e.id]["거래ID"] if linked else None})
        meta.append({"record_id": r["id"], "written_at": written.isoformat(), "source": r["source"],
                     "rule": r.get("rule"), "about_event": r.get("about"),
                     "about_tx": rows[e.id]["거래ID"] if e else None, "linked_when_written": linked,
                     "feedback_at": next_feedback(points, written), "learn": r.get("learn", [])})
    return out, meta


# ---------------------------------------------------------------- 정답 (평가 전용)
def _issues(p, by_id, rows, points) -> list[dict]:
    """기간 중 쟁점 (운영 중에 새로 생기거나 바뀌는 것). 근거 거래가 detect_by 피드백 전에 들어와 있어야 한다."""
    dates, out = {pt["date"] for pt in points}, []
    for i in p.get("issues", []):
        if i["type"] not in ISSUE_TYPES:
            raise ValueError(f"{p['id']} {i['id']}: 모르는 쟁점 유형 {i['type']}")
        if i["detect_by"].isoformat() not in dates or not i["from"] <= i["detect_by"]:
            raise ValueError(f"{p['id']} {i['id']}: detect_by 는 from 이후의 피드백 날짜여야 함")
        prevented = []
        for ev in i.get("evidence", []):
            if ev not in by_id and p.get("_modified"):   # ②·③ 세계: 바뀐 소비로 근거 거래가 안 생겼다 (막은 쟁점)
                prevented.append(ev)
            elif ev not in by_id or by_id[ev].visible_on > i["detect_by"]:
                raise ValueError(f"{p['id']} {i['id']}: 근거 {ev} 가 detect_by 까지 들어오지 않음")
        item = {**i, "evidence_tx": [rows[ev]["거래ID"] for ev in i.get("evidence", []) if ev in by_id]}
        out.append(item | ({"prevented": prevented} if prevented else {}))
    return out


def _status(item: dict, day: dt.date) -> str:
    if "from" in item and day < item["from"]:
        return "아직"
    if "until" in item and day > item["until"]:
        return "종료"
    if "detect_by" in item and day < item["detect_by"]:
        return "선택"
    return "필수"


def _cases(p, record_meta, issues, points, d0) -> list[dict]:
    """평가 케이스마다 확인할 피드백 날짜(check_at)를 붙인다.
    온보딩 = D0, 기록 케이스 = 그 기록 뒤 첫 피드백, 쟁점 케이스 = 쟁점의 detect_by, 없으면 매일 (None)."""
    written = {r["record_id"]: r["feedback_at"] for r in record_meta}
    detect = {i["id"]: i["detect_by"].isoformat() for i in issues}
    dates, out = {pt["date"] for pt in points}, []
    for c in p.get("cases", []):
        if c["phase"] == "onboarding":
            at = d0.isoformat()
        elif "check_at" in c:
            at = c["check_at"].isoformat()
        elif "record" in c:
            at = written[c["record"]]
        elif "issue" in c:
            at = detect[c["issue"]]
        else:
            at = None
        if at is not None and at not in dates:
            raise ValueError(f"{p['id']} {c['id']}: check_at {at} 은 피드백 날짜가 아님")
        out.append({**c, "check_at": at})
    return out


def goal_progress(p: dict, events: list[Event], d0: dt.date, upto: dt.date) -> dict | None:
    """목표 달성도 (가정, docs/10 §3). upto 까지 들어온 D0 이후 내역 기준 (내역에 찍힌 달로 나눈다).
    - 진척 = 현재 저축액 + 끝난 달들의 (수입 − 지출). 필요 = 필요 월 저축액 × 끝난 달 수.
      이번 달은 월급 전후로 크게 흔들리므로 진척에 넣지 않고 지금까지의 수입·지출만 따로 보여준다.
    - 저축 이체는 내 돈의 이동이라 지출이 아니다. 카드대금·머니 충전도 이미 센 돈이라 빼지 않는다.
    - 현금 인출은 쓴 돈으로 본다. 정산 입금과 환불은 지출에서 뺀다.
    - 목표를 위해 쓴 돈(goal_tracking.goal_categories, 예: 여행 예약)은 지출이 아니라 목표에 쓴 돈으로 진척에 넣는다."""
    goal = _goal(p.get("onboarding", {}).get("fixed_answers", {}).get("goal"), d0)
    if not goal:
        return None
    cats = set(p.get("goal_tracking", {}).get("goal_categories", []))
    months = {f"{y}-{m:02d}": {"month": f"{y}-{m:02d}", "income": 0, "spend": 0, "goal_spend": 0,
                               "done": dt.date(y, m, _last_day(y, m)) <= upto} for y, m in _months(d0, upto + DAY)}
    for e in events:
        if e.at.date() < d0 or e.visible_on > upto:
            continue
        b, sign = months[f"{e.at:%Y-%m}"], -1 if e.kind == "cancel" else 1
        if e.kind in ("purchase", "cancel"):
            b["goal_spend" if e.category in cats else "spend"] += sign * e.amount
        elif e.kind == "transfer_in" and e.group:
            b["spend"] -= e.amount
        elif e.kind == "transfer_in":
            b["income"] += e.amount
        elif e.kind == "atm" or (e.kind == "transfer_out" and e.category != "저축"):
            b["spend"] += e.amount
    for b in months.values():
        b["net"] = b["income"] - b["spend"]
    done = [b for b in months.values() if b.pop("done")]
    current = next((b for b in months.values() if b not in done), None)
    gained = sum(b["net"] for b in done)
    required = goal["monthly_required"] * len(done)
    return {"through": upto.isoformat(), "goal_amount": goal["amount"], "current_saved": goal["current_saved"],
            "monthly_required": goal["monthly_required"], "months_done": done, "progress": goal["current_saved"] + gained,
            "required_by_now": required, "on_track": gained >= required if done else None, "this_month": current,
            "calc": (f"현재 저축 {goal['current_saved']:,} + 끝난 달 (수입 − 지출) {gained:,} = "
                     f"{goal['current_saved'] + gained:,} / 필요 {goal['monthly_required']:,} × {len(done)}개월 = "
                     f"{required:,} (목표용 지출은 지출에서 빼고 목표에 쓴 돈으로 본다)")}


def _timeline(p, events, points, issues, cases, d0) -> list[dict]:
    """피드백 시점마다: 쟁점별 상태(기준 세계), 그때 확인할 케이스, 목표 달성도."""
    items = p.get("problems", []) + issues
    out = []
    for pt in points:
        day = dt.date.fromisoformat(pt["date"])
        out.append({**pt, "issues": {i["id"]: _status(i, day) for i in items},
                    "cases": [c["id"] for c in cases if c["check_at"] == pt["date"]],
                    "goal_progress": goal_progress(p, events, d0, day)})
    return out


def _truth(p, events, by_id, rows, groups, record_meta, balance, bills, start, d0, end, points, issues) -> dict:
    monthly: dict[str, dict] = {}

    def month(e: Event) -> dict:
        return monthly.setdefault(f"{e.at:%Y-%m}", {"income": {}, "spend": {}, "saving": 0, "cash_withdrawal": 0,
                                                    "card_bill": 0})

    def add(bucket: dict, key: str, amount: int):
        bucket[key] = bucket.get(key, 0) + amount

    for e in events:
        if e.kind == "purchase":                            # 할부도 결제한 달에 전액 (현금흐름은 card_bills)
            add(month(e)["spend"], e.category, e.amount)
        elif e.kind == "cancel":                            # 원래 결제한 달에서 뺀다
            add(month(by_id[e.ref])["spend"], e.category, -e.amount)
        elif e.kind == "transfer_in" and e.group:           # 정산 입금은 대표 결제에서 뺀다 (순지출)
            add(month(by_id[e.group])["spend"], by_id[e.group].category, -e.amount)
        elif e.kind == "transfer_in":
            add(month(e)["income"], e.category, e.amount)
        elif e.kind == "transfer_out" and e.category == "저축":
            month(e)["saving"] += e.amount
        elif e.kind == "transfer_out":
            add(month(e)["spend"], e.category, e.amount)
        elif e.kind == "atm":
            month(e)["cash_withdrawal"] += e.amount       # 용처 모름. 현금 기록이 있으면 그쪽이 지출
        elif e.kind == "card_bill":
            month(e)["card_bill"] += e.amount             # 이미 결제로 센 돈이 계좌에서 빠지는 것 (지출 아님)
        # charge: 내 돈이 머니로 옮겨간 것이라 집계하지 않는다

    dutch = []
    for gid, g in groups.items():
        if gid not in by_id:
            continue
        repaid = [e for e in events if e.group == gid and e.kind == "transfer_in"]
        received = sum(e.amount for e in repaid)
        dutch.append({**g, "paid_count": len(repaid), "received": received, "net_cost": g["total"] - received,
                      "payment_tx": rows[gid]["거래ID"], "repayment_tx": [rows[e.id]["거래ID"] for e in repaid]})

    history = [f"{y}-{m:02d}" for y, m in _months(start, d0)]
    fixed = p.get("onboarding", {}).get("fixed_answers", {})
    cases = _cases(p, record_meta, issues, points, d0)
    return {
        "note": ("facts = 생성 코드가 계산한 사실(정확), user_sim = 유저 역할 시뮬레이터가 답할 내용, "
                 "rubric = 우리가 정한 채점 기준(정답 문장이 아님). 모두 가상 세계 기준이다. "
                 "적재·분석 코드와 에이전트 입력에 넣지 않는다."),
        "persona": p["id"], "user_id": p["user_id"],
        "period": {"history": [start.isoformat(), (d0 - DAY).isoformat()],
                   "live": [d0.isoformat(), (end - DAY).isoformat()], "feedback": points},
        "facts": {
            "goal": _goal(fixed.get("goal"), d0),
            "levers": _levers(p, monthly, groups, by_id, history),
            "monthly": monthly,
            "balance": balance,
            "card_bills": bills,
            "installments": [{"tx": rows[e.id]["거래ID"], "event_id": e.id, "amount": e.amount, "months": e.installment,
                              "per_month": e.amount // e.installment} for e in events if e.installment > 1],
            "dutch_groups": dutch,
            "cancels": [{"original_tx": rows[e.ref]["거래ID"], "cancel_tx": rows[e.id]["거래ID"]}
                        for e in events if e.kind == "cancel"],
            "records": record_meta,
            "timeline": _timeline(p, events, points, issues, cases, d0),
            "events": [{"tx_id": rows[e.id]["거래ID"], "event_id": e.id, "at": e.at.isoformat(),
                        "visible_on": e.visible_on.isoformat(), "kind": e.kind, "channel": e.channel,
                        "true_category": e.category, "amount": e.amount, "group": e.group, "ref": e.ref,
                        "installment": e.installment} for e in events],
        },
        "user_sim": {"fixed_answers": fixed, "hidden_facts": p.get("hidden_facts", []),
                     "responses": p.get("responses", [])},          # 반응 규칙 (서비스 있음 세계, docs/10 §4)
        "rubric": {"problems": p.get("problems", []), "issues": issues, "cases": cases},
    }


def _goal(goal: dict | None, d0: dt.date) -> dict | None:
    """고정 답변의 목표로 부족액과 필요 월 저축액을 계산한다 (D0가 속한 달부터 마감 달까지)."""
    if not goal:
        return None
    deadline = goal["deadline"]
    months_left = (deadline.year - d0.year) * 12 + deadline.month - d0.month + 1
    gap = goal["amount"] - goal["current_saved"]
    required = -(-gap // months_left)
    return {"amount": goal["amount"], "current_saved": goal["current_saved"], "deadline": deadline.isoformat(),
            "gap": gap, "months_left": months_left, "monthly_required": required,
            "calc": f"({goal['amount']:,} − {goal['current_saved']:,}) ÷ {months_left}개월 = {required:,} (원 단위 올림)"}


def _levers(p, monthly, groups, by_id, history: list[str]) -> dict:
    """얽힌 문제별 절약 후보 크기 (온보딩 기간 기준). 선택지 크기 순서를 채점할 때 쓴다."""
    out = {}
    for prob in p.get("problems", []):
        lever = prob.get("lever")
        if not lever:
            continue
        kind, cats = lever["kind"], lever.get("categories") or [lever.get("category")]
        label = "·".join(c for c in cats if c)
        if kind == "spend":
            by_month = {ym: sum(monthly.get(ym, {}).get("spend", {}).get(c, 0) for c in cats) for ym in history}
            size, calc = sum(by_month.values()) // len(history), f"{label} 순지출 {len(history)}개월 평균 (원 단위 내림)"
        elif kind == "income_drop":
            by_month = {ym: sum(monthly.get(ym, {}).get("income", {}).get(c, 0) for c in cats) for ym in history}
            size = by_month[history[0]] - by_month[history[-1]]
            calc = f"{label} 입금 {history[0]} − {history[-1]}"
        elif kind == "unpaid_club":
            by_month = dict.fromkeys(history, 0)
            for g in groups.values():
                ym = f"{by_id[g['payment']].at:%Y-%m}" if g["payment"] in by_id else None
                if ym in by_month:
                    by_month[ym] += g["unpaid"] * g["share"]
            size, calc = sum(by_month.values()) // len(history), f"못 받은 정산 {len(history)}개월 평균 (원 단위 내림)"
        else:
            raise ValueError(f"{p['id']} {prob['id']}: 모르는 lever {kind}")
        out[prob["id"]] = {"side": prob["side"], "by_month": by_month, "monthly_size": size, "calc": calc}
    return out
