"""전문 검색 에이전트 프로토타입 (A, 실험 단계).

    uv sync --group experiment
    uv run python -X utf8 -m spending_agent.search.specialists "자취생인데 통신비 줄이고 싶어. 데이터 15GB 쓰고 2만원 이하로"

흐름:
  1. route_and_extract  (메인, lite, 검색 없음): 분야 판단 + 사용자 스펙 추출. 필수 스펙이 없으면 되묻는다.
  2. specialist_search  (전문, 3.5-flash + Google 검색): 분야 범위(사이트·관점) + 스펙으로 검색어를 만들어 검색.
  3. refine             (메인, Google 에 재제출): 새 사실 없이 짧게 다듬는다. 약관 예외(함수 호출로 재제출해 개선된
                        그라운딩 결과를 얻는 경우)에 맞춰 중간 결과는 저장하지 않고, 최종 결과에 원래 검색 제안·출처를 붙인다.
  4. SearchPanelEvent 로 반환 → 화면이 그대로 렌더링 (docs/06).

지키는 것 (docs/06, Gemini API 약관):
  - 검색 결과를 저장·로그하지 않는다. 계산 Tool 에 넘기지 않는다 (가격 추출 금지).
  - 출처는 모델이 쓴 문구가 아니라 grounding 메타데이터만 쓴다 (실험에서 모델이 출처를 지어냄).
  - 검색 프롬프트에는 상품 조건만. 개인정보 패턴(전화번호·주민번호·계좌·이메일)은 지운다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from ..contracts import SEARCH_NOTICE, SearchPanelEvent
from .directory import DOMAINS

ROUTER_MODEL = os.environ.get("GEMINI_ROUTER_MODEL", "gemini-3.1-flash-lite")
SEARCH_MODEL = os.environ.get("GEMINI_SEARCH_MODEL", "gemini-3.5-flash")
MAX_SUGGESTIONS = 5
PII = re.compile(r"(\d{2,3}-\d{3,4}-\d{4}|\d{6}-?[1-4]\d{6}|\d{3,6}-\d{2,6}-\d{2,8}|[\w.+-]+@[\w-]+\.[\w.]+)")


# ---------------------------------------------------------------- LLM 인터페이스 (테스트에서 가짜로 교체)
@dataclass
class Grounded:
    text: str
    sources: list[dict]            # [{"title", "uri"}] — grounding 메타데이터에서만
    suggestions_html: str | None
    queries: list[str]


class LLM(Protocol):
    def json(self, model: str, prompt: str) -> dict: ...
    def grounded(self, model: str, prompt: str) -> Grounded: ...
    def text(self, model: str, prompt: str) -> str: ...


class GeminiLLM:
    def __init__(self):
        from dotenv import load_dotenv
        from google import genai
        load_dotenv()
        self.c = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    def json(self, model, prompt):
        from google.genai import types
        r = self.c.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0))
        return json.loads(r.text)

    def grounded(self, model, prompt):
        from google.genai import types
        r = self.c.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.2))
        m = r.candidates[0].grounding_metadata if r.candidates else None
        sources, seen = [], set()
        for ch in (m.grounding_chunks or []) if m else []:
            if ch.web and ch.web.uri not in seen:
                seen.add(ch.web.uri)
                sources.append({"title": ch.web.title or "", "uri": ch.web.uri})
        return Grounded(text=r.text or "", sources=sources,
                        suggestions_html=m.search_entry_point.rendered_content if m and m.search_entry_point else None,
                        queries=list(m.web_search_queries or []) if m else [])

    def text(self, model, prompt):
        from google.genai import types
        r = self.c.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(temperature=0))
        return r.text or ""


# ---------------------------------------------------------------- 1. 분야 판단 + 스펙 추출
ROUTER_PROMPT = """사용자 메시지를 읽고 아래 분야 중 하나를 고르고, 그 분야의 스펙 항목 값을 메시지에서 찾아 JSON으로만 답해라.
메시지에 없는 값은 null. 추측하지 마라. 값은 사용자 표현 그대로 쓴다 (예: "5GB 이하", "2만 원 이하", "300분 이상" — 이하/이상/정도를 지우지 마라). 이름·소득·계좌·정확한 주소 같은 개인정보는 넣지 마라(지역은 구 단위까지).
분야: {domains}
분야별 스펙 키: {spec_keys}
형식: {{"domain": "<키 또는 null>", "spec": {{"<스펙키>": "<값 또는 null>"}}}}

