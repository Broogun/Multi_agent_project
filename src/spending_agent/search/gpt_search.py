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

TRACKING_PREFIXES = ("utm_", "gad_")
TRACKING_KEYS = {"gclid", "gbraid", "wbraid", "fbclid", "ref", "ref_src", "source"}
MAX_LINKS = 8
# 같은 페이지의 모바일·API 주소는 하나로 본다
HOST_ALIASES = ("www.", "m.", "mobile.", "api.")
VIEW_PARAMS = {"type"}            # 화면 전환용 파라미터 (같은 페이지)
# 파일·첨부는 요금제 안내가 아니라 뺀다
FILE_HINTS = ("download", "/attach/", "/upload/")
FILE_EXTS = (".pdf", ".hwp", ".hwpx", ".zip", ".xlsx", ".xls", ".doc", ".docx", ".ppt", ".pptx")
# 경로 허용 목록: 도메인 전체가 아니라 요금제 안내 경로만 통과시킨다 (FAQ·보도자료·이벤트 제외).
# 실제 스모크에서 본 경로만 넣었다. 규칙이 없는 도메인은 경로에 요금제 단서가 있을 때만 통과.
PATH_RULES: dict[str, tuple[str, ...]] = {
    "smartchoice.or.kr": ("/smc/plan/",),
    "mvnohub.kr": ("/product/products",),
    "ktmmobile.com": ("feeplandetail", "feeplanlist"),
    "uplusumobile.com": ("/product/pric/",),
    "sk7mobile.com": ("/prod/data/callingplan",),
    "liivm.com": ("/rateplan/plans",),                 # /info/rateplan/ 은 판매중단 안내라 제외
    "direct.lghellovision.net": ("/rate/", "/usim/", "/esim/"),   # /event/ · /mgm/ · /with/ 제외
    "kt.com": ("/wdic/productdetail", "/wdic/index"),
}
PLAN_HINTS = ("plan", "charge", "rate", "fee", "product", "요금")
# 요금제 경로여도 일반 사용자에게 맞지 않는 페이지 (판매중단 · 법인)
EXCLUDE_HINTS = ("closed", "/corp", "infolte", "inforeliable")
# 페이지를 열 수 없어서, 제목이 없을 때는 주소 경로로 이름을 붙인다 (경로 이름 기반, 확인된 페이지 제목이 아님)
SITE_NAMES = {"smartchoice.or.kr": "스마트초이스", "mvnohub.kr": "알뜰폰허브", "ktmmobile.com": "KT M모바일",
              "uplusumobile.com": "U+유모바일", "sk7mobile.com": "SK7모바일", "liivm.com": "KB리브모바일",
              "direct.lghellovision.net": "LG헬로모바일",
              "kt.com": "KT", "sktelecom.com": "SKT", "lguplus.com": "LG U+"}
PATH_LABELS = (("plancompare", "요금제 비교"), ("catalog", "요금제 목록"), ("recomend_search", "맞춤 요금제 찾기"),
               ("prepay", "선불 요금제"), ("productdetail", "상품 상세"), ("feeplandetail", "요금제 상세"),
               ("callingplanview", "요금제 상세"), ("pricdetail", "요금제 상세"), ("callingplanlist", "요금제 목록"),
               ("priclist", "요금제 목록"), ("products", "요금제 목록"), ("/rateplan/plans", "요금제"),
               ("limit-plans", "요금제"), ("rateviewusim", "유심 요금제"), ("rateplan", "요금제 찾기"),
               ("usimview", "유심 요금제"), ("everythingesim", "eSIM 요금제"))
PANEL_TEXT = "이 조건으로 찾은 링크예요. 실제 요금과 조건은 각 사이트에서 확인하세요."
EMPTY_TEXT = "조건에 맞는 링크를 찾지 못했어요. 조건을 바꿔 다시 찾아볼까요?"
# 고정 링크: 검색 없이 항상 먼저 보여 주는 공공 비교 페이지 (비용 0, 매번 같음).
# 261127 실제 검색 결과에서 나온 주소만 넣었다. 주소가 바뀌면 주기 검사(L3)로 고친다.
PINNED: dict[str, list[SearchSource]] = {
    "mobile_plan": [
        {"title": "스마트초이스 요금제 비교", "uri": "https://www.smartchoice.or.kr/smc/plan/planCompare.do"},
        {"title": "알뜰폰허브 요금제 목록", "uri": "https://www.mvnohub.kr/product/products.do"},
    ],
}
PINNED_TEXT = "공공 요금제 비교 사이트예요. 실제 요금과 조건은 각 사이트에서 확인하세요."
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


def _base_host(url: str) -> str:
    """m. · www. · api. 를 뗀 호스트 (같은 페이지 판단용)."""
    h = (urlsplit(url).hostname or "").lower()
    changed = True
    while changed:
        changed = False
        for a in HOST_ALIASES:
            if h.startswith(a) and h.count(".") > 1:
                h, changed = h[len(a):], True
    return h


