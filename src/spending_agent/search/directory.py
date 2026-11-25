"""전문 검색 에이전트별 범위: 팀이 미리 조사한 사이트·꿀팁 관점·사용자 스펙 항목.

이건 검색 결과가 아니라 팀이 직접 확인해 관리하는 디렉터리다 (저장 가능).
- sites: 검색 범위로 프롬프트에 넣는다. last_checked 는 팀이 공식 사이트 존재를 확인한 날
- angles: 검색할 관점 (꿀팁 범주)
- spec: 사용자에게서 받을 조건. required 가 빠지면 메인 에이전트가 되묻는다
- 개인정보(이름, 소득, 계좌, 정확한 주소)는 spec 에 두지 않는다. 지역은 구 단위까지만.
근거: docs/reports/261125-1405-a-gemini-sdk-experiment.md 와 261125 검색 정확도 실험 (범위 지정 시 공식 출처 39%→66%,
      스펙 지정 시 구체 상품·조건 확인 4건 중 3건 정확).
"""
from __future__ import annotations

DOMAINS: dict[str, dict] = {
    "telecom": {
        "label": "통신비",
        "sites": [
            {"name": "스마트초이스", "url": "https://www.smartchoice.or.kr", "what": "요금제 추천·비교, 미환급액 조회, 선택약정 조회 (공공)"},
            {"name": "알뜰폰허브", "url": "https://www.mvnohub.kr", "what": "알뜰폰 공식 포털 (공공)"},
            {"name": "모요", "url": "https://www.moyoplan.com", "what": "알뜰폰 요금제 비교 (민간)"},
        ],
        "angles": ["사용량 확인", "알뜰폰 전환 시 약정·위약금·할인 후 요금·QoS", "선택약정 25% 할인",
                   "통신사 최적요금제 고지", "취약계층 감면·미환급액", "결합할인·부가서비스 정리"],
        "spec": [
            {"key": "data_gb", "ask": "한 달에 데이터를 대략 몇 GB 쓰세요?", "required": True},
            {"key": "voice", "ask": "통화는 많이 하는 편인가요? (예: 300분 이상, 무제한 필요)", "required": False},
            {"key": "budget_monthly", "ask": "월 요금은 얼마 이하면 좋겠어요?", "required": True},
            {"key": "network", "ask": "선호하는 통신망이 있나요? (SKT/KT/LG U+, 상관없음)", "required": False},
            {"key": "contract_left", "ask": "지금 약정이 남아 있나요?", "required": False},
        ],
    },
    "grocery": {
        "label": "장보기",
        "sites": [
            {"name": "참가격", "url": "https://www.price.go.kr", "what": "생필품 가격 정보 (한국소비자원)"},
            {"name": "KAMIS", "url": "https://www.kamis.or.kr", "what": "농축수산물 가격 (aT)"},
            {"name": "라스트오더", "url": "https://lastorder.co.kr", "what": "마감할인 픽업"},
            {"name": "떠리몰", "url": "https://thirtymall.com", "what": "소비기한 임박 상품"},
        ],
        "angles": ["장보기 전 계획(목록·재고 확인·소량 구매)", "싸게 사는 곳(전통시장·마감할인·새벽배송 무료배송 기준)",
                   "1인 가구 소분·냉동 보관", "지역화폐·온누리상품권", "공공 가격 정보로 시세 확인"],
        "spec": [
            {"key": "household", "ask": "몇 명이 먹나요? (예: 1인 가구)", "required": True},
            {"key": "region", "ask": "어느 지역에서 장을 보세요? (구 단위까지만)", "required": False},
            {"key": "budget_per_trip", "ask": "한 번 장볼 때 예산은 얼마 정도예요?", "required": False},
            {"key": "frequency", "ask": "얼마나 자주 장을 보세요?", "required": False},
            {"key": "cooking", "ask": "주로 어떤 걸 해 드세요? (간단 요리, 냉동식품 등)", "required": False},
        ],
    },
    "transit": {
        "label": "교통비",
        "sites": [
            {"name": "K-패스·모두의카드", "url": "https://korea-pass.kr", "what": "대중교통 환급 (국토부)"},
            {"name": "기후동행패스", "url": "https://news.seoul.go.kr/traffic", "what": "서울시 정기권·환급"},
        ],
        "angles": ["환급 유형 비교(비율형·정액형)", "대상별 환급률(청년·어르신·다자녀)", "카드 발급·등록 방법", "지역별 제도 차이"],
        "spec": [
            {"key": "region", "ask": "어느 지역에서 주로 대중교통을 타세요? (시·도)", "required": True},
            {"key": "monthly_spend", "ask": "한 달 교통비가 대략 얼마예요?", "required": True},
            {"key": "age_group", "ask": "청년(만 19~34세), 어르신 등 해당하는 대상이 있나요?", "required": False},
            {"key": "wide_bus", "ask": "광역버스나 GTX를 자주 타세요?", "required": False},
        ],
    },
    "subscription": {
        "label": "구독",
        "sites": [
            {"name": "각 서비스 공식 요금 페이지", "url": "", "what": "넷플릭스·유튜브·티빙·쿠팡 와우 등 공식 요금제"},
        ],
        "angles": ["안 쓰는 구독 해지", "하위·광고형 요금제", "연결제 할인", "통신사·카드 결합 혜택", "공식 가족 요금제 (계정 공유 중개는 약관 확인 필요)"],
        "spec": [
            {"key": "services", "ask": "지금 구독 중인 서비스가 뭐예요?", "required": True},
            {"key": "usage", "ask": "그중 자주 안 쓰는 게 있나요?", "required": False},
        ],
    },
    "hidden_money": {
        "label": "숨은 돈",
        "sites": [
            {"name": "파인", "url": "https://fine.fss.or.kr", "what": "금융감독원 금융소비자 정보 포털"},
            {"name": "어카운트인포", "url": "https://www.payinfo.or.kr", "what": "내 계좌·자동이체 통합 조회"},
            {"name": "카드포인트 통합조회", "url": "https://www.cardpoint.or.kr", "what": "여신금융협회"},
            {"name": "내보험찾아줌", "url": "https://cont.insure.or.kr", "what": "숨은 보험금 조회"},
            {"name": "스마트초이스 미환급액", "url": "https://www.smartchoice.or.kr", "what": "통신 미환급액"},
        ],
        "angles": ["휴면계좌·자동이체 정리", "카드포인트 현금화", "숨은 보험금", "통신 미환급액", "세금 환급"],
        "spec": [
            {"key": "interest", "ask": "어떤 걸 찾아보고 싶어요? (카드포인트, 휴면계좌, 보험금 등)", "required": False},
        ],
    },
}
