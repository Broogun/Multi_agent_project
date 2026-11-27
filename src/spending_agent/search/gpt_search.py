"""GPT 웹 검색 어댑터: 검색 요청 → 링크 카드(SearchPanelEvent).

- OpenAI Responses API 의 web_search 도구를 허용 도메인 필터와 함께 부른다.
  `uv run --with openai ...` 로 쓴다 (기본 의존성에 넣지 않음). 키는 .env 의 OPENAI_API_KEY.
- **링크만 보여 준다.** 모델이 쓴 요약 문장은 버리고, 출처(URL·제목)만 꺼낸다.
  화면 문구는 고정 문장이다 (LLM 문장을 그대로 띄우지 않는다).
- 표시 전 필터 (docs/06 '표시 전 필터', 규칙 코드):
  허용 도메인 밖 제거 · 추적 파라미터 제거 · 같은 주소 하나로 · 공식 출처 먼저.
- 결과는 저장하지 않고, 에이전트 입력·state 에 넣지 않는다. 호출부는 panel_id 만 에이전트에 돌려준다.
- 링크된 페이지를 열지 않는다 (요청도 하지 않는다).
"""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..contracts import SEARCH_NOTICE, SearchPanelEvent, SearchSource

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {"gclid", "fbclid", "ref", "ref_src", "source"}
MAX_LINKS = 8
PANEL_TEXT = "이 조건으로 찾은 링크예요. 실제 요금과 조건은 각 사이트에서 확인하세요."
EMPTY_TEXT = "조건에 맞는 링크를 찾지 못했어요. 조건을 바꿔 다시 찾아볼까요?"
INSTRUCTIONS = ("허용된 사이트에서 조건에 맞는 요금제 안내 페이지를 찾아라. 가격을 단정하지 말고, "
                "판매처에서 확인하라고 안내하라.")


def clean_url(url: str) -> str:
    """추적 파라미터를 지우고 조각(#)을 뺀다. 리다이렉트를 따라가지는 않는다 (링크를 열지 않는다)."""
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith(TRACKING_PREFIXES) and k.lower() not in TRACKING_KEYS]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, urlencode(q), ""))


def _host(url: str) -> str:
    h = urlsplit(url).hostname or ""
    return h.lower().removeprefix("www.")


def in_allowed(url: str, allowed: list[str]) -> bool:
    """허용 도메인 또는 그 하위 도메인인가 (필터가 일부 무시된다는 제보가 있어 코드로 다시 거른다)."""
    h = _host(url)
    return any(h == d or h.endswith("." + d) for d in allowed)


def filter_sources(raw: list[dict], allowed: list[str], official: set[str] | None = None) -> list[SearchSource]:
    """표시 전 필터. raw: [{"url", "title"}]. 순서는 공식 먼저, 그다음 원래 순서."""
    official = official or set()
    seen: set[str] = set()
    out: list[tuple[int, int, SearchSource]] = []
    for i, r in enumerate(raw):
        url = r.get("url") or ""
        if not url.startswith(("http://", "https://")) or not in_allowed(url, allowed):
            continue
        u = clean_url(url)
        sp = urlsplit(u)
        key = f"{_host(u)}{sp.path.rstrip('/')}?{sp.query}"     # www 유무·끝 / 만 다른 주소는 같은 링크로 본다
        if key in seen:
            continue
        seen.add(key)
        title = (r.get("title") or "").strip() or _host(u)
        rank = 0 if any(_host(u) == d or _host(u).endswith("." + d) for d in official) else 1
        out.append((rank, i, {"title": title, "uri": u}))
    out.sort(key=lambda t: (t[0], t[1]))
    return [s for _, _, s in out][:MAX_LINKS]


def _get(o: Any, k: str, default=None):
    return o.get(k, default) if isinstance(o, dict) else getattr(o, k, default)


def extract_sources(response: Any) -> list[dict]:
    """Responses API 응답에서 출처만 꺼낸다: 검색 호출의 sources 와 답변의 url_citation 주석. 요약 문장은 버린다."""
    raw: list[dict] = []
    for item in _get(response, "output", []) or []:
        kind = _get(item, "type")
        if kind == "web_search_call":
            for s in _get(_get(item, "action", {}) or {}, "sources", []) or []:
                raw.append({"url": _get(s, "url"), "title": _get(s, "title")})
        elif kind == "message":
            for c in _get(item, "content", []) or []:
                for a in _get(c, "annotations", []) or []:
                    if _get(a, "type") == "url_citation":
                        raw.append({"url": _get(a, "url"), "title": _get(a, "title")})
    return raw


def panel_id(query: str, sources: list[SearchSource]) -> str:
    return hashlib.sha1((query + "|" + "|".join(s["uri"] for s in sources)).encode()).hexdigest()[:12]


class GPTSearch:
    """사용 예: GPTSearch(model="gpt-...").panel(req["query"], req["allowed_domains"])"""

    def __init__(self, model: str, client: Any = None, official: set[str] | None = None):
        if client is None:
            from openai import OpenAI  # 선택 설치

            from .. import config  # noqa: F401  (.env 로딩)
            client = OpenAI()
        self.model, self.client, self.official = model, client, official or set()

    def panel(self, query: str, allowed_domains: list[str]) -> SearchPanelEvent:
        if not query or not allowed_domains:
            raise ValueError("검색어와 허용 도메인이 있어야 한다 (허용 목록 없이 전체 웹을 찾지 않는다)")
        resp = self.client.responses.create(
            model=self.model,
            tools=[{"type": "web_search", "filters": {"allowed_domains": list(allowed_domains)}}],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            instructions=INSTRUCTIONS,
            input=query,
        )
        sources = filter_sources(extract_sources(resp), allowed_domains, self.official)
        return {"type": "search_panel", "panel_id": panel_id(query, sources), "query": query,
                "text": PANEL_TEXT if sources else EMPTY_TEXT, "suggestions_html": None,
                "sources": sources, "notice": SEARCH_NOTICE}
