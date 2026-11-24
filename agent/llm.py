"""Gemini 호출 래퍼. 그래프는 이 인터페이스만 쓰므로 테스트에서는 FakeLLM 으로 바꿔 끼운다.

  chat(system, history, tools)  → LLMReply(text, calls)       에이전트 대화 (함수 호출)
  grounded_search(query)        → GroundedResult              검색 창 전용 (Google 검색 그라운딩)

두 호출은 완전히 분리한다. grounded_search 의 결과는 chat 의 history 로 절대 들어가지 않는다
(Gemini API 약관: 그라운딩 결과를 수정·분석·캐시하지 않고 검색 제안과 함께 그대로 표시).

환경변수: GEMINI_API_KEY (.env 가능), GEMINI_MODEL (기본 gemini-2.5-flash. 사용 가능 모델은 AI Studio에서 확인)
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "gemini-2.5-flash"

SEARCH_PROMPT = (
    "다음 상품을 한국에서 살 수 있는 판매처와 확인 방법을 간단히 정리해줘: {query}\n"
    "규칙: 구체적인 가격을 단정하지 말고 '판매처에서 확인'이라고 안내할 것. "
    "광고성 문구 없이 판매처 종류(대형마트, 온라인몰, 공식몰 등)와 비교할 때 볼 점(용량, 배송비) 위주로 5줄 이내."
)


@dataclass
class LLMReply:
    text: str = ""
    calls: list[dict] = field(default_factory=list)   # [{"name": str, "args": dict}]


@dataclass
class GroundedResult:
    text: str                       # Gemini 가 검색으로 만든 답변 (수정 금지)
    suggestions_html: str | None    # Google 검색 제안 HTML (반드시 함께 렌더링)
    sources: list[dict]             # [{"title", "uri"}]
    queries: list[str]


class LLM(Protocol):
    def chat(self, system: str, history: list[dict], tools: list[Callable]) -> LLMReply: ...
    def grounded_search(self, query: str) -> GroundedResult: ...


def _load_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    return key


class GeminiLLM:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        from google import genai
        key = api_key or _load_key()
        if not key:
            raise RuntimeError("GEMINI_API_KEY 를 .env 또는 환경변수에 넣어주세요.")
        self.client = genai.Client(api_key=key)
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self._decl_cache: dict[str, Any] = {}

    # ---------------------------------------------------------- 대화 + 함수 호출
    def _declarations(self, tools):
        from google.genai import types
        out = []
        for fn in tools:
            if fn.__name__ not in self._decl_cache:
                d = types.FunctionDeclaration.from_callable(client=self.client._api_client, callable=fn, use_json_schema=True)
                if getattr(fn, "__agent_description__", None):
                    d.description = fn.__agent_description__
                self._decl_cache[fn.__name__] = d
            out.append(self._decl_cache[fn.__name__])
        return out

    def _contents(self, history):
        from google.genai import types
        contents = []
        for h in history:
            if h["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=h["text"])]))
            elif h["role"] == "model":
                parts = [types.Part(text=h["text"])] if h.get("text") else []
                parts += [types.Part(function_call=types.FunctionCall(name=c["name"], args=c["args"])) for c in h.get("calls", [])]
                contents.append(types.Content(role="model", parts=parts))
            elif h["role"] == "tool":
                contents.append(types.Content(role="user", parts=[types.Part(
                    function_response=types.FunctionResponse(name=h["name"], response={"result": h["result"]}))]))
        return contents

    def chat(self, system, history, tools):
        from google.genai import types
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=self._declarations(tools))] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )
        r = self.client.models.generate_content(model=self.model, contents=self._contents(history), config=cfg)
        calls = [{"name": fc.name, "args": dict(fc.args or {})} for fc in (r.function_calls or [])]
        text = "" if calls else (r.text or "")
        return LLMReply(text=text, calls=calls)

    # ---------------------------------------------------------- 검색 창 전용 그라운딩
    def grounded_search(self, query):
        from google.genai import types
        cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.2)
        r = self.client.models.generate_content(model=self.model, contents=SEARCH_PROMPT.format(query=query), config=cfg)
        meta = r.candidates[0].grounding_metadata if r.candidates else None
        sources = []
        for ch in (meta.grounding_chunks or []) if meta else []:
            if ch.web:
                sources.append({"title": ch.web.title, "uri": ch.web.uri})
        return GroundedResult(
            text=r.text or "",
            suggestions_html=meta.search_entry_point.rendered_content if meta and meta.search_entry_point else None,
            sources=sources,
            queries=list(meta.web_search_queries or []) if meta else [],
        )


class FakeLLM:
    """테스트용. script: chat 호출마다 돌려줄 LLMReply 목록."""

    def __init__(self, script: list[LLMReply], search_text: str = "가짜 검색 요약: ○○마트, △△몰에서 판매"):
        self.script = list(script)
        self.search_text = search_text
        self.chat_inputs: list[list[dict]] = []
        self.search_queries: list[str] = []

    def chat(self, system, history, tools):
        self.chat_inputs.append([dict(h) for h in history])
        return self.script.pop(0) if self.script else LLMReply(text="(스크립트 끝)")

    def grounded_search(self, query):
        self.search_queries.append(query)
        return GroundedResult(text=self.search_text, suggestions_html="<div>검색 제안</div>",
                              sources=[{"title": "example.com", "uri": "https://example.com"}], queries=[query])
