"""기준선 하네스의 결정적 도구 (docs/10 §5). 에이전트 구조가 달라도 같은 도구를 쓴다 (비교 조건).

- 보이는 거래(그 시점까지 들어온 통합 내역)만 본다. truth.json 은 보지 않는다.
- 계산·조회 도구는 contracts.ToolResult 를 돌려준다. 유저에게 보이는 숫자는 여기 value·calc 에 있어야 한다
  (agent.tools.verify_response 로 대조).
- B 의 실제 계산 함수(data/)가 나오기 전의 최소 구현이다. 목표 달성도만 생성기의 goal_progress 를 쓴다 (docs/10 §3).
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import math
import operator
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from ..contracts import ToolResult
from ..demo.generator import goal_progress

WEEKDAYS = "월화수목금토일"
INSTALLMENT = re.compile(r"할부 (\d+)개월")
OUTPUT_LIMIT = 20000          # 도구 결과를 LLM 에 넣을 때 자르는 길이 (문자)


@dataclass
class View:
    """에이전트가 이 시점에 볼 수 있는 것. world 는 목표 달성도 계산에만 쓴다 (docs/10 §3 가정)."""
    persona: dict
    world: object             # demo.generator.World (서비스 있음 세계일 수 있다)
    rows: list[dict]          # 보이는 통합 내역 행
    through: dt.date          # 보이는 마지막 날
    today: dt.date


def _result(value, calc=None, status="ok", caveats=(), as_of=None) -> ToolResult:
    return {"status": status, "value": value, "calc": calc or {},
            "sources": [{"id": "TX_VISIBLE", "name": "통합 거래내역 (지금까지 들어온 것)", "as_of": as_of}],
            "as_of": as_of, "caveats": list(caveats)}


def _date(s: str | None) -> str | None:
    return dt.date.fromisoformat(s[:10]).isoformat() if s else None


def _filter(rows, start_date=None, end_date=None, type=None, category=None, subcategory=None, keyword=None):
    start_date, end_date = _date(start_date), _date(end_date)
    for r in rows:
        if (start_date and r["날짜"] < start_date) or (end_date and r["날짜"] > end_date):
            continue
        if (type and r["타입"] != type) or (category and r["대분류"] != category) \
                or (subcategory and r["소분류"] != subcategory):
            continue
        if keyword and keyword not in r["내용"] + r["메모"]:
            continue
        yield r


# ------------------------------------------------------------------ 조회·집계
def spending_summary(v: View, start_date=None, end_date=None, group_by="category", category=None,
                     subcategory=None, keyword=None) -> ToolResult:
    keys = {"month": lambda r: r["날짜"][:7], "category": lambda r: r["대분류"],
            "subcategory": lambda r: f'{r["대분류"]}/{r["소분류"]}', "merchant": lambda r: r["내용"],
            "payment": lambda r: r["결제수단"],
            "weekday": lambda r: WEEKDAYS[dt.date.fromisoformat(r["날짜"]).weekday()]}
    if group_by not in keys:
        return _result(None, status="needs_check", caveats=[f"group_by 는 {', '.join(keys)} 중 하나"])
    groups: dict = defaultdict(lambda: {"spent": 0, "count": 0})
    for r in _filter(v.rows, start_date, end_date, "지출", category, subcategory, keyword):
        g = groups[keys[group_by](r)]
        g["spent"] -= r["금액"]       # 지출은 음수, 환불은 양수 → 순지출(양수)
        g["count"] += 1
    ordered = [{"key": k, **g} for k, g in sorted(groups.items(), key=lambda kv: -kv[1]["spent"])]
    total, count = sum(g["spent"] for g in ordered), sum(g["count"] for g in ordered)
    filters = {k: x for k, x in {"start_date": start_date, "end_date": end_date, "category": category,
                                 "subcategory": subcategory, "keyword": keyword}.items() if x}
    return _result({"filters": filters, "group_by": group_by, "groups": ordered, "total": {"spent": total, "count": count}},
                   calc={"total": f"지출 행 {count}건의 순지출 합 = {total:,} (환불 반영)"},
                   caveats=["카드대금·간편결제 충전·이름 없는 이체는 지출 행이 아니라서 뺐다 (이중 집계 방지)",
                            "분류는 앱 자동 분류라 틀릴 수 있다"], as_of=v.through.isoformat())


def list_transactions(v: View, start_date=None, end_date=None, type=None, category=None, subcategory=None,
                      keyword=None, limit=40) -> ToolResult:
    rows = list(_filter(v.rows, start_date, end_date, type, category, subcategory, keyword))
    limit = max(1, min(int(limit), 100))
    return _result({"count": len(rows), "rows": rows[-limit:]},
                   caveats=[f"최근 {limit}건만 보여 준다"] if len(rows) > limit else [], as_of=v.through.isoformat())


def recurring_payments(v: View) -> ToolResult:
    """여러 달 반복되는 지출(가맹점별)과 가격 변화, 온보딩 이후 처음 보인 가맹점."""
    d0 = v.world.d0.isoformat()
    by: dict[str, list[dict]] = defaultdict(list)
    for r in v.rows:
        if r["타입"] == "지출" and r["금액"] < 0 and r["대분류"] != "현금":
            by[r["내용"]].append(r)
    recurring, new, calc = [], [], {}
    for name, rows in by.items():
        months = sorted({r["날짜"][:7] for r in rows})
        first = min(r["날짜"] for r in rows)
        charges = [{"date": r["날짜"], "amount": -r["금액"], "tx": r["거래ID"]} for r in rows]
        item = {"merchant": name, "category": f'{rows[0]["대분류"]}/{rows[0]["소분류"]}', "first_seen": first,
                "charges": charges}
        if len(months) >= 2 and len(rows) <= len(months) + 1:
            by_month = {}
            for c in charges:
                by_month.setdefault(c["date"][:7], c["amount"])
            changes, prev = [], None
            for ym in months:
                if prev is not None and by_month[ym] != by_month[prev]:
                    changes.append({"month": ym, "from": by_month[prev], "to": by_month[ym],
                                    "diff": by_month[ym] - by_month[prev]})
                    calc[f"{name} {ym}"] = f"{by_month[ym]:,} − {by_month[prev]:,} = {by_month[ym] - by_month[prev]:,}"
                prev = ym
            recurring.append({**item, "months": months, "price_changes": changes, "started_after_onboarding": first >= d0})
        elif first >= d0:
            new.append(item)
    return _result({"recurring": recurring, "new_since_onboarding": new}, calc=calc,
                   caveats=["같은 가맹점이 2개월 이상, 달마다 1~2번 결제되면 반복으로 본다 (정기결제가 아닐 수 있다)",
                            "카드 결제와 간편결제는 가맹점 이름이 다르게 찍힐 수 있다"], as_of=v.through.isoformat())


def unnamed_transfers(v: View, start_date=None, end_date=None) -> ToolResult:
    """상대 이름이 없는 입금·출금과 ATM 출금을 방향·금액별로 묶는다."""
    groups: dict = defaultdict(list)
    for r in _filter(v.rows, start_date, end_date):
        if r["대분류"] == "미분류" and r["타입"] in ("수입", "이체"):
            groups[("입금" if r["금액"] > 0 else "출금", abs(r["금액"]))].append(r)
        elif r["대분류"] == "현금":
            groups[("ATM 출금", abs(r["금액"]))].append(r)
    out = [{"direction": d, "amount": a, "count": len(rs), "dates": [r["날짜"] for r in rs],
            "days_of_month": sorted({int(r["날짜"][8:]) for r in rs}), "tx": [r["거래ID"] for r in rs],
            "via": sorted({r["결제수단"] for r in rs})}
           for (d, a), rs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), -kv[0][1]))]
    return _result({"groups": out}, caveats=["통합 내역에는 이체 상대 이름이 없다. 용도는 유저에게 확인해야 한다"],
                   as_of=v.through.isoformat())


def _ym(y: int, m: int, n: int) -> str:
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y}-{m + 1:02d}"


def card_bills(v: View) -> ToolResult:
    """신용카드 대금을 전월 사용분(일시불 + 할부 회차 − 취소)과 맞춰 본다."""
    bills = [r for r in v.rows if r["대분류"] == "카드대금"]
    if not bills:
        return _result({"bills": []}, caveats=["카드대금 행이 없다 (체크카드이거나 아직 결제일 전)"],
                       as_of=v.through.isoformat())
    first_month = min(r["날짜"] for r in v.rows)[:7]
    out, calc = [], {}
    for b in bills:
        label = b["내용"].removesuffix(" 결제대금")
        y, m = int(b["날짜"][:4]), int(b["날짜"][5:7])
        usage = _ym(y, m, -1)
        item = {"bill_month": b["날짜"][:7], "bill_date": b["날짜"], "amount": -b["금액"], "usage_month": usage,
                "bill_tx": b["거래ID"]}
        if usage < first_month:
            item["basis"] = "내역 이전 사용분이라 이 내역으로는 근거를 찾을 수 없다"
            out.append(item)
            continue
        lump = inst = cancels = 0
        for r in v.rows:
            if label not in r["결제수단"] or r["타입"] != "지출":
                continue
            ry, rm = int(r["날짜"][:4]), int(r["날짜"][5:7])
            if r["금액"] > 0:                                   # 취소(환불)
                cancels += r["금액"] if r["날짜"][:7] == usage else 0
                continue
            n = int(k.group(1)) if (k := INSTALLMENT.search(r["결제수단"])) else 1
            if n == 1:
                lump += -r["금액"] if r["날짜"][:7] == usage else 0
                continue
            i = (y * 12 + m) - (ry * 12 + rm) - 1               # 이번 대금이 몇 번째 회차인가 (0부터)
            if 0 <= i < n:
                total, per = -r["금액"], -r["금액"] // n
                inst += total - per * (n - 1) if i == 0 else per
        computed = lump + inst - cancels
        item |= {"lump_sum": lump, "installments": inst, "cancels": cancels, "computed": computed,
                 "matches": computed == item["amount"]}
        calc[item["bill_month"]] = (f"{usage} 사용분: 일시불 {lump:,} + 할부 회차 {inst:,} − 취소 {cancels:,} = {computed:,}"
                                    f" (대금 {item['amount']:,})")
        out.append(item)
    return _result({"bills": out}, calc=calc,
                   caveats=["카드대금 이체는 이미 센 카드 결제의 돈이라 지출에 더하지 않는다"], as_of=v.through.isoformat())


def goal_progress_tool(v: View) -> ToolResult:
    g = goal_progress(v.persona, v.world.events, v.world.d0, v.through)
    if not g:
        return _result(None, status="no_data", caveats=["목표가 없다"])
    return _result(g, calc={"progress": g["calc"]}, as_of=v.through.isoformat(),
                   caveats=["끝난 달만 진척에 넣는다. 이번 달은 월급 전후로 흔들려서 this_month 에 따로 보여 준다",
                            "저축 이체는 내 돈의 이동이라 지출이 아니다"])


def forecast_goal(v: View, goal_amount: int, current_saved: int, deadline: str) -> ToolResult:
    y, m = int(deadline[:4]), int(deadline[5:7])
    months = max(1, (y - v.today.year) * 12 + (m - v.today.month) + 1)    # 이번 달 포함
    need = math.ceil((int(goal_amount) - int(current_saved)) / months)
    return _result({"months_left": months, "monthly_required": need, "gap": int(goal_amount) - int(current_saved)},
                   calc={"monthly_required": f"({int(goal_amount):,} − {int(current_saved):,}) ÷ {months}개월 = {need:,}"
                                             " (이번 달 포함, 원 단위 올림)"}, as_of=v.today.isoformat())


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg, ast.FloorDiv: operator.floordiv}


def calculate(v: View, expression: str) -> ToolResult:
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("숫자와 + − × ÷ 괄호만 쓸 수 있다")
    try:
        x = ev(ast.parse(expression.replace(",", ""), mode="eval").body)
    except (SyntaxError, ValueError, ZeroDivisionError) as e:
        return _result(None, status="needs_check", caveats=[str(e)])
    x = int(x) if float(x).is_integer() else round(x, 2)
    return _result({"expression": expression, "result": x}, calc={"result": f"{expression} = {x:,}"})


# ------------------------------------------------------------------ 도구 상자
_DATE = {"type": "string", "description": "YYYY-MM-DD"}
_FILTERS = {"start_date": _DATE, "end_date": _DATE, "category": {"type": "string", "description": "대분류 (앱 분류)"},
            "subcategory": {"type": "string", "description": "소분류"},
            "keyword": {"type": "string", "description": "내용(가맹점명)에 들어 있는 말"}}
SPECS = {
    "spending_summary": {
        "description": "기간·조건별 순지출 합계 (지출 행만, 환불 반영, 카드대금·충전·이체 제외). "
                       "group_by: month | category | subcategory | merchant | payment | weekday",
        "parameters": {"type": "object", "properties": {"group_by": {"type": "string"}, **_FILTERS}}},
    "list_transactions": {
        "description": "통합 거래내역 행 조회 (금액: 지출·출금 음수, 수입·환불 양수). 최근 limit 건.",
        "parameters": {"type": "object", "properties": {
            **_FILTERS, "type": {"type": "string", "description": "지출 | 수입 | 이체"}, "limit": {"type": "integer"}}}},
    "recurring_payments": {
        "description": "여러 달 반복되는 지출(가맹점별 결제 내역·가격 변화)과 온보딩 이후 처음 보인 가맹점.",
        "parameters": {"type": "object", "properties": {}}},
    "unnamed_transfers": {
        "description": "상대 이름이 없는 입금·출금과 ATM 출금을 방향·금액별로 묶어 날짜와 함께 보여 준다.",
        "parameters": {"type": "object", "properties": {"start_date": _DATE, "end_date": _DATE}}},
    "card_bills": {
        "description": "신용카드 대금을 전월 사용분(일시불 + 할부 회차 − 취소)과 맞춰 본 계산.",
        "parameters": {"type": "object", "properties": {}}},
    "goal_progress": {
        "description": "목표 달성도: 현재 저축 + 끝난 달 (수입 − 지출)을 필요 월 저축액 × 끝난 달 수와 비교. 이번 달은 따로.",
        "parameters": {"type": "object", "properties": {}}},
    "forecast_goal": {
        "description": "목표 금액·현재 저축·마감(YYYY-MM-DD)으로 남은 개월(이번 달 포함)과 필요 월 저축액 계산.",
        "parameters": {"type": "object", "properties": {
            "goal_amount": {"type": "integer"}, "current_saved": {"type": "integer"}, "deadline": _DATE},
            "required": ["goal_amount", "current_saved", "deadline"]}},
    "calculate": {
        "description": "사칙연산 계산기. 유저에게 말할 숫자(합계·차이·평균·비율)는 이걸로 계산한다.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}},
    "ask_user": {
        "description": "유저에게 짧은 질문 하나. 내역만으로 알 수 없는 사실(이체 용도, 계속 쓸지, 정산 여부)을 확인할 때. "
                       "target 에는 무엇에 대한 질문인지(가맹점명, 날짜·금액이 있는 거래)를 적는다.",
        "parameters": {"type": "object", "properties": {"target": {"type": "string"}, "question": {"type": "string"}},
                       "required": ["target", "question"]}},
}
FUNCS: dict[str, Callable[..., ToolResult]] = {
    "spending_summary": spending_summary, "list_transactions": list_transactions,
    "recurring_payments": recurring_payments, "unnamed_transfers": unnamed_transfers, "card_bills": card_bills,
    "goal_progress": goal_progress_tool, "forecast_goal": forecast_goal, "calculate": calculate,
}


@dataclass
class ToolBox:
    """한 번의 피드백 호출에서 에이전트가 쓰는 도구. 호출과 결과를 모두 기록한다 (숫자 검증·채점·비용 측정)."""
    view: View
    ask: Callable[[str, str], dict] | None = None     # 유저 시뮬레이터
    max_questions: int = 3
    log: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)

    def specs(self) -> list[dict]:
        names = list(FUNCS) + (["ask_user"] if self.ask else [])
        return [{"name": n, **SPECS[n]} for n in names]

    def call(self, name: str, args: dict) -> dict:
        if name == "ask_user" and self.ask:
            out = self._ask(**args)
        elif name in FUNCS:
            try:
                out = FUNCS[name](self.view, **args)
            except (TypeError, ValueError, KeyError) as e:
                out = _result(None, status="needs_check", caveats=[f"{name} 인자 오류: {e}"])
        else:
            out = {"ok": False, "error": f"모르는 도구: {name}"}
        self.log.append({"name": name, "args": args, "output": out})
        return out

    def _ask(self, target: str = "", question: str = "") -> dict:
        if len(self.questions) >= self.max_questions:
            return {"ok": False, "error": f"이번 피드백 질문 한도({self.max_questions}개)를 넘었다. 질문 없이 진행하라."}
        a = self.ask(target, question)
        self.questions.append({"target": target, "question": question, **a})
        return {"ok": True, "user_answer": a["answer"]}

    def results(self) -> list[ToolResult]:
        return [t["output"] for t in self.log if t["output"].get("status") == "ok"]

    @staticmethod
    def dump(out: dict) -> str:
        return json.dumps(out, ensure_ascii=False, default=str)[:OUTPUT_LIMIT]
