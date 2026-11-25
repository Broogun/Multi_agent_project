"""전문 검색 에이전트 프로토타입 테스트 (가짜 LLM, 네트워크 없음)."""
import pytest

from spending_agent import contracts
from spending_agent.search import specialists as sp
from spending_agent.search.directory import DOMAINS

SECRET = "비밀 그라운딩 원문 [출처: 지어낸 기관]"


class FakeLLM:
    def __init__(self, route: dict, grounded_text: str = SECRET, sources=None):
        self.route = route
        self.grounded_text = grounded_text
        self.sources = sources if sources is not None else [{"title": "smartchoice.or.kr", "uri": "https://r/1"}]
        self.prompts = []

    def json(self, model, prompt):
        self.prompts.append(("json", prompt))
        return self.route

    def grounded(self, model, prompt):
        self.prompts.append(("grounded", prompt))
        return sp.Grounded(text=self.grounded_text, sources=self.sources, suggestions_html="<div>제안</div>",
                           queries=["알뜰폰 SKT 15GB"])

    def text(self, model, prompt):
        self.prompts.append(("text", prompt))
        return "다듬은 답변"


def test_need_info_when_required_spec_missing():
    llm = FakeLLM({"domain": "telecom", "spec": {"data_gb": "15"}})
    r = sp.run(llm, "통신비 줄이고 싶어. 데이터 15GB")
    assert r.status == "need_info"
    assert "월 요금" in r.question
    assert not any(k == "grounded" for k, _ in llm.prompts)  # 스펙이 모자라면 검색하지 않음


def test_ok_returns_contract_panel_with_metadata_sources():
    llm = FakeLLM({"domain": "telecom", "spec": {"data_gb": "15", "budget_monthly": "20000", "network": "SKT"}})
    r = sp.run(llm, "...")
    assert r.status == "ok"
    assert set(r.panel) == set(contracts.SearchPanelEvent.__annotations__)
    assert r.panel["sources"] == llm.sources          # 출처는 메타데이터에서만
    assert r.panel["text"] == "다듬은 답변"
    assert r.panel["notice"] == contracts.SEARCH_NOTICE


def test_refine_prompt_forbids_new_facts_and_sources():
    llm = FakeLLM({"domain": "grocery", "spec": {"household": "1인"}})
    sp.run(llm, "...")
    refine_prompt = next(p for k, p in llm.prompts if k == "text")
    assert "새로운 사실" in refine_prompt and "출처 URL" in refine_prompt


def test_grounded_text_not_kept_in_result():
    llm = FakeLLM({"domain": "grocery", "spec": {"household": "1인"}})
    r = sp.run(llm, "...")
    assert SECRET not in repr(r)  # 다듬기 후 중간 결과는 결과 객체에 남지 않음


def test_no_sources_retries_once_then_unverified():
    llm = FakeLLM({"domain": "grocery", "spec": {"household": "1인"}}, sources=[])
    r = sp.run(llm, "...", do_refine=False)
    grounded_calls = [p for k, p in llm.prompts if k == "grounded"]
    assert len(grounded_calls) == 2 and grounded_calls[1].startswith(sp.RETRY_PREFIX)
    assert r.status == "unverified"
    assert "검색 출처 없이" in r.panel["notice"]


def test_out_of_scope():
    r = sp.run(FakeLLM({"domain": "stocks", "spec": {}}), "주식 뭐 사?")
    assert r.status == "out_of_scope"


@pytest.mark.parametrize("value", ["010-1234-5678", "test@example.com", "123-456-7890123"])
def test_pii_removed_from_spec_and_prompt(value):
    llm = FakeLLM({"domain": "telecom", "spec": {"data_gb": "15", "budget_monthly": f"2만원 {value}"}})
    r = sp.run(llm, "...")
    assert value not in r.spec["budget_monthly"]
    grounded_prompt = next(p for k, p in llm.prompts if k == "grounded")
    assert value not in grounded_prompt


def test_unknown_spec_keys_dropped():
    llm = FakeLLM({"domain": "telecom", "spec": {"data_gb": "15", "budget_monthly": "2만", "income": "300만"}})
    r = sp.run(llm, "...")
    assert "income" not in r.spec


def test_every_domain_has_sites_angles_and_required_or_optional_spec():
    for key, d in DOMAINS.items():
        assert d["sites"] and d["angles"] and d["spec"], key
        assert "{" not in sp.build_specialist_prompt(key, {}), key  # 템플릿 치환 누락 없음
