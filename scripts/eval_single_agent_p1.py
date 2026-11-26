"""P1 단일 에이전트 기준선 실험 (A1: LLM 1개 + Python 계산 Tool). 실험 스크립트이며 서비스 코드가 아니다.

준비: uv run python -m spending_agent.demo build P1 && uv run python -m spending_agent.demo advance P1 --days 14
실행: uv run --with openai --with python-dotenv python -X utf8 scripts/eval_single_agent_p1.py data/demo/P1 .env [model]
결과: 실행 기록 JSON 은 data/demo/ 아래가 아니라 이 스크립트 옆에 남지 않도록 eval/runs/ 에 저장한다 (git 제외).
에이전트에는 truth.json 의 정답·숨은 사실을 넣지 않는다. 숨은 사실은 사용자 시뮬레이터만, 정답은 채점만 쓴다.
"""
from __future__ import annotations

import ast
import csv
import json
import math
import operator
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

DEMO = Path(sys.argv[1])
load_dotenv(sys.argv[2])
MODEL = sys.argv[3] if len(sys.argv) > 3 else "gpt-6.1-terra"
SIM_MODEL = "gpt-5.4-mini"
JUDGE_MODEL = "gpt-6.2-sol"
client = OpenAI()
truth = json.loads((DEMO / "truth.json").read_text(encoding="utf-8"))
SIM = truth["user_sim"]
CASES = truth["rubric"]["cases"]


# ------------------------------------------------------------------ 데이터
def load_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return [dict(r, 금액=int(r["금액"])) for r in csv.DictReader(f)]


ONBOARD = load_csv(DEMO / "onboarding" / "transactions.csv")
DAILY = {d.name: load_csv(d / "transactions.csv") for d in sorted((DEMO / "daily").iterdir())}
RECORDS = []
for d in sorted((DEMO / "daily").iterdir()):
    rp = d / "records.json"
    if rp.exists():
        RECORDS += [dict(r, day=d.name) for r in json.loads(rp.read_text(encoding="utf-8"))["records"]]
RECORDS.sort(key=lambda r: r["written_at"])

STATE = {"rows": list(ONBOARD), "today": "2026-12-01", "tool_log": [], "questions": []}


def visible_rows():
    return STATE["rows"]


def _filter(start_date=None, end_date=None, type=None, category=None, subcategory=None, keyword=None):
    out = []
    for r in visible_rows():
        if start_date and r["날짜"] < start_date: continue
        if end_date and r["날짜"] > end_date: continue
        if type and r["타입"] != type: continue
        if category and r["대분류"] != category: continue
        if subcategory and r["소분류"] != subcategory: continue
        if keyword and keyword not in (r["내용"] + r["메모"]): continue
        out.append(r)
    return out


# ------------------------------------------------------------------ Tool (결정적 Python)
def list_transactions(start_date=None, end_date=None, type=None, category=None, subcategory=None, keyword=None, limit=60):
    rows = _filter(start_date, end_date, type, category, subcategory, keyword)
    return {"count": len(rows), "rows": rows[: max(1, min(int(limit), 120))]}


def sum_transactions(group_by="month", start_date=None, end_date=None, type=None, category=None, subcategory=None, keyword=None):
    key = {"month": lambda r: r["날짜"][:7], "category": lambda r: r["대분류"], "subcategory": lambda r: f'{r["대분류"]}/{r["소분류"]}',
           "merchant": lambda r: r["내용"], "type": lambda r: r["타입"], "payment": lambda r: r["결제수단"]}[group_by]
    agg = defaultdict(lambda: {"sum": 0, "count": 0})
    for r in _filter(start_date, end_date, type, category, subcategory, keyword):
        a = agg[key(r)]; a["sum"] += r["금액"]; a["count"] += 1
    return {"group_by": group_by, "groups": dict(sorted(agg.items(), key=lambda kv: kv[1]["sum"]))}


def find_recurring():
    by = defaultdict(list)
    for r in visible_rows():
        if r["타입"] == "지출":
            by[r["내용"]].append(r)
    out = []
    for m, rows in by.items():
        months = sorted({r["날짜"][:7] for r in rows})
        if len(months) >= 2 and len(rows) <= len(months) + 1:
            out.append({"merchant": m, "category": f'{rows[0]["대분류"]}/{rows[0]["소분류"]}', "months": months,
                        "charges": [{"date": r["날짜"], "amount": r["금액"], "payment": r["결제수단"]} for r in rows]})
    return {"recurring_candidates": out}


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg, ast.Pow: operator.pow}


