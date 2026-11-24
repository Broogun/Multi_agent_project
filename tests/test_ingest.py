"""거래내역 파이프라인 테스트: 합성 데이터의 정답(truth.json)과 비교.

    python -m unittest tests.test_ingest -v
"""
import json
import pathlib
import tempfile
import unittest

from pipeline import ingest, synthetic

FILES = ["card_a.csv", "card_b.csv", "bank.csv", "ledger.csv"]


class IngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        synthetic.write()
        cls.truth = json.loads((synthetic.OUT / "truth.json").read_text(encoding="utf-8"))
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        ingest.DB_PATH = pathlib.Path(cls.tmp.name) / "user.db"
        cls.con = ingest.connect()
        cls.results = [ingest.ingest_file(cls.con, synthetic.OUT / f, "u", [cls.truth["user_self_name"]]) for f in FILES]
        cls.rep = ingest.report(cls.con, "u")

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def q(self, sql, *a):
        return self.con.execute(sql, a).fetchall()

    def test_all_rows_parsed(self):
        for r in self.results:
            self.assertEqual(r["failed"], [], r["file"])

    def test_encoding_and_header_offset(self):
        a = self.results[0]
        self.assertEqual(a["encoding"], "cp949")  # 카드사 A는 cp949 + 안내문 2줄
        self.assertGreater(a["inserted"], 0)

    def test_net_spending_matches_truth(self):
        # 카드대금 정산·이체 제외, 환불 차감, 카드와 겹치는 가계부 행(중복 후보) 제외
        self.assertEqual(self.rep["net_spending"], self.truth["net_spending_total"])

    def test_card_settlement_excluded(self):
        self.assertEqual(self.rep["by_type"].get("card_settlement"), self.truth["card_settlement_rows"])

    def test_self_transfer_not_spending(self):
        rows = self.q("SELECT txn_type FROM txn WHERE merchant_raw = ?", self.truth["user_self_name"])
        self.assertEqual(len(rows), self.truth["self_transfer_rows"])
        self.assertTrue(all(r[0] == "transfer" for r in rows))

    def test_auto_debit_is_spending(self):
        rows = self.q("SELECT txn_type FROM txn WHERE merchant_raw IN ('월세', 'KT 통신요금')")
        self.assertEqual({r[0] for r in rows}, {"spend"})

    def test_refund_negative_and_linked(self):
        rows = self.q("SELECT amount, linked_txn_id FROM txn WHERE txn_type='refund'")
        self.assertEqual(len(rows), self.truth["refunds"])
        self.assertLess(rows[0][0], 0)
        self.assertIsNotNone(rows[0][1])

    def test_installment(self):
        t = self.truth["installment_txn"]
        r = self.q("SELECT installment_months FROM txn WHERE merchant_raw=?", t["merchant"])
        self.assertEqual(r[0][0], t["months"])

    def test_same_day_same_store_kept(self):
        s = self.truth["same_day_same_store_legit"]
        rows = self.q("SELECT dup_status FROM txn WHERE occurred_date=? AND merchant_raw=?", s["date"], s["merchant"])
        self.assertGreaterEqual(len(rows), s["count_min"])
        self.assertTrue(all(r[0] is None for r in rows))  # 승인번호가 달라 후보 아님

    def test_ledger_overlap_flagged_not_deleted(self):
        n = self.q("SELECT count(*) FROM txn WHERE format_id='ledger' AND dup_status='candidate'")[0][0]
        self.assertEqual(n, self.truth["ledger_rows_overlapping_cards"])

    def test_reupload_idempotent(self):
        before = self.q("SELECT count(*) FROM txn")[0][0]
        for f in FILES:
            r = ingest.ingest_file(self.con, synthetic.OUT / f, "u", [self.truth["user_self_name"]])
            self.assertEqual(r["inserted"], 0, f)
        self.assertEqual(self.q("SELECT count(*) FROM txn")[0][0], before)

    def test_unknown_format_rejected(self):
        p = pathlib.Path(self.tmp.name) / "weird.csv"
        p.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
        with self.assertRaises(ingest.FormatError):
            ingest.read_csv(p)


if __name__ == "__main__":
    unittest.main()
