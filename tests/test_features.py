"""가맹점 사전 + 정기결제 탐지 + F01~F08 테스트 (합성 데이터 정답과 비교).

    python -m unittest tests.test_features -v
"""
import json
import pathlib
import sqlite3
import tempfile
import unittest

from pipeline import features, ingest, merchants, synthetic

FILES = ["card_a.csv", "card_b.csv", "bank.csv", "ledger.csv"]


class FeatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        synthetic.write()
        cls.truth = json.loads((synthetic.OUT / "truth.json").read_text(encoding="utf-8"))
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        ingest.DB_PATH = pathlib.Path(cls.tmp.name) / "user.db"
        cls.con = ingest.connect()
        for f in FILES:
            ingest.ingest_file(cls.con, synthetic.OUT / f, "u", [cls.truth["user_self_name"]])
        cls.result = features.compute(cls.con, "u")

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def q(self, sql, *a):
        return self.con.execute(sql, a).fetchall()

    def period(self, feature):
        return {k: json.loads(d) for k, d in self.q(
            "SELECT key, detail FROM feature_period WHERE user_id='u' AND feature=?", feature)}

    # --- 가맹점 사전
    def test_dictionary_matches_all_card_spend(self):
        n = self.q("SELECT count(*) FROM txn WHERE format_id LIKE 'card%' AND txn_type='spend' AND merchant_norm IS NULL")[0][0]
        self.assertEqual(n, 0)

    def test_specific_matches(self):
        cases = {"NETFLIX.COM": "넷플릭스", "쿠팡이츠": "쿠팡이츠", "쿠팡와우": "쿠팡 와우", "쿠팡": "쿠팡",
                 "CU 강남점": "CU", "경기광역버스 M4101": "광역버스", "후불교통(지하철)": "지하철",
                 "배민클럽": "배민클럽", "배달의민족": "배달의민족"}
        for raw, brand in cases.items():
            self.assertEqual(merchants.match(raw)["brand"], brand, raw)

    def test_short_ascii_pattern_not_overmatching(self):
        self.assertIsNone(merchants.match("SCUBA 다이빙"))  # 'cu'가 중간에 있어도 CU로 잡지 않음

    def test_ledger_category_kept(self):
        rows = self.q("SELECT category, category_source FROM txn WHERE format_id='ledger' AND merchant_raw='친구 결혼식 축의금'")
        self.assertEqual(rows[0], ("경조사", "source"))

    # --- 정기결제
    def test_recurring_found(self):
        found = {r[0] for r in self.q("SELECT merchant_norm FROM recurring WHERE user_id='u'")}
        for b in self.truth["recurring_brands"]:
            self.assertIn(b, found)

    def test_recurring_no_frequent_shop_false_positive(self):
        found = {r[0] for r in self.q("SELECT merchant_norm FROM recurring WHERE user_id='u'")}
        for b in self.truth["delivery_brands"] + ["스타벅스", "메가커피", "GS25", "CU", "지하철", "광역버스", "이마트"]:
            self.assertNotIn(b, found)

    # --- 지표
    def test_f01_delivery_matches_raw_count(self):
        total = self.q("SELECT sum(value) FROM feature_weekly WHERE feature='F01_delivery'")[0][0]
        raw = self.q("SELECT count(*) FROM txn WHERE txn_type='spend' AND category='배달'"
                     " AND COALESCE(dup_status,'')!='candidate'")[0][0]
        self.assertEqual(total, raw)
        self.assertGreater(total, 0)

    def test_f02_only_under_10000(self):
        for (d,) in self.q("SELECT detail FROM feature_weekly WHERE feature='F02_conv_small'"):
            d = json.loads(d)
            self.assertLess(d["amount"] / d["count"], 10000)

    def test_f04_no_benefit_without_verified_brands(self):
        for (d,) in self.q("SELECT detail FROM feature_weekly WHERE feature='F04_cafe'"):
            b = json.loads(d)["carbon_point_benefit"]
            self.assertEqual(b["eligible_count"], 0)  # 참여 브랜드 미확인 → 계산 안 함
            self.assertTrue(b["unknown_brands"])

    def test_f06_trial_conversion(self):
        t = self.truth["free_trial_conversion"]
        got = self.period("F06_trial_conversion")
        self.assertIn(t["merchant"], got)
        self.assertEqual(got[t["merchant"]]["paid_date"], t["paid"])

    def test_f07_duplicate_subscription(self):
        got = self.period("F07_duplicate_subscription")
        self.assertTrue(any(k.startswith("넷플릭스|") for k in got))
        d = next(v for k, v in got.items() if k.startswith("넷플릭스|"))
        self.assertEqual(set(d["raw_names"]), set(self.truth["duplicate_subscription"]))

    def test_f08_transit_change(self):
        d = self.period("F08_transit_change")["all"]
        self.assertEqual(d["changed"], self.truth["transit_pattern_changed"])
        self.assertGreater(d["recent_2m"]["wide_share"], d["before"]["wide_share"])

    def test_version_recorded(self):
        v = {r[0] for r in self.q("SELECT DISTINCT version FROM feature_weekly")}
        self.assertEqual(v, {features.FEATURE_VERSION})


if __name__ == "__main__":
    unittest.main()
