"""LangGraph 에이전트 테스트 (FakeLLM, 네트워크 없음).

    python -m unittest tests.test_agent -v
"""
import json
import pathlib
import tempfile
import unittest

from agent import graph, validator
from agent.llm import FakeLLM, LLMReply
from pipeline import features, ingest, synthetic

FILES = ["card_a.csv", "card_b.csv", "bank.csv", "ledger.csv"]
SECRET = "비밀 검색 요약: ○○마트 4,980원"


class AgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        synthetic.write()
        truth = json.loads((synthetic.OUT / "truth.json").read_text(encoding="utf-8"))
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        ingest.DB_PATH = pathlib.Path(cls.tmp.name) / "user.db"
        con = ingest.connect()
        for f in FILES:
            ingest.ingest_file(con, synthetic.OUT / f, "u", [truth["user_self_name"]])
        features.compute(con, "u")
        con.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tool_call_then_grounded_answer(self):
        llm = FakeLLM([
            LLMReply(calls=[{"name": "forecast_goal",
                             "args": {"goal_amount": 2000000, "deadline": "2027-08-31", "current_saved": 500000}}]),
            LLMReply(text="목표까지 매달 150,000원이 필요합니다. (출처: 사용자 거래내역)"),
        ])
        final, ui = graph.run(llm, "u", "200만 원 모을 수 있을까? 지금 50만 원 있어")
        self.assertEqual(final["validation"], {"ok": True})
        self.assertIn("150,000", final["answer"])
        self.assertEqual(final["tool_results"][0]["value"]["monthly_required"], 150000)
        self.assertEqual(ui, [])

    def test_user_id_injected_not_chosen_by_llm(self):
        llm = FakeLLM([LLMReply(calls=[{"name": "detect_recurring", "args": {}}]), LLMReply(text="정기결제를 찾았습니다.")])
        final, _ = graph.run(llm, "u", "정기결제 보여줘")
        self.assertEqual(final["tool_results"][0]["status"], "ok")
        llm2 = FakeLLM([LLMReply(calls=[{"name": "detect_recurring", "args": {"user_id": "someone_else"}}]),
                        LLMReply(text="오류")])
        final2, _ = graph.run(llm2, "u", "남의 정기결제")
        self.assertEqual(final2["tool_results"][0]["status"], "error")  # user_id 인자를 받지 않음

    def test_search_result_never_reaches_llm_or_state(self):
        llm = FakeLLM([LLMReply(calls=[{"name": "open_search_panel", "args": {"query": "달걀 10구"}}]),
                       LLMReply(text="검색 창을 띄웠어요. 판매처에서 확인한 가격을 알려주세요.")], search_text=SECRET)
        final, ui = graph.run(llm, "u", "달걀 어디서 사지?")
        self.assertEqual(len(ui), 1)
        self.assertEqual(ui[0]["text"], SECRET)                       # UI 에는 그대로
        self.assertIn("판매처에서 확인", ui[0]["notice"])
        self.assertNotIn(SECRET, json.dumps(final, ensure_ascii=False))        # 최종 state 에 없음
        self.assertNotIn(SECRET, json.dumps(llm.chat_inputs, ensure_ascii=False))  # LLM 입력에 없음
        self.assertEqual(llm.search_queries, ["달걀 10구"])

    def test_validator_catches_made_up_number(self):
        llm = FakeLLM([
            LLMReply(calls=[{"name": "forecast_goal", "args": {"goal_amount": 2000000, "deadline": "2027-08-31"}}]),
            LLMReply(text="매달 173,000원을 모으면 됩니다."),          # 지어낸 숫자
            LLMReply(text="매달 200,000원이 필요합니다."),              # 재시도: Tool 값
        ])
        final, _ = graph.run(llm, "u", "200만 원 목표")
        self.assertTrue(any("[검증기]" in h.get("text", "") for h in final["history"] if h["role"] == "user"))
        self.assertEqual(final["validation"], {"ok": True})

    def test_validator_removes_sentence_after_retry(self):
        llm = FakeLLM([LLMReply(text="넷플릭스는 월 99,999원입니다. 해지는 앱에서 하세요."),
                       LLMReply(text="넷플릭스는 월 88,888원입니다. 해지는 앱에서 하세요.")])
        final, _ = graph.run(llm, "u", "넷플릭스 얼마야")
        self.assertEqual(final["validation"]["action"], "removed")
        self.assertNotIn("88,888", final["answer"])
        self.assertIn("해지는 앱에서", final["answer"])

    def test_validator_units(self):
        tool = [{"value": {"monthly_surplus": 424970, "required": 150000}}]
        self.assertEqual(validator.check("여유자금은 약 42.5만 원, 필요액은 15만 원", tool, []), [])
        self.assertTrue(validator.check("필요액은 17만 원", tool, []))

    def test_step_limit(self):
        loop = [LLMReply(calls=[{"name": "get_source_info", "args": {"source_id": "P02_KCA_PRICE"}}])] * 20
        final, _ = graph.run(FakeLLM(loop), "u", "무한 반복")
        self.assertIn("멈췄습니다", final["answer"])


if __name__ == "__main__":
    unittest.main()
