"""GPT 검색 어댑터: 실제 API 는 부르지 않는다 (가짜 client)."""
from types import SimpleNamespace as NS

import pytest

from spending_agent.contracts import SEARCH_NOTICE
from spending_agent.search.gpt_search import (
    EMPTY_TEXT,
    PANEL_TEXT,
    GPTSearch,
    clean_url,
    extract_sources,
    filter_sources,
    in_allowed,
    is_file_or_landing,
    page_key,
    path_allowed,
)

ALLOWED = ["smartchoice.or.kr", "kt.com"]


class FakeClient:
    def __init__(self, response):
        self.calls, self._resp = [], response
        self.responses = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return self._resp


def _resp():
    return NS(output=[
        NS(type="web_search_call", action=NS(sources=[
            NS(url="https://www.smartchoice.or.kr/smc/plan/planCompare.do?id=1&utm_source=openai", title="요금제 비교"),
            NS(url="https://blog.example.com/cheap-plan", title="블로그"),        # 허용 밖
        ])),
        NS(type="message", content=[NS(text="이 요금제는 월 9,900원입니다.", annotations=[
            NS(type="url_citation", url="https://product.kt.com/wDic/productDetail.do?ItemCode=7#top", title="KT 요금제"),
            NS(type="url_citation", url="https://smartchoice.or.kr/smc/plan/planCompare.do?id=1", title="중복"),
        ])]),
    ])


def test_clean_url_strips_tracking_and_fragment():
    assert clean_url("https://WWW.a.com/p?id=1&utm_source=openai&gclid=x#f") == "https://www.a.com/p?id=1"


def test_allowed_includes_subdomains_only():
    assert in_allowed("https://shop.kt.com/x", ALLOWED)
    assert not in_allowed("https://notkt.com/x", ALLOWED) and not in_allowed("https://kt.com.evil.io/", ALLOWED)


def test_filter_drops_outside_dedups_and_puts_official_first():
    raw = extract_sources(_resp())
    out = filter_sources(raw, ALLOWED, official={"smartchoice.or.kr"})
    assert [s["uri"] for s in out] == ["https://www.smartchoice.or.kr/smc/plan/planCompare.do?id=1",
                                       "https://product.kt.com/wDic/productDetail.do?ItemCode=7"]
    assert all("blog" not in s["uri"] for s in out)


def test_panel_shows_links_only_not_model_text():
    client = FakeClient(_resp())
    ev = GPTSearch(model="m", client=client, official={"smartchoice.or.kr"}).panel("알뜰폰 요금제 월 10GB 이상", ALLOWED)
    assert ev["type"] == "search_panel" and ev["text"] == PANEL_TEXT and ev["notice"] == SEARCH_NOTICE
    assert "9,900" not in str(ev)                                       # 모델이 쓴 가격 문장은 버린다
    assert ev["suggestions_html"] is None and ev["sources"] and len(ev["panel_id"]) == 12
    call = client.calls[0]
    assert call["tools"][0]["filters"]["allowed_domains"] == ALLOWED and call["input"] == "알뜰폰 요금제 월 10GB 이상"


def test_empty_result_message():
    ev = GPTSearch(model="m", client=FakeClient(NS(output=[]))).panel("q", ALLOWED)
    assert ev["sources"] == [] and ev["text"] == EMPTY_TEXT


def test_requires_allowed_domains():
    with pytest.raises(ValueError):
        GPTSearch(model="m", client=FakeClient(NS(output=[]))).panel("q", [])


def test_same_page_on_mobile_api_hosts_is_one_link():
    urls = ["https://m.smartchoice.or.kr/smc/plan/planCompare.do", "https://api.smartchoice.or.kr/smc/plan/planCompare.do",
            "https://smartchoice.or.kr/smc/plan/planCompare.do?type=m", "https://www.smartchoice.or.kr/smc/plan/planCompare.do/"]
    assert len({page_key(u) for u in urls}) == 1
    out = filter_sources([{"url": u, "title": ""} for u in urls], ALLOWED)
    assert len(out) == 1


def test_files_attachments_and_home_are_dropped():
    for u in ["https://smartchoice.or.kr/smc/download.do?fileName=a.pdf&path=x", "https://m.corp.kt.com/attach/board/1/2/a",
              "https://smartchoice.or.kr/smc/resources/upload/guide/a.pdf", "https://m.smartchoice.or.kr/smc/",
              "https://m.product.kt.com/"]:
        assert is_file_or_landing(u), u
    assert not is_file_or_landing("https://product.kt.com/wDic/productDetail.do?ItemCode=1013")


def test_titles_from_citation_or_path_label():
    raw = [{"url": "https://m.smartchoice.or.kr/smc/plan/planCompare.do", "title": ""},
           {"url": "https://smartchoice.or.kr/smc/plan/planCompare.do", "title": "요금제 비교 검색"},
           {"url": "https://product.kt.com/wDic/productDetail.do?ItemCode=1", "title": ""},
           {"url": "https://product.kt.com/wDic/index.do?CateCode=1", "title": "https://kt.com/some"}]
    out = filter_sources(raw, ALLOWED)
    assert [s["title"] for s in out] == ["요금제 비교 검색", "KT 상품 상세", "KT 페이지"]


def test_path_allow_list_keeps_plan_pages_only():
    ok = ["https://m.smartchoice.or.kr/smc/plan/planCompare.do", "https://product.kt.com/wDic/productDetail.do?ItemCode=1",
          "https://www.lguplus.com/mobile/plan/mplan/5g-all"]
    bad = ["https://api.smartchoice.or.kr/smc/support/faq.do?tab=B", "https://ermsweb.kt.com/mobile/faq/faqSeg13_1.do",
           "https://m.lguplus.com/about/en/corporation/promotion/press-kit/detail?atclNo=1",
           "https://www.lguplus.com/benefit-event/ongoing/82173"]
    assert all(path_allowed(u) for u in ok)
    assert not any(path_allowed(u) for u in bad)
