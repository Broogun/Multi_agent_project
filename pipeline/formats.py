"""거래내역 CSV 포맷 템플릿.

각 템플릿은 헤더 컬럼으로 자동 인식한다 (required 가 모두 있으면 매칭).
실제 카드사·은행 파일이 들어오면 여기에 템플릿만 추가하면 된다.
컬럼 의미:
  date / datetime : 거래일(시)        amount : 금액 (단일 컬럼)
  out / in        : 출금액 / 입금액    merchant : 가맹점·적요
  approval_no     : 승인번호 (원천 ID)  installment : 할부 표기
  cancel          : 취소 표기           type : 가계부앱의 지출/수입/이체 구분
"""

FORMATS = [
    {
        "id": "card_a",
        "name": "카드사 A형 (이용일자·이용가맹점)",
        "kind": "card",
        "required": ["이용일자", "이용가맹점", "이용금액"],
        "columns": {
            "date": "이용일자", "merchant": "이용가맹점", "amount": "이용금액",
            "installment": "할부개월", "approval_no": "승인번호", "cancel": "취소여부",
        },
        "date_formats": ["%Y.%m.%d", "%Y-%m-%d", "%Y%m%d"],
        "cancel_values": ["Y", "취소"],
    },
    {
        "id": "card_b",
        "name": "카드사 B형 (승인일시·가맹점명)",
        "kind": "card",
        "required": ["승인일시", "가맹점명", "승인금액(원)"],
        "columns": {
            "datetime": "승인일시", "merchant": "가맹점명", "amount": "승인금액(원)",
            "installment": "결제방법", "approval_no": "승인번호", "cancel": "상태",
        },
        "date_formats": ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M"],
        "cancel_values": ["취소", "승인취소"],
    },
    {
        "id": "bank",
        "name": "은행 거래내역 (출금액·입금액)",
        "kind": "bank",
        "required": ["거래일시", "적요", "출금액", "입금액"],
        "columns": {
            "datetime": "거래일시", "merchant": "적요", "out": "출금액", "in": "입금액",
            "memo": "내용", "balance": "잔액",
        },
        "date_formats": ["%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d"],
    },
    {
        "id": "ledger",
        "name": "가계부앱 내보내기 (날짜·타입·금액)",
        "kind": "ledger",
        "required": ["날짜", "타입", "내용", "금액"],
        "columns": {
            "date": "날짜", "time": "시간", "type": "타입", "category": "대분류",
            "merchant": "내용", "amount": "금액", "method": "결제수단", "memo": "메모",
        },
        "date_formats": ["%Y-%m-%d", "%Y.%m.%d"],
        "type_map": {"지출": "spend", "수입": "income", "이체": "transfer"},
    },
]

# 은행 출금 적요가 이 패턴이면 카드대금 결제(정산) → 카드 거래와 이중 계산되므로 지출에서 제외
CARD_SETTLEMENT_PATTERNS = [
    "카드대금", "카드결제", "신한카드", "KB국민카드", "국민카드", "삼성카드", "현대카드",
    "롯데카드", "우리카드", "하나카드", "BC카드", "비씨카드", "NH농협카드", "농협카드",
]
# 은행 적요에서 이체로 보는 패턴 (본인 계좌 여부는 사용자 프로필 self_names 로 판단)
TRANSFER_PATTERNS = ["이체", "송금", "토스", "카카오페이송금", "오픈뱅킹"]
