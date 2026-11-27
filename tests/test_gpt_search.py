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
    pinned_panel,
    safe_query,
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
    assert call["store"] is False                                       # 서버 저장 안 함
    assert call["reasoning"] == {"effort": "none"}                      # 페이지를 열지 않게


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


def test_mvno_plan_paths_pass_and_events_do_not():
    ok = ["https://www.mvnohub.kr/product/products.do?themeTagIds=270",
          "https://www.ktmmobile.com/m/content/mKtFeePlanDetail.do?contSeq=1137",
          "https://www.uplusumobile.com/product/pric/pricDetail?seq=27",
          "https://www.sk7mobile.com/prod/data/callingPlanView.do?prodCd=PD00000331",
          "https://m.liivm.com/rateplan/plans/productsKp"]
    bad = ["https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=937",
           "https://www.uplusumobile.com/event-benefit/event/ongoing/12135",
           "https://www.liivm.com/info/rateplan/rateplan/infoLteGain",
           "https://m.liivm.com/biz/event/ongoing-detailed/01/EVT0001429",
           "https://m.liivm.com/rateplan/plans/closed", "https://m.liivm.com/rateplan/plans/corp?detailNo=1"]
    assert all(path_allowed(u) for u in ok) and not any(path_allowed(u) for u in bad)
    allowed = ["mvnohub.kr", "sk7mobile.com"]
    out = filter_sources([{"url": ok[3], "title": ""}, {"url": ok[0], "title": ""}], allowed, official={"mvnohub.kr"})
    assert [s["title"] for s in out] == ["알뜰폰허브 요금제 목록", "SK7모바일 요금제 상세"]       # 공공 먼저


def test_pinned_panel_needs_no_api():
    ev = pinned_panel("mobile_plan")
    assert [x["title"] for x in ev["sources"]] == ["스마트초이스 요금제 비교", "알뜰폰허브 요금제 목록"]
    assert ev["notice"] == SEARCH_NOTICE and pinned_panel("없는주제")["sources"] == []


def test_pinned_first_and_same_page_not_repeated():
    resp = NS(output=[NS(type="web_search_call", action=NS(sources=[
        NS(url="https://m.smartchoice.or.kr/smc/plan/planCompare.do?type=m", title=""),      # 고정 링크와 같은 페이지
        NS(url="https://www.sk7mobile.com/prod/data/callingPlanList.do?refCode=USIM", title="SK7 유심 요금제"),
    ]))])
    allowed = ["smartchoice.or.kr", "mvnohub.kr", "sk7mobile.com"]
    ev = GPTSearch(model="m", client=FakeClient(resp)).panel("q", allowed, topic="mobile_plan")
    assert [x["title"] for x in ev["sources"]] == ["스마트초이스 요금제 비교", "알뜰폰허브 요금제 목록", "SK7 유심 요금제"]


def test_hellomobile_plan_pages_only():
    ok = ["https://direct.lghellovision.net/rate/rateViewUsim.do", "https://direct.lghellovision.net/esim/everythingEsim.do?idx=1"]
    bad = ["https://direct.lghellovision.net/event/viewEventDetailGuest.do?idxOfEvent=586",
           "https://direct.lghellovision.net/mgm/rcmndmbr.do?pcode=1"]
    assert all(path_allowed(u) for u in ok) and not any(path_allowed(u) for u in bad)


def test_private_comparison_site_not_allowed():
    from spending_agent.search.plan_spec import ALLOWED_DOMAINS
    assert "moyoplan.com" not in [d["domain"] for d in ALLOWED_DOMAINS]
    assert filter_sources([{"url": "https://www.moyoplan.com/plans/18763", "title": "x"}],
                          [d["domain"] for d in ALLOWED_DOMAINS]) == []


def test_ad_click_url_is_not_shown():
    ad = "https://www.google.com/aclk?sa=L&adurl=https://www.moyoplan.com?utm_source%3Dgoogle"
    assert filter_sources([{"url": ad, "title": "광고"}], ["moyoplan.com"]) == []          # 광고 리다이렉트는 허용 도메인 밖
    assert clean_url("https://www.moyoplan.com/plans/1?utm_source=google&gclid=x&gad_source=1") ==         "https://www.moyoplan.com/plans/1"


def test_query_guard_strips_pii_and_rejects_money():
    assert safe_query("알뜰폰 요금제 010-1234-5678 a@b.com 월 10GB") == "알뜰폰 요금제 월 10GB"
    for bad in ["알뜰폰 요금제 월 65,780원 이하", "요금제 3만원 이하", "요금제 3 만 원", ""]:
        with pytest.raises(ValueError):
            safe_query(bad)
    client = FakeClient(NS(output=[]))
    with pytest.raises(ValueError):
        GPTSearch(model="m", client=client).panel("요금제 월 55,000원", ALLOWED)
    assert client.calls == []                                           # 보내기 전에 막는다


def test_pinned_links_respect_allowlist():
    ev = GPTSearch(model="m", client=FakeClient(NS(output=[]))).panel("q", ["sk7mobile.com"], topic="mobile_plan")
    assert ev["sources"] == []


def test_page_opening_result_is_rejected():
    resp = NS(output=[NS(type="web_search_call", action=NS(type="search", sources=[])),
                      NS(type="web_search_call", action=NS(type="open_page", url="https://kt.com/x"))])
    g = GPTSearch(model="m", client=FakeClient(resp))
    with pytest.raises(RuntimeError):
        g.panel("q", ALLOWED)
    assert g.last_actions == ["search", "open_page"]