def page_key(url: str) -> str:
    sp = urlsplit(url)
    q = sorted((k, v) for k, v in parse_qsl(sp.query, keep_blank_values=True) if k.lower() not in VIEW_PARAMS)
    return f"{_base_host(url)}{sp.path.rstrip('/').lower()}?{urlencode(q)}"


def is_file_or_landing(url: str) -> bool:
    """파일 다운로드·첨부·홈 화면은 요금제 안내 링크가 아니다."""
    sp = urlsplit(url)
    low = (sp.path + "?" + sp.query).lower()
    if any(h in low for h in FILE_HINTS) or sp.path.lower().endswith(FILE_EXTS):
        return True
    segs = [x for x in sp.path.split("/") if x]
    return len(segs) <= 1 and not sp.query and (not segs or sp.path.endswith("/"))   # 홈 · 첫 화면 (/, /smc/)


def path_allowed(url: str, rules: dict[str, tuple[str, ...]] | None = None) -> bool:
    """도메인별 경로 규칙이 있으면 그 경로만, 없으면 요금제 단서가 있는 경로만 통과."""
    rules = PATH_RULES if rules is None else rules
    h = _host(url)
    path = urlsplit(url).path.lower()
    if any(x in path for x in EXCLUDE_HINTS):
        return False
    for d, prefixes in rules.items():
        if h == d or h.endswith("." + d):
            return any(path.startswith(x) or x in path for x in prefixes)
    return any(k in path for k in PLAN_HINTS)


def label_for(url: str, allowed: list[str]) -> str:
    h = _host(url)
    site = next((SITE_NAMES.get(d, d) for d in allowed if h == d or h.endswith("." + d)), h)
    path = urlsplit(url).path.lower()
    page = next((name for key, name in PATH_LABELS if key in path), "페이지")
    return f"{site} {page}"


def in_allowed(url: str, allowed: list[str]) -> bool:
    """허용 도메인 또는 그 하위 도메인인가 (필터가 일부 무시된다는 제보가 있어 코드로 다시 거른다)."""
    h = _host(url)
    return any(h == d or h.endswith("." + d) for d in allowed)


def filter_sources(raw: list[dict], allowed: list[str], official: set[str] | None = None,
                   path_rules: dict[str, tuple[str, ...]] | None = None) -> list[SearchSource]:
    """표시 전 필터. raw: [{"url", "title"}]. 순서는 공식 먼저, 그다음 원래 순서."""
    official = official or set()
    titles: dict[str, str] = {}               # 같은 페이지에 제목이 있는 출처가 있으면 그 제목을 쓴다
    for r in raw:
        t, url = (r.get("title") or "").strip(), r.get("url") or ""
        if t and url and t != url and not t.startswith("http"):
            titles.setdefault(page_key(clean_url(url)), t)
    seen: set[str] = set()
    out: list[tuple[int, int, SearchSource]] = []
    for i, r in enumerate(raw):
        url = r.get("url") or ""
        if not url.startswith(("http://", "https://")) or not in_allowed(url, allowed):
            continue
        u = clean_url(url)
        if is_file_or_landing(u) or not path_allowed(u, path_rules):
            continue
        key = page_key(u)                    # m.·www.·api.·type=m 만 다른 주소는 같은 페이지
        if key in seen:
            continue
        seen.add(key)
        title = titles.get(key) or label_for(u, allowed)
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


def merge_pinned(pinned: list[SearchSource], found: list[SearchSource]) -> list[SearchSource]:
    """고정 링크를 앞에 두고, 검색 결과에서 같은 페이지는 뺀다."""
    keys = {page_key(x["uri"]) for x in pinned}
    return list(pinned) + [x for x in found if page_key(x["uri"]) not in keys][:MAX_LINKS]


def pinned_panel(topic: str) -> SearchPanelEvent:
    """검색 없이 고정 링크만 띄운다 (API 호출 없음)."""
    links = list(PINNED.get(topic, []))
    return {"type": "search_panel", "panel_id": panel_id(topic, links), "query": topic,
            "text": PINNED_TEXT if links else EMPTY_TEXT, "suggestions_html": None, "sources": links,
            "notice": SEARCH_NOTICE}


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

    def panel(self, query: str, allowed_domains: list[str], topic: str | None = None) -> SearchPanelEvent:
        """topic 이 있으면 그 고정 링크를 먼저 두고 검색 결과를 뒤에 붙인다 ("더 찾아보기")."""
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
        pinned = PINNED.get(topic or "", [])
        if pinned:
            sources = merge_pinned(pinned, sources)
        return {"type": "search_panel", "panel_id": panel_id(query, sources), "query": query,
                "text": PANEL_TEXT if sources else EMPTY_TEXT, "suggestions_html": None,
                "sources": sources, "notice": SEARCH_NOTICE}