def calculate(expression):
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
        if isinstance(n, ast.BinOp): return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp): return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("숫자와 + - * / 괄호만 가능")
    v = ev(ast.parse(expression.replace(",", ""), mode="eval").body)
    return {"expression": expression, "result": round(v, 2)}


def forecast_goal(goal_amount, current_saved, deadline):
    y, m = int(deadline[:4]), int(deadline[5:7])
    ty, tm = int(STATE["today"][:4]), int(STATE["today"][5:7])
    months = max(1, (y - ty) * 12 + (m - tm) + 1)  # 이번 달 포함
    need = math.ceil((goal_amount - current_saved) / months)
    return {"months_left": months, "monthly_required": need,
            "calc": f"({goal_amount:,} − {current_saved:,}) ÷ {months}개월 = {need:,} (원 단위 올림)"}


def ask_user(question):
    STATE["questions"].append(question)
    facts = "\n".join(f'- {h["topic"]}: {h["answer"]}' for h in SIM["hidden_facts"])
    prompt = (f"너는 가계부 앱 사용자다. 아래 '내가 아는 사실'에 있는 내용만 짧게 사실대로 답하라. 질문과 관련 없는 사실은 말하지 마라. "
              f"관련 사실이 없으면 '잘 모르겠어요'라고 답하라.\n내가 아는 사실:\n{facts}\n\n앱의 질문: {question}")
    r = client.responses.create(model=SIM_MODEL, input=prompt)
    AUX["sim"]["calls"] += 1; AUX["sim"]["in"] += r.usage.input_tokens; AUX["sim"]["out"] += r.usage.output_tokens
    return {"user_answer": r.output_text.strip()}


TOOLS_PY = {f.__name__: f for f in [list_transactions, sum_transactions, find_recurring, calculate, forecast_goal, ask_user]}
FILTER_PROPS = {"start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string"},
                "type": {"type": "string", "description": "지출|수입|이체"}, "category": {"type": "string", "description": "대분류"},
                "subcategory": {"type": "string"}, "keyword": {"type": "string", "description": "내용/메모 포함 검색"}}
