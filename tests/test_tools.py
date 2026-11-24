"""MCP Tool 테스트. 네트워크 없이 돈다 (API 호출은 가짜 응답으로 대체).

    python -m unittest tests.test_tools -v
"""
import asyncio
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from pipeline import features, ingest, synthetic
from tools import cache, core

FILES = ["card_a.csv", "card_b.csv", "bank.csv", "ledger.csv"]

EPOST_XML = """<?xml version="1.0" encoding="utf-8"?><AlddlChargeResponse><cmmMsgHeader><successYN>Y</successYN>
<returnCode>00</returnCode></cmmMsgHeader>
<alddlCharge><telecomName>KT</telecomName><bizName>A사</bizName><chargeName>A 10GB</chargeName><chargeDiv>무약정 후불</chargeDiv>
<chargeAmount>15000</chargeAmount><voiceAmount>300</voiceAmount><dataAmount>10240</dataAmount></alddlCharge>
<alddlCharge><telecomName>SKT</telecomName><bizName>B사</bizName><chargeName>B 5GB</chargeName><chargeDiv>무약정 후불</chargeDiv>
<chargeAmount>8000</chargeAmount><voiceAmount>100</voiceAmount><dataAmount>5120</dataAmount></alddlCharge>
<alddlCharge><telecomName>LG</telecomName><bizName>C사</bizName><chargeName>C 선불 20GB</chargeName><chargeDiv>선불</chargeDiv>
<chargeAmount>9000</chargeAmount><voiceAmount>0</voiceAmount><dataAmount>20480</dataAmount></alddlCharge>
</AlddlChargeResponse>""".encode()


class ToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        synthetic.write()
        cls.truth = json.loads((synthetic.OUT / "truth.json").read_text(encoding="utf-8"))
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        ingest.DB_PATH = pathlib.Path(cls.tmp.name) / "user.db"
        con = ingest.connect()
        for f in FILES:
            ingest.ingest_file(con, synthetic.OUT / f, "u", [cls.truth["user_self_name"]])
        features.compute(con, "u")
        con.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assertEnvelope(self, r):
        for k in ("status", "value", "calc", "sources", "as_of", "caveats"):
            self.assertIn(k, r)

    # --- 구독·정기결제
    def test_recurring_envelope(self):
        r = core.detect_recurring("u")
        self.assertEnvelope(r)
        names = {i["merchant"] for i in r["value"]["recurring"]}
        self.assertTrue(set(self.truth["recurring_brands"]) <= names)
        self.assertEqual(r["sources"][0]["id"], "U_TXN")

    def test_subscriptions_alerts(self):
        r = core.check_subscriptions("u")
        kinds = {a["type"] for a in r["value"]["alerts"]}
        self.assertEqual(kinds, {"duplicate", "trial_converted"})
        dup = next(a for a in r["value"]["alerts"] if a["type"] == "duplicate")
        self.assertEqual(dup["possible_extra"], 13500)

    def test_subscription_secondary_price_not_computed(self):
        r = core.check_subscriptions("u")
        yt = next(s for s in r["value"]["subscriptions"] if s["service"] == "유튜브 프리미엄")
        for o in yt["options"]:
            self.assertNotIn("monthly_saving", o)  # 2차 자료 가격으로는 절감액을 만들지 않음

    # --- 목표
    def test_forecast_arithmetic(self):
        r = core.forecast_goal("u", 2_000_000, "2027-08-31", 500_000, monthly_income=2_650_000)
        v = r["value"]
        self.assertEqual(v["months_left"], 10)
        self.assertEqual(v["monthly_required"], 150_000)
        self.assertEqual(v["projected_at_deadline"], 500_000 + v["monthly_surplus"] * 10)
        self.assertEqual(v["on_track"], v["gap_at_deadline"] >= 0)

    def test_forecast_no_data(self):
        self.assertEqual(core.forecast_goal("nobody", 1, "2027-01-01")["status"], "no_data")

    # --- 통신
    def test_mobile_needs_penalty_when_contract_left(self):
        self.assertEqual(core.compare_mobile_plan(55000, 5, months_left_on_contract=4)["status"], "needs_check")

    def test_mobile_net_saving(self):
        with mock.patch.object(cache, "cached_get", return_value=(EPOST_XML, "2026-11-24T10:00:00", False)):
            r = core.compare_mobile_plan(55000, 5, months_left_on_contract=4, early_termination_fee=30000,
                                         bundle_discount_lost_monthly=2000)
        c = r["value"]["candidates"]
        self.assertEqual([x["plan"] for x in c], ["B 5GB", "A 10GB"])  # 선불 제외, 데이터 조건 충족
        self.assertEqual(c[0]["net_saving_12m_switch_now"], (55000 - 8000 - 2000) * 12 - 30000)
        self.assertEqual(c[0]["net_saving_12m_after_contract"], (55000 - 8000 - 2000) * 8)

    # --- 교통
    def test_transit_unverified_rule_blocks_number(self):
        r = core.calc_transit_refund(100000, "일반", "수도권")
        if r["status"] != "no_data":  # 규칙 DB가 없는 환경이면 no_data
            self.assertEqual(r["status"], "needs_check")
            self.assertIsNone(r["value"])

    # --- 가격
    def test_bulk_rejects_search_prices(self):
        r = core.calc_bulk_purchase([{"name": "x", "price": 1000, "quantity": 1, "unit_size": 100,
                                      "source_type": "search_result"}], daily_use=1)
        self.assertEqual(r["status"], "needs_check")

    def test_bulk_shelf_life(self):
        opts = [{"name": "small", "price": 3000, "quantity": 1, "unit_size": 1000, "source_type": "user_verified"},
                {"name": "big", "price": 7000, "quantity": 1, "unit_size": 3000, "source_type": "user_verified"}]
        r = core.calc_bulk_purchase(opts, daily_use=10, shelf_life_days=150)
        self.assertEqual(r["value"]["best"], "small")  # big 은 300일이라 유통기한 초과
        r = core.calc_bulk_purchase(opts, daily_use=10, shelf_life_days=365)
        self.assertEqual(r["value"]["best"], "big")
        self.assertEqual(r["value"]["saving_vs_most_expensive"], round((3 - 7000 / 3000) * 3000))

    def test_record_user_price_rejects_search(self):
        self.assertEqual(core.record_user_price("u", "달걀", 4980, "2026-11-24", "search_result")["status"], "needs_check")
        self.assertEqual(core.record_user_price("u", "달걀", 4980, "2026-11-24", "public_api")["status"], "needs_check")
        r = core.record_user_price("u", "달걀 10구", 4980, "2026-11-24", "user_verified", seller="○○마트")
        self.assertEqual(r["status"], "ok")

    # --- 서버
    def test_server_registers_tools_with_descriptions(self):
        import mcp_server
        tools = asyncio.run(mcp_server.server.list_tools())
        self.assertEqual(len(tools), 10)
        for t in tools:
            self.assertNotEqual(t.description, t.name)


if __name__ == "__main__":
    unittest.main()