사용자 메시지: {message}"""


def route_and_extract(llm: LLM, message: str) -> dict:
    domains = ", ".join(f"{k}({v['label']})" for k, v in DOMAINS.items())
    spec_keys = {k: [s["key"] for s in v["spec"]] for k, v in DOMAINS.items()}
    out = llm.json(ROUTER_MODEL, ROUTER_PROMPT.format(domains=domains, spec_keys=json.dumps(spec_keys, ensure_ascii=False),
                                                      message=message))
    domain = out.get("domain") if out.get("domain") in DOMAINS else None
    spec = {k: v for k, v in (out.get("spec") or {}).items() if v not in (None, "", "null")} if domain else {}
    if domain:
        allowed = {s["key"] for s in DOMAINS[domain]["spec"]}
        spec = {k: PII.sub("", str(v)) for k, v in spec.items() if k in allowed}
    return {"domain": domain, "spec": spec}


def missing_required(domain: str, spec: dict) -> list[dict]:
    return [s for s in DOMAINS[domain]["spec"] if s["required"] and s["key"] not in spec]


# ---------------------------------------------------------------- 2. 전문 에이전트 검색
SPECIALIST_PROMPT = """너는 {label} 절약 전문 검색 에이전트다. 2026년 현재 한국 기준.
1) 사용자 스펙에 맞는 정보를 찾도록 구체적인 검색어를 직접 만들어 검색하라. 일반론보다 스펙에 맞는 실제 서비스·상품·제도를 찾는다.
2) 아래 사이트 범위를 우선한다 (공식 사이트 자료 우선): {sites}
3) 검색 관점: {angles}
4) 스펙을 만족하는 항목을 최대 5개, 각 항목이 스펙의 어떤 조건을 만족하는지 적어라. 확인되지 않으면 '확인 필요'.
5) 가격·할인 기간은 검색 시점 정보다. 판매처·공식 사이트에서 다시 확인하라고 안내하라.
6) 필요 없는 구매나 충동구매를 부르는 할인 상품 추천은 하지 마라. 본문에 출처 URL을 직접 쓰지 마라.
7) 예산 조건이 있으면 할인 기간 요금과 할인 후 요금을 모두 예산과 비교해 표시하라 (할인 후 초과면 '할인 후 예산 초과').
8) 이미 종료된 제도·행사는 넣지 마라. 종료 여부가 불확실하면 '확인 필요'.
9) 검색어 목록이나 '검색했습니다' 같은 과정 설명은 쓰지 마라. 결과만 쓴다.

사용자 스펙:
{spec}"""


RETRY_PREFIX = "반드시 Google 검색을 먼저 수행해 2026년 현재 공식 자료를 확인한 뒤 답하라. 검색 없이 기억으로 답하지 마라.\n\n"


def build_specialist_prompt(domain: str, spec: dict) -> str:
    d = DOMAINS[domain]
    sites = "; ".join(f"{s['name']}({s['url'] or '공식 페이지'}): {s['what']}" for s in d["sites"])
    spec_lines = "\n".join(f"- {k}: {v}" for k, v in spec.items()) or "- (스펙 없음: 일반적인 방법 위주)"
    return PII.sub("", SPECIALIST_PROMPT.format(label=d["label"], sites=sites, angles=", ".join(d["angles"]), spec=spec_lines))


# ---------------------------------------------------------------- 3. 다듬기 (재제출)
REFINE_PROMPT = """아래 답변을 사용자가 읽기 쉽게 다듬어라. 규칙:
- 새로운 사실·숫자·서비스를 추가하지 마라. 원문에 있는 내용만 쓴다.
- 항목 수와 '확인 필요' 표시, 가격 재확인 안내는 유지한다.
- 출처 URL이나 '[출처: ...]' 문구는 쓰지 마라 (출처는 화면이 따로 보여준다).
- 검색어 목록이나 검색 과정 설명은 빼라.
- 900자 이내, 한국어.