TOOLS = [
    {"type": "function", "name": "list_transactions", "description": "통합 거래내역 조회 (금액: 지출 음수, 수입 양수).",
     "parameters": {"type": "object", "properties": {**FILTER_PROPS, "limit": {"type": "integer"}}}},
    {"type": "function", "name": "sum_transactions", "description": "조건에 맞는 거래 합계. group_by: month|category|subcategory|merchant|type|payment",
     "parameters": {"type": "object", "properties": {"group_by": {"type": "string"}, **FILTER_PROPS}}},
    {"type": "function", "name": "find_recurring", "description": "여러 달 반복되는 지출 후보 목록.",
     "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "calculate", "description": "사칙연산 계산기. 사용자에게 말할 숫자는 반드시 이걸로 계산한다.",
     "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}},
    {"type": "function", "name": "forecast_goal", "description": "목표 월 필요 저축액. deadline YYYY-MM-DD",
     "parameters": {"type": "object", "properties": {"goal_amount": {"type": "integer"}, "current_saved": {"type": "integer"},
                                                     "deadline": {"type": "string"}}, "required": ["goal_amount", "current_saved", "deadline"]}},
    {"type": "function", "name": "ask_user", "description": "사용자에게 짧은 질문 하나. 내역만으로 알 수 없는 사실(용도, 계속 쓸지, 정산 등)을 확인할 때.",
     "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
]

SYSTEM = """너는 목표 지향 소비 관리 앱의 AI 코치다 (단일 에이전트). 사용자 거래내역·목표를 보고 새는 돈과 바꿀 만한 소비를 찾아 제안한다.
원칙:
1. 사용자에게 말하는 금액·기간·절감액은 반드시 Tool 결과(calculate, forecast_goal, sum_transactions 등)에서 나온 숫자만 쓴다. 암산 금지.
2. 거래내역만으로 알 수 없는 것은 추측하지 말고 ask_user 로 묻는다. 한 번에 질문 하나, 한 세션에 최대 3개.
3. 사용자가 지키겠다고 한 소비(보호 지출)는 줄이라고 권하지 않는다.
4. 해지·변경 결정은 사용자가 한다. 대신 결정하지 않는다.
5. 이중 집계를 조심한다 (카드대금 출금, 간편결제 충전, 대표 결제 후 정산 입금 등).
6. 개입할 가치가 없으면 짧게 답하고 끝낸다.
답변은 한국어로 간결하게, 핵심 숫자는 계산식과 함께."""


# ------------------------------------------------------------------ 에이전트 루프
METRICS = {"llm_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0}
PHASES = {}          # 단계별 지표
CALLS = []           # 호출별 (단계, 입력, 출력, 초)
AUX = {"sim": {"calls": 0, "in": 0, "out": 0}, "judge": {"calls": 0, "in": 0, "out": 0}}
CUR = {"phase": "onboarding"}


def _ph():
    return PHASES.setdefault(CUR["phase"], {"llm_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0,
                                            "tools": {}})


def agent_turn(history, user_msg, max_steps=20):
    history.append({"role": "user", "content": user_msg})
    t0 = time.time()
    for _ in range(max_steps):
        c0 = time.time()
        r = client.responses.create(model=MODEL, instructions=SYSTEM, input=history, tools=TOOLS)
        dt = time.time() - c0
        METRICS["llm_calls"] += 1
        METRICS["input_tokens"] += r.usage.input_tokens; METRICS["output_tokens"] += r.usage.output_tokens
        ph = _ph(); ph["llm_calls"] += 1; ph["input_tokens"] += r.usage.input_tokens; ph["output_tokens"] += r.usage.output_tokens
        CALLS.append({"phase": CUR["phase"], "in": r.usage.input_tokens, "out": r.usage.output_tokens, "sec": round(dt, 1)})
        history += r.output
        calls = [o for o in r.output if o.type == "function_call"]
        if not calls:
            METRICS["seconds"] += time.time() - t0; _ph()["seconds"] += time.time() - t0
            return r.output_text
        for c in calls:
            METRICS["tool_calls"] += 1
            ph = _ph(); ph["tool_calls"] += 1; ph["tools"][c.name] = ph["tools"].get(c.name, 0) + 1
            try:
                out = TOOLS_PY[c.name](**json.loads(c.arguments or "{}"))
            except Exception as e:  # noqa: BLE001
                out = {"error": str(e)}
            STATE["tool_log"].append({"tool": c.name, "args": c.arguments, "out": out})
            history.append({"type": "function_call_output", "call_id": c.call_id, "output": json.dumps(out, ensure_ascii=False)[:30000]})
    METRICS["seconds"] += time.time() - t0
    return "(최대 단계 초과)"


def numbers_in(text):
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]{3,}", text)}


def tool_numbers():
    s = set()
    for t in STATE["tool_log"]:
        s |= numbers_in(json.dumps(t["out"], ensure_ascii=False))
        s |= {str(abs(int(x))) for x in re.findall(r"-?\d{4,}", json.dumps(t["out"]))}
    return s


# ------------------------------------------------------------------ 실행
def main():
    history, transcript = [], []
    fa = SIM["fixed_answers"]
    onboarding_msg = (f"[2026-12-01 온보딩] 목표: {fa['goal']['name']} {fa['goal']['amount']:,}원, 기한 {fa['goal']['deadline']}, "
                      f"지금 모은 돈 {fa['goal']['current_saved']:,}원. 수입: {fa['income']}. 주거: {fa['housing']}. "
                      f"지키고 싶은 소비: {', '.join(fa['protected'])}. 고민: {fa['concern']}. "
                      "최근 3개월(9~11월) 통합 거래내역을 올렸어요. 분석해 주세요.")
    q0 = len(STATE["questions"])
    reply = agent_turn(history, onboarding_msg)
    transcript.append({"phase": "onboarding", "user": onboarding_msg, "questions": STATE["questions"][q0:], "reply": reply})
    print("=== 온보딩 답변\n" + reply + "\n")

    for rec in RECORDS:
        for day in sorted(DAILY):
            if day <= rec["day"] and day > STATE["today"]:
                STATE["rows"] += DAILY[day]
        STATE["today"] = rec["day"]
        CUR["phase"] = rec["record_id"]
        msg = f"[{rec['written_at'][:10]} 유저 기록 {rec['record_id']}] {rec['text']}"
        q0 = len(STATE["questions"])
        reply = agent_turn(history, msg)
        transcript.append({"phase": "live", "record": rec["record_id"], "user": msg, "questions": STATE["questions"][q0:], "reply": reply})
        print(f"=== {rec['record_id']} ({rec['written_at'][:10]}) {rec['text']}\n{reply}\n")

    # 숫자 근거 검사 (결정적)
    tnums = tool_numbers()
    unsupported = []
    for t in transcript:
        bad = sorted(n for n in numbers_in(t["reply"]) if n not in tnums and not n.startswith("202"))
        if bad: unsupported.append({"phase": t.get("record", t["phase"]), "numbers": bad})

    # 케이스 채점 (LLM 심판 + 정답 사실)
    rec_for_case = {"P1-C05": "R2", "P1-C06": "R1", "P1-C07": "R4", "P1-C08": "R3"}
    facts = {k: truth["facts"][k] for k in ("goal", "levers", "card_bills", "dutch_groups") if k in truth["facts"]}
    results = []
    for c in CASES:
        if c["id"] == "P1-C09":
            results.append({"id": c["id"], "verdict": "PASS", "reason": "기록 없는 날은 에이전트를 호출하지 않는 구조 (하네스에서 보장)"})
            continue
        seg = [t for t in transcript if (t["phase"] == "onboarding" if c["phase"] == "onboarding" else t.get("record") == rec_for_case.get(c["id"]))]
        prompt = ("다음 평가 케이스로 AI 코치의 응답을 채점하라. JSON만: "
                  '{"expect":[true/false...], "must_not_violated":[true/false...], "verdict":"PASS|PARTIAL|FAIL", "reason":"한두 문장"}\n'
                  "PASS=expect 모두 충족 & must_not 위반 없음, PARTIAL=일부 충족 & 위반 없음, FAIL=위반 있음 또는 대부분 미충족.\n"
                  f"케이스: {json.dumps(c, ensure_ascii=False)}\n정답 사실: {json.dumps(facts, ensure_ascii=False)[:6000]}\n"
                  f"AI 응답(질문 포함): {json.dumps(seg, ensure_ascii=False)[:12000]}")
        r = client.responses.create(model=JUDGE_MODEL, input=prompt)
        AUX["judge"]["calls"] += 1; AUX["judge"]["in"] += r.usage.input_tokens; AUX["judge"]["out"] += r.usage.output_tokens
        txt = r.output_text
        try:
            j = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        except Exception:  # noqa: BLE001
            j = {"verdict": "ERROR", "reason": txt[:200]}
        results.append({"id": c["id"], **j})

    print("=== 케이스 채점")
    for x in results:
        print(f'{x["id"]}: {x.get("verdict")} — {x.get("reason")}')
    print("\n=== 숫자 근거 검사 (Tool 결과에 없는 4자리 이상 숫자)")
    print(json.dumps(unsupported, ensure_ascii=False) if unsupported else "없음")
    print("\n=== 질문 수", len(STATE["questions"]), STATE["questions"])
    print("=== 지표(전체)", json.dumps(METRICS, ensure_ascii=False))
    print("=== 단계별")
    for k, v in PHASES.items():
        print(f'{k:10} LLM {v["llm_calls"]:2} · Tool {v["tool_calls"]:2} · 입력 {v["input_tokens"]:>8,} · 출력 {v["output_tokens"]:>6,} · {v["seconds"]:5.1f}초 · {v["tools"]}')
    print("=== 호출별 입력 토큰", [(c["phase"], c["in"]) for c in CALLS])
    print("=== 보조 모델", json.dumps(AUX, ensure_ascii=False))
    runs = Path("eval/runs"); runs.mkdir(parents=True, exist_ok=True)
    out = runs / f"p1_single_{MODEL}_{int(time.time())}.json"
    out.write_text(json.dumps({"model": MODEL, "transcript": transcript, "results": results, "unsupported": unsupported,
                               "metrics": METRICS, "phases": PHASES, "calls": CALLS, "aux": AUX, "questions": STATE["questions"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", out)


if __name__ == "__main__":
    main()
