"""합성 거래내역 CSV 생성기 (실데이터 대신 개발·테스트용).

    python -m pipeline.synthetic            # data/synthetic/ 에 CSV 4개 + truth.json 생성

한 명의 가상 사용자 '김하늘'(서울, 20대, 여행 목표)의 2026-08~10월 3개월치.
파이프라인이 처리해야 하는 함정을 일부러 넣는다:
  - 카드사 A(cp949, 안내문 행 포함) / 카드사 B(utf-8) / 은행 / 가계부앱 포맷
  - 은행의 카드대금 출금 (이중 계산 함정), 본인 계좌 간 이체
  - 승인취소(환불), 할부
  - 같은 날 같은 가게 정상 2회 결제 (중복 후보이되 삭제하면 안 됨)
  - 구독(넷플릭스·유튜브·멜론·헬스장), 무료체험 후 유료 전환, 같은 구독 이중 결제
  - 배달앱, 카페 브랜드, 편의점, 택시, 교통
truth.json 에는 정답(순지출 합계, 정기결제 목록 등)을 기록해 테스트에서 비교한다.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "synthetic"
SELF = "김하늘"
rng = random.Random(42)
START, END = dt.date(2026, 8, 1), dt.date(2026, 10, 31)


def days():
    d = START
    while d <= END:
        yield d
        d += dt.timedelta(days=1)


def t(h0=8, h1=23):
    return dt.time(rng.randint(h0, h1), rng.randint(0, 59), rng.randint(0, 59))


card_a, card_b, truth_spend = [], [], []
appr = iter(range(30000001, 39999999))

# 정기결제 정답
SUBS = [
    # (가맹점, 금액, 결제일, 카드, 시작일)
    ("넷플릭스", 13500, 5, "a", START),
    ("유튜브프리미엄", 14900, 12, "a", START),
    ("멜론", 10900, 20, "b", START),
    ("스포애니 헬스", 59000, 1, "b", START),
    ("쿠팡와우", 7890, 15, "a", dt.date(2026, 9, 15)),  # 9/15 무료체험 0원 → 10/15 유료 전환
]


def add(card, date, time, merchant, amount, inst=1, cancel=False, approval=None, count_truth=True):
    no = approval or str(next(appr))
    row = dict(date=date, time=time, merchant=merchant, amount=amount, inst=inst, cancel=cancel, no=no)
    (card_a if card == "a" else card_b).append(row)
    if count_truth:
        truth_spend.append(-amount if cancel else amount)
    return no


for d in days():
    # 구독
    for m, amt, day_, card, since in SUBS:
        if d.day == day_ and d >= since:
            if m == "쿠팡와우" and d == since:
                add(card, d, t(0, 6), m, 0)  # 무료체험 0원 승인
            else:
                add(card, d, t(0, 6), m, amt)
    # 카페 (주 4~6회, 평일)
    if d.weekday() < 5 and rng.random() < 0.85:
        brand = rng.choice(["스타벅스", "스타벅스", "투썸플레이스", "메가커피", "이디야커피"])
        add(rng.choice("ab"), d, t(8, 10), f"{brand} {rng.choice(['강남역점', '역삼점', '선릉점'])}",
            rng.choice([4500, 4700, 5000, 6100, 2000]))
    # 배달 (주 2~4회)
    if rng.random() < 0.42:
        add("a", d, t(18, 22), rng.choice(["배달의민족", "쿠팡이츠", "요기요"]), rng.randrange(14000, 32000, 500))
    # 편의점 소액
    if rng.random() < 0.5:
        add("b", d, t(7, 23), rng.choice(["GS25 역삼점", "CU 강남점", "세븐일레븐 선릉점"]), rng.randrange(1200, 9800, 100))
    # 택시 (주 1회, 일부 심야)
    if rng.random() < 0.15:
        add("a", d, t(0, 2) if rng.random() < 0.5 else t(18, 22), "카카오T 택시", rng.randrange(8000, 26000, 100))
    # 장보기 (주 1회)
    if d.weekday() == 5:
        add("b", d, t(11, 18), rng.choice(["이마트 역삼점", "홈플러스 강서점", "롯데마트 서울역점"]), rng.randrange(35000, 90000, 10))
    # 온라인 쇼핑 (심야 포함)
    if rng.random() < 0.12:
        add("a", d, t(22, 23) if rng.random() < 0.6 else t(12, 20), rng.choice(["쿠팡", "무신사", "29CM", "올리브영온라인"]),
            rng.randrange(12000, 89000, 100))

    # 출퇴근 교통 (평일 2회): 8~9월 지하철, 10월부터 이사로 광역버스 (F08 교통 패턴 변화)
    if d.weekday() < 5:
        for h in (8, 19):
            if d.month < 10:
                add("b", d, dt.time(h, rng.randint(0, 40), 0), "후불교통(지하철)", 1550)
            else:
                add("b", d, dt.time(h, rng.randint(0, 40), 0), "경기광역버스 M4101", 3000)

# 환불: 무신사 주문 하나 취소
orig = next(r for r in card_a if r["merchant"] == "무신사")
add("a", orig["date"] + dt.timedelta(days=3), t(), "무신사", orig["amount"], cancel=True, approval=orig["no"])
# 할부: 노트북 3개월 할부 (승인 금액 전액이 이용일에 잡힘)
add("b", dt.date(2026, 9, 3), dt.time(15, 2, 11), "삼성전자 디지털프라자", 1290000, inst=3)
# 같은 날 같은 가게 정상 2회 결제 (카드사 A는 승인번호 있음 → 별개 거래)
add("a", dt.date(2026, 10, 7), dt.time(12, 10, 0), "메가커피 강남역점", 2000)
add("a", dt.date(2026, 10, 7), dt.time(15, 40, 0), "메가커피 강남역점", 2000)
# 같은 구독 이중 결제 (넷플릭스가 B카드에서도 10월부터 결제)
add("b", dt.date(2026, 10, 5), dt.time(3, 1, 0), "NETFLIX.COM", 13500)

# ------------------------------------------------------------ 은행
bank, bal = [], 1_800_000
truth_bank_spend = []


def bank_row(when, memo_main, out=0, inn=0, memo=""):
    global bal
    bal += inn - out
    bank.append({"거래일시": when.strftime("%Y.%m.%d %H:%M:%S"), "적요": memo_main,
                 "출금액": f"{out:,}" if out else "0", "입금액": f"{inn:,}" if inn else "0",
                 "잔액": f"{bal:,}", "내용": memo})


for m in (8, 9, 10):
    bank_row(dt.datetime(2026, m, 25, 9, 0), "급여", inn=2_650_000, memo="(주)하늘상사")
    bank_row(dt.datetime(2026, m, 25, 9, 30), f"{SELF}", out=500_000, memo="적금 이체")  # 본인 계좌 → 이체
    bank_row(dt.datetime(2026, m, 26, 10, 0), "KT 통신요금", out=55_000, memo="자동이체")
    truth_bank_spend.append(55_000)
    bank_row(dt.datetime(2026, m, 27, 10, 0), "월세", out=550_000, memo="자동이체 집주인")
    truth_bank_spend.append(550_000)
    bank_row(dt.datetime(2026, m, 14, 8, 0), "신한카드", out=rng.randrange(600_000, 900_000), memo="카드대금")
    bank_row(dt.datetime(2026, m, 14, 8, 5), "KB국민카드", out=rng.randrange(400_000, 700_000), memo="카드대금")
    bank_row(dt.datetime(2026, m, 10, 19, 0), "토스 이체", out=30_000, memo="박지민")  # 타인 송금 → 이체
bank.sort(key=lambda r: r["거래일시"])

# ------------------------------------------------------------ 가계부앱 (9월분만 내보냈다고 가정, 카드와 일부 겹침)
ledger = []
for r in card_b:
    if r["date"].month == 9 and not r["cancel"] and r["amount"] > 0 and rng.random() < 0.3:
        ledger.append({"날짜": r["date"].isoformat(), "시간": r["time"].strftime("%H:%M"), "타입": "지출",
                       "대분류": "식비" if "커피" in r["merchant"] or "스타벅스" in r["merchant"] else "생활",
                       "내용": r["merchant"], "금액": f"-{r['amount']}", "결제수단": "KB국민카드", "메모": ""})
ledger.append({"날짜": "2026-09-21", "시간": "13:00", "타입": "지출", "대분류": "경조사", "내용": "친구 결혼식 축의금",
               "금액": "-100000", "결제수단": "현금", "메모": "현금"})
truth_cash = [100000]


def write():
    OUT.mkdir(parents=True, exist_ok=True)
    # 카드사 A: cp949, 위에 안내문 2줄
    with open(OUT / "card_a.csv", "w", encoding="cp949", newline="") as f:
        w = csv.writer(f)
        w.writerow(["신한카드 이용내역 조회"]); w.writerow(["조회기간: 2026.08.01 ~ 2026.10.31"])
        w.writerow(["이용일자", "이용가맹점", "이용금액", "할부개월", "승인번호", "취소여부"])
        for r in sorted(card_a, key=lambda r: (r["date"], r["time"])):
            w.writerow([r["date"].strftime("%Y.%m.%d"), r["merchant"], f"{r['amount']:,}",
                        "일시불" if r["inst"] == 1 else str(r["inst"]), r["no"], "Y" if r["cancel"] else ""])
    with open(OUT / "card_b.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["승인일시", "가맹점명", "승인금액(원)", "결제방법", "승인번호", "상태"])
        for r in sorted(card_b, key=lambda r: (r["date"], r["time"])):
            w.writerow([dt.datetime.combine(r["date"], r["time"]).strftime("%Y-%m-%d %H:%M:%S"), r["merchant"],
                        r["amount"], "일시불" if r["inst"] == 1 else f"할부{r['inst']}개월", r["no"],
                        "취소" if r["cancel"] else "승인"])
    with open(OUT / "bank.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(bank[0].keys())); w.writeheader(); w.writerows(bank)
    with open(OUT / "ledger.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ledger[0].keys())); w.writeheader(); w.writerows(ledger)

    truth = {
        "user_self_name": SELF,
        "net_spending_cards": sum(truth_spend),
        "net_spending_bank": sum(truth_bank_spend),
        "net_spending_cash_ledger_only": sum(truth_cash),
        "net_spending_total": sum(truth_spend) + sum(truth_bank_spend) + sum(truth_cash),
        "ledger_rows_overlapping_cards": len(ledger) - 1,
        "card_settlement_rows": 6, "self_transfer_rows": 3,
        "refunds": 1, "installment_txn": {"merchant": "삼성전자 디지털프라자", "months": 3},
        "same_day_same_store_legit": {"date": "2026-10-07", "merchant": "메가커피 강남역점", "count_min": 2},
        "recurring": [{"merchant": m, "amount": a, "day": d} for m, a, d, _, _ in SUBS],
        "duplicate_subscription": ["넷플릭스", "NETFLIX.COM"],
        "free_trial_conversion": {"merchant": "쿠팡 와우", "trial": "2026-09-15", "paid": "2026-10-15"},
        "transit_pattern_changed": True,
        "recurring_brands": ["넷플릭스", "유튜브 프리미엄", "멜론", "헬스장"],
        "delivery_brands": ["배달의민족", "쿠팡이츠", "요기요"],
    }
    (OUT / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"생성: {OUT.relative_to(ROOT)} (카드A {len(card_a)}, 카드B {len(card_b)}, 은행 {len(bank)}, 가계부 {len(ledger)}행)")
    print(f"정답 순지출: {truth['net_spending_total']:,}원 (카드 {truth['net_spending_cards']:,} + 은행 {truth['net_spending_bank']:,} + 현금 {sum(truth_cash):,})")


if __name__ == "__main__":
    write()
