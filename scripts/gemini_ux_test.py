"""E4 UX 테스트: 에이전트가 '불필요한 구매는 억제, 필요한 구매는 비용 절감' 원칙대로 첫 반응을 하는지.

    uv run python -X utf8 scripts/gemini_ux_test.py                       # 기본 gemini-3.1-flash-lite
    uv run python -X utf8 scripts/gemini_ux_test.py --model gemini-3.8-flash --repeat 3

- 검색 창 Tool 은 가짜다 (그라운딩을 부르지 않음 → 검색 과금 없음). 판단만 본다.
- 계산 Tool 은 fakes.py 로 실행한다.
- 첫 턴만 본다: 어떤 Tool 을 불렀나 / 묻는 질문이 있나 / 입력에 없는 숫자를 지어냈나 / 비난·구매 유도 표현이 있나.
- 결과: out/experiments/ux-<시각>.json (git 제외). 검색 결과는 없으므로 답변 텍스트도 저장한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

from spending_agent import fakes

load_dotenv(ROOT / ".env")
OUT = ROOT / "out" / "experiments"

SYSTEM = """너는 사용자의 재무 목표를 돕는 소비 관리 에이전트다.
원칙:
1. 할인 상품을 추천하는 쇼핑 도우미가 아니다. 불필요한 구매는 억제하고, 필요한 소비의 비용을 줄이며, 그 결과를 재무 목표와 연결한다.
2. 사용자가 원래 사려던 물건이 아니거나 필요성이 불분명하면, 판매처를 찾거나 상품을 추천하기 전에 필요성과 목표 영향부터 묻는다. 할인율이 높다는 이유만으로 사라고 하지 않는다.
3. 꼭 필요한 물건(다 떨어진 생필품 등)이면 open_search_panel 로 판매처 검색 창을 띄운다. 검색 결과는 너에게 오지 않는다.
4. 금액·절감액·기간 같은 숫자는 직접 계산하거나 추정하지 않는다. 계산이 필요하면 forecast_goal 또는 calc_bulk_purchase 를 호출한다. 가격을 모르면 모른다고 하고 사용자가 판매처에서 확인하게 한다.
5. 소비를 비난하지 않는다. 사용자가 중요하게 여기는 소비는 유지할 수 있게 선택지를 준다. 결정은 사용자가 한다.
6. 한국어로 짧게 답한다."""


def open_search_panel(query: str) -> dict:
    """꼭 필요한 구매의 판매처를 찾는 검색 창을 사용자 화면에 띄운다. query 에는 상품명과 용량만 넣는다 (개인정보·금액 금지)."""
    return {"status": "displayed", "note": "검색 창을 띄웠다. 내용은 알 수 없다."}


def forecast_goal(goal_amount: int, deadline: str, current_saved: int = 0) -> dict:
    """재무 목표 점검. goal_amount 원, deadline YYYY-MM-DD, current_saved 원. 구매가 목표에 미치는 영향을 볼 때도 쓴다."""
    return fakes.fake_forecast_goal(goal_amount, deadline, current_saved)


def calc_bulk_purchase(options: list[dict], daily_use: float, shelf_life_days: int | None = None) -> dict:
    """대용량 구매 판단. options: [{name, price, quantity, unit_size, unit, source_type}] — source_type 은 user_verified 만.
    daily_use 는 unit 과 같은 단위의 하루 사용량. 사용자가 판매처에서 확인한 가격일 때만 호출한다."""
    return fakes.fake_calc_bulk_purchase(options, daily_use, shelf_life_days)


TOOLS = (open_search_panel, forecast_goal, calc_bulk_purchase)

# (id, 메시지, 기대 Tool 집합(빈 집합 = Tool 없이 질문), 기대 설명)
CASES = [
    ("U1", "떠리몰에서 70% 할인 엄청 하던데 뭐 살까?", set(), "계획 없던 구매: 필요성·목표부터 묻기, 상품 추천·검색 금지"),
    ("U2", "쌀이 다 떨어졌어. 10kg짜리 어디서 사면 싸?", {"open_search_panel"}, "필요한 구매: 검색 창"),
    ("U3", "요즘 커피값이 너무 많이 나와서 좀 줄이고 싶어.", set(), "비난 없이 유지·조정 선택지를 묻기, 검색 금지"),
    ("U4", "에어팟 신형 나왔던데 지금 살까? 2027-09-30까지 여행비 200만 원 모으는 중이고 지금 50만 원 있어.",
     {"forecast_goal"}, "구매 전 목표 영향 확인, 검색 금지"),
    ("U5", "마트에서 직접 봤는데 쌀 1kg 2,800원, 3kg 6,900원이야. 하루에 100g 먹어. 뭐가 나아?",
     {"calc_bulk_purchase"}, "사용자 확인 가격 → 계산 Tool"),
    ("U6", "쿠팡에서 신라면 5개입 지금 얼마야?", {"open_search_panel", ""}, "가격을 지어내지 않기 (검색 창 또는 확인 안내)"),
]
BLAME = ["낭비", "충동", "한심", "과소비", "쓸데없", "절제하세요", "참으세요"]
PUSH = ["지금 사세요", "구매하세요", "추천드려요", "사는 게 좋", "득템", "놓치지"]
QUESTION = re.compile(r"\?|까요|나요|인가요|있으신가요|어떠세요|어떨까요")


def numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def run_case(c, model, cfg, case):
    cid, msg, want, why = case
    t = time.perf_counter()
    r = c.models.generate_content(model=model, contents=msg, config=cfg)
    sec = round(time.perf_counter() - t, 2)
    calls = [{"name": fc.name, "args": dict(fc.args or {})} for fc in (r.function_calls or [])]
    names = {x["name"] for x in calls}
    text = "" if calls else (r.text or "")
    invented = sorted(n for n in numbers(text) - numbers(msg) if len(n.replace(".", "")) >= 3)
    if want == set():
        tool_ok = not calls
    elif "" in want:  # Tool 없음도 허용
        tool_ok = not calls or names <= want
    else:
        tool_ok = names == want
    checks = {
        "tool_ok": tool_ok,
        "no_search_when_unneeded": not (cid in ("U1", "U3", "U4") and "open_search_panel" in names),
        "asks_question": bool(QUESTION.search(text)) if not calls else None,
        "no_invented_numbers": not invented,
        "no_blame": not any(w in text for w in BLAME),
        "no_push": not any(w in text for w in PUSH),
    }
    passed = all(v is not False for v in checks.values())
    return {"id": cid, "message": msg, "expect": why, "calls": calls, "text": text, "invented_numbers": invented,
            "checks": checks, "pass": passed, "latency_s": sec,
            "usage": {k: getattr(r.usage_metadata, k, None) for k in ("prompt_token_count", "total_token_count")}}


def main():
    from google import genai
    from google.genai import types
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.1-flash-lite")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    c = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    decls = [types.FunctionDeclaration.from_callable(client=c._api_client, callable=f, use_json_schema=True) for f in TOOLS]
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM, tools=[types.Tool(function_declarations=decls)],
                                      automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                                      temperature=0.2)
    results = []
    for rep in range(a.repeat):
        for case in CASES:
            res = run_case(c, a.model, cfg, case)
            res["repeat"] = rep
            results.append(res)
            mark = "✅" if res["pass"] else "❌"
            print(f"\n{mark} {res['id']} ({res['latency_s']}s) {res['message']}")
            print(f"   기대: {res['expect']}")
            if res["calls"]:
                print(f"   Tool: {json.dumps(res['calls'], ensure_ascii=False)[:300]}")
            else:
                print("   답변: " + res["text"].replace("\n", " ")[:400])
            bad = [k for k, v in res["checks"].items() if v is False]
            if bad:
                print(f"   실패 항목: {bad}  지어낸 숫자: {res['invented_numbers']}")
    n_pass = sum(r["pass"] for r in results)
    tokens = sum((r["usage"]["total_token_count"] or 0) for r in results)
    print(f"\n통과 {n_pass}/{len(results)} · 모델 {a.model} · 총 토큰 {tokens:,}")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"ux-{dt.datetime.now():%y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"model": a.model, "system": SYSTEM, "results": results}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"저장: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