답변:
{text}"""


def refine(llm: LLM, grounded_text: str) -> str:
    return llm.text(ROUTER_MODEL, REFINE_PROMPT.format(text=grounded_text))


# ---------------------------------------------------------------- 전체
@dataclass
class Result:
    status: str                     # need_info | ok | unverified(검색 출처 없음) | out_of_scope
    domain: str | None = None
    spec: dict = field(default_factory=dict)
    question: str | None = None     # need_info 일 때 사용자에게 되물을 말
    panel: SearchPanelEvent | None = None
    queries: list[str] = field(default_factory=list)


def run(llm: LLM, message: str, known_spec: dict | None = None, do_refine: bool = True) -> Result:
    routed = route_and_extract(llm, message)
    domain = routed["domain"]
    if not domain:
        return Result(status="out_of_scope", question="통신비, 장보기, 교통비, 구독, 숨은 돈 중 어떤 걸 줄이고 싶으세요?")
    spec = {**(known_spec or {}), **routed["spec"]}
    missing = missing_required(domain, spec)
    if missing:
        return Result(status="need_info", domain=domain, spec=spec, question=" ".join(m["ask"] for m in missing))
    prompt = build_specialist_prompt(domain, spec)
    g = llm.grounded(SEARCH_MODEL, prompt)
    if not g.sources:  # 모델이 검색을 건너뜀 (실험: 교통비 질문에서 출처 0건인데 '검색했다'고 답함) → 1회 재시도
        g = llm.grounded(SEARCH_MODEL, RETRY_PREFIX + prompt)
    status = "ok" if g.sources else "unverified"
    text = refine(llm, g.text) if do_refine and g.text else g.text
    # 중간 결과(g.text)는 여기서 버린다: 저장·로그하지 않음 (약관 예외 조건 ii)
    panel: SearchPanelEvent = {
        "type": "search_panel", "panel_id": uuid.uuid4().hex[:8],
        "query": ", ".join(f"{k}={v}" for k, v in spec.items())[:80] or DOMAINS[domain]["label"],
        "text": text,
        "suggestions_html": g.suggestions_html,   # 최대 5개 제한은 렌더링 쪽에서 (HTML 수정 금지)
        "sources": g.sources,
        "notice": SEARCH_NOTICE if g.sources else SEARCH_NOTICE + " (이 답변은 검색 출처 없이 생성되었습니다)",
    }
    return Result(status=status, domain=domain, spec=spec, panel=panel, queries=g.queries)


def _print(res: Result):
    print(f"[상태] {res.status} · 분야 {res.domain} · 스펙 {json.dumps(res.spec, ensure_ascii=False)}")
    if res.question:
        print(f"[되묻기] {res.question}")
    if res.panel:
        p = res.panel
        print(f"[검색어] {res.queries}")
        print("┌─ 검색 창")
        for line in p["text"].splitlines():
            print("│ " + line)
        print(f"│ 출처 {len(p['sources'])}건: " + ", ".join(s["title"] for s in p["sources"][:12]))
        print(f"│ 검색 제안 HTML: {'있음' if p['suggestions_html'] else '없음'}")
        print("│ " + p["notice"])
        print("└─")


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) or "자취생인데 통신비 줄이고 싶어"
    llm = GeminiLLM()
    res = run(llm, msg)
    _print(res)
    while res.status == "need_info":
        ans = input("\n나> ").strip()
        if not ans:
            break
        res = run(llm, msg + " " + ans, known_spec=res.spec)
        _print(res)
