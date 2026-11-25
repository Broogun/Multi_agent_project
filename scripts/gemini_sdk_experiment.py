"""Gemini SDK 실험 (제공자 검토용, 서비스 코드 아님).

목적: 검색·LLM 제공자를 정하기 전에 Gemini SDK(google-genai)가 우리 계약에 맞는지 확인한다.
  E0 모델 목록      : 이 키로 쓸 수 있는 모델 ID 확인 (docs/06 미확인 "모델")
  E1 기본 생성      : 한국어 응답, 지연 시간, 토큰 사용량
  E2 검색 그라운딩  : 검색 답변 + 검색 제안 HTML + 출처를 SearchPanelEvent 형식으로 만들 수 있는지
  E3 함수 호출      : 에이전트가 계산 함수(B)를 고르고 인자를 정확히 채우는지 (fakes 로 실행)

실행:
    uv sync --group experiment
    # .env 에 GEMINI_API_KEY=... (GEMINI_MODEL 은 비워두면 E0 결과에서 고른다)
    uv run python scripts/gemini_sdk_experiment.py            # 전체
    uv run python scripts/gemini_sdk_experiment.py --only E2  # 하나만
    uv run python scripts/gemini_sdk_experiment.py --model gemini-2.5-flash

저장: out/experiments/gemini-<시각>.json (git 제외)
  - 검색 결과 본문·출처 URL 은 저장하지 않는다 (docs/06: 검색 결과 저장 금지). 길이·개수·유무 같은 메타데이터만 남긴다.
  - 화면 출력은 사람이 확인하는 용도로만.
비용: E2 는 검색 그라운딩 과금 대상이다. 한 번 실행에 그라운딩 호출 2회.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

from spending_agent import contracts, fakes

load_dotenv(ROOT / ".env")
OUT = ROOT / "out" / "experiments"
SEARCH_QUERIES = ["쌀 10kg", "생수 2L 6개"]
SEARCH_PROMPT = (
    "다음 상품을 한국에서 살 수 있는 판매처와 확인 방법을 간단히 정리해줘: {query}\n"
    "규칙: 구체적인 가격을 단정하지 말고 '판매처에서 확인'이라고 안내할 것. "
    "판매처 종류(대형마트, 온라인몰, 공식몰 등)와 비교할 때 볼 점(용량, 배송비) 위주로 5줄 이내."
)


def client():
    from google import genai
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY 가 없습니다. .env 에 넣어주세요 (.env.example 참고).")
    return genai.Client(api_key=key)


def usage(r) -> dict:
    u = getattr(r, "usage_metadata", None)
    if not u:
        return {}
    return {k: getattr(u, k, None) for k in ("prompt_token_count", "candidates_token_count", "total_token_count")}


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, round(time.perf_counter() - t, 2)


# ---------------------------------------------------------------- E0
def e0_models(c) -> dict:
    names = []
    for m in c.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if "generateContent" in actions or not actions:
            names.append(m.name.removeprefix("models/"))
    gem = sorted(n for n in names if n.startswith("gemini"))
    print(f"[E0] generateContent 가능 모델 {len(gem)}개")
    for n in gem:
        print("   ", n)
    return {"models": gem}


def pick_model(arg: str | None, models: list[str] | None) -> str:
    if arg:
        return arg
    if os.environ.get("GEMINI_MODEL"):
        return os.environ["GEMINI_MODEL"]
    for pref in ("gemini-3-flash", "gemini-2.5-flash"):
        for m in models or []:
            if m.startswith(pref) and "lite" not in m and "image" not in m and "tts" not in m:
                return m
    return "gemini-2.5-flash"


# ---------------------------------------------------------------- E1
def e1_basic(c, model) -> dict:
    from google.genai import types
    r, sec = timed(lambda: c.models.generate_content(
        model=model, contents="월급 250만 원인 사회초년생에게 소비를 점검하는 질문 3개를 짧게 해줘.",
        config=types.GenerateContentConfig(temperature=0.2)))
    print(f"\n[E1] 기본 생성 ({sec}s)\n{r.text}")
    return {"ok": bool(r.text), "latency_s": sec, "usage": usage(r), "text_len": len(r.text or "")}


# ---------------------------------------------------------------- E2
def to_panel(r, query: str) -> contracts.SearchPanelEvent:
    meta = r.candidates[0].grounding_metadata if r.candidates else None
    sources = [{"title": ch.web.title, "uri": ch.web.uri}
               for ch in ((meta.grounding_chunks or []) if meta else []) if ch.web]
    return {"type": "search_panel", "panel_id": "exp00001", "query": query, "text": r.text or "",
            "suggestions_html": meta.search_entry_point.rendered_content if meta and meta.search_entry_point else None,
            "sources": sources, "notice": contracts.SEARCH_NOTICE}


def e2_grounding(c, model) -> dict:
    from google.genai import types
    runs = []
    for q in SEARCH_QUERIES:
        try:
            r, sec = timed(lambda q=q: c.models.generate_content(
                model=model, contents=SEARCH_PROMPT.format(query=q),
                config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())],
                                                   temperature=0.2)))
        except Exception as e:  # noqa: BLE001 — 실험: 지역·요금제 제한 등 어떤 실패든 기록
            print(f"\n[E2] {q}: 실패 {type(e).__name__}: {e}")
            runs.append({"query": q, "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"})
            continue
        panel = to_panel(r, q)
        meta = r.candidates[0].grounding_metadata if r.candidates else None
        redirect = sum("vertexaisearch.cloud.google.com" in s["uri"] or "grounding-api-redirect" in s["uri"]
                       for s in panel["sources"])
        print(f"\n[E2] 검색 그라운딩 '{q}' ({sec}s) — 화면 확인용, 저장하지 않음")
        print("┌─ 검색 창 미리보기")
        for line in panel["text"].splitlines():
            print("│ " + line)
        for s in panel["sources"]:
            print(f"│  - {s['title']}: {s['uri'][:90]}")
        print(f"│ 검색 제안 HTML: {'있음' if panel['suggestions_html'] else '없음'} · {panel['notice']}")
        print("└─")
        runs.append({
            "query": q, "ok": True, "latency_s": sec, "usage": usage(r),
            "contract_keys_ok": set(panel) == set(contracts.SearchPanelEvent.__annotations__),
            "text_len": len(panel["text"]), "has_price_like_number": any(ch.isdigit() for ch in panel["text"]),
            "sources": len(panel["sources"]), "redirect_style_links": redirect,
            "has_suggestions_html": bool(panel["suggestions_html"]),
            "web_search_queries": len(meta.web_search_queries or []) if meta else 0,
            "grounding_supports": len(meta.grounding_supports or []) if meta else 0,
        })
    return {"runs": runs}


# ---------------------------------------------------------------- E3
def e3_function_calling(c, model) -> dict:
    from google.genai import types

    def calc_bulk_purchase(options: list[dict], daily_use: float, shelf_life_days: int | None = None) -> dict:
        """대용량 구매 판단. options: [{name, price, quantity, unit_size, unit, source_type}], daily_use: 하루 사용량(같은 단위)."""
        return fakes.fake_calc_bulk_purchase(options, daily_use, shelf_life_days)

    def forecast_goal(goal_amount: int, deadline: str, current_saved: int = 0) -> dict:
        """재무 목표 점검. goal_amount 원, deadline YYYY-MM-DD, current_saved 원."""
        return fakes.fake_forecast_goal(goal_amount, deadline, current_saved)

    cases = [
        ("10개월 뒤 여행 가려고 200만 원 모으고 있어. 지금 50만 원 있어. 기한은 2027-09-30이야.",
         "forecast_goal", {"goal_amount": 2000000, "current_saved": 500000, "deadline": "2027-09-30"}),
        ("내가 직접 마트에서 확인했는데 쌀 1kg 2,800원, 3kg 6,900원이야. 하루 100g 먹어. 뭐가 나아?",
         "calc_bulk_purchase", {"daily_use": 100}),
    ]
    decls = [types.FunctionDeclaration.from_callable(client=c._api_client, callable=f, use_json_schema=True)
             for f in (calc_bulk_purchase, forecast_goal)]
    cfg = types.GenerateContentConfig(
        system_instruction="너는 소비 관리 에이전트다. 숫자는 직접 계산하지 말고 반드시 함수를 호출한다.",
        tools=[types.Tool(function_declarations=decls)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True), temperature=0)
    results = []
    for msg, want, want_args in cases:
        r, sec = timed(lambda msg=msg: c.models.generate_content(model=model, contents=msg, config=cfg))
        calls = [{"name": fc.name, "args": dict(fc.args or {})} for fc in (r.function_calls or [])]
        got = calls[0] if calls else None
        args_ok = bool(got) and all(got["args"].get(k) == v for k, v in want_args.items())
        opts = (got or {}).get("args", {}).get("options") or []
        print(f"\n[E3] '{msg[:30]}…' ({sec}s) → {json.dumps(calls, ensure_ascii=False)}")
        results.append({"want": want, "got": got and got["name"], "name_ok": bool(got) and got["name"] == want,
                        "args_ok": args_ok, "options_parsed": len(opts),
                        "answered_without_tool": bool(r.text) and not calls, "latency_s": sec, "usage": usage(r)})
    return {"cases": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["E0", "E1", "E2", "E3"])
    ap.add_argument("--model")
    a = ap.parse_args()
    c = client()
    report = {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
              "sdk": __import__("importlib.metadata").metadata.version("google-genai")}
    models = None
    if a.only in (None, "E0"):
        report["E0"] = e0_models(c)
        models = report["E0"]["models"]
    model = pick_model(a.model, models)
    report["model"] = model
    print(f"\n사용 모델: {model}")
    for name, fn in (("E1", e1_basic), ("E2", e2_grounding), ("E3", e3_function_calling)):
        if a.only in (None, name):
            try:
                report[name] = fn(c, model)
            except Exception as e:  # noqa: BLE001 — 실험: 실패도 결과로 기록
                print(f"\n[{name}] 실패 {type(e).__name__}: {e}")
                report[name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"gemini-{dt.datetime.now():%y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n메타데이터 저장: {path.relative_to(ROOT)} (검색 본문·URL 제외)")


if __name__ == "__main__":
    main()
