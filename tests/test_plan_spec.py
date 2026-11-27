from spending_agent.search.plan_spec import (
    ALLOWED_DOMAINS,
    build_query,
    build_search_request,
    estimate_monthly_gb,
    to_spec,
)


def test_daily_youtube_outside_needs_more_data_and_throttle():
    a = {"activities": ["video"], "outside": "2to3h", "wifi": "half", "budget_monthly": 20000}
    est = estimate_monthly_gb(a)
    assert est["kind"] == "추정" and 0 < est["low"] < est["high"]
    spec = to_spec(a, est)
    assert spec["profile"] == "밖에서 영상 위주"
    assert spec["throttle_mbps_min"] == 3
    assert spec["data_gb_min"] >= est["high"]


def test_music_only_needs_far_less_than_video():
    music = to_spec({"activities": ["music"], "outside": "2to3h", "wifi": "half"})
    video = to_spec({"activities": ["video"], "outside": "2to3h", "wifi": "half"})
    assert music["profile"] == "음악 위주" and music["throttle_mbps_min"] == 1
    assert music["data_gb_min"] < video["data_gb_min"]


def test_measured_usage_wins_over_estimate():
    est = estimate_monthly_gb({"activities": ["video"], "outside": "gt3h", "measured_gb_monthly": 8})
    assert est["kind"] == "실측" and est["low"] == 8 and est["high"] == 8.8
    assert to_spec({"measured_gb_monthly": 8}, est)["data_gb_min"] == 9


def test_more_wifi_means_less_data():
    base = {"activities": ["sns_web"], "outside": "1h"}
    assert estimate_monthly_gb({**base, "wifi": "rarely"})["high"] > estimate_monthly_gb({**base, "wifi": "mostly"})["high"]


def test_unverified_rates_are_flagged():
    assert any("확인되지" in c for c in estimate_monthly_gb({"activities": ["music"]})["caveats"])


def test_no_activity_cannot_estimate():
    est = estimate_monthly_gb({"activities": []})
    assert est["high"] == 0 and est["caveats"]
    assert to_spec({"activities": []}, est)["data_gb_min"] == 1


def test_query_has_spec_and_budget_only():
    spec = to_spec({"activities": ["video"], "outside": "1h", "heavy_calls": True, "budget_monthly": 20000,
                    "family_bundle": True})
    q = build_query(spec)
    assert q.startswith("알뜰폰 요금제") and "소진 후 3Mbps" in q and "통화 무제한" in q and "월 2만원 이하" in q
    assert "결합" not in q                                    # 개인 사정은 검색어에 넣지 않는다
    assert any("결합" in c for c in spec["caveats"])           # 대신 화면 주의 문구로


def test_search_request_uses_allowed_domains_and_assumptions():
    req = build_search_request({"activities": ["video", "music"], "outside": "2to3h", "wifi": "mostly"})
    assert req["allowed_domains"] == [d["domain"] for d in ALLOWED_DOMAINS]
    assert req["spec"]["assumptions"] and "GB" in req["query"]
