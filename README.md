# Multi_agent_project

AI 에이전트 기반 목표 지향형 소비 관리 서비스.

## 설정
`.env` 파일에 공공데이터포털 인증키를 넣는다 (git에 올리지 않음).
```
DATA_GO_KR_KEY=...
```

## 데이터 수집기
### 한국소비자원 생필품 가격 (P02, data.go.kr 15158701, 출처: 한국소비자원 / 공공누리 1유형)
```
python collectors/kca_price.py all              # 판매점·상품·기준코드 + 최신 조사일 가격
python collectors/kca_price.py prices --day 20261106
```
- 결과: `data/prices.db` (SQLite). 조회는 `v_kca_price` 뷰 (상품명·매장명·지역·단위가격 조인됨)
- 원본 응답: `data/raw/kca/` (gzip), 호출 기록: `fetch_log` 테이블
- 조사일은 금요일. 중단 후 재실행하면 이어서 수집

### 수집기 현황
`pip install -r requirements.txt` 후 프로젝트 루트에서 실행. 결과는 모두 `data/prices.db`, 호출 기록은 `fetch_log`.

| ID | 스크립트 | 상태 |
|---|---|---|
| P02 생필품 가격 (한국소비자원) | `collectors/kca_price.py` | 동작 |
| P01 우체국알뜰폰 요금제 | `collectors/epost_mvno.py` | 활용신청 승인 대기 |
| P04 온라인수집가격 (통계청) | `collectors/kostat_online_price.py` | 활용신청 승인 대기, 응답 필드 검증 필요 |
| P09 aT 가격추이 | `collectors/at_price_trend.py` | 활용신청 승인 대기, 응답 필드 검증 필요 |
| P10 서울교통공사 운임 | `collectors/seoul_metro_fare.py` | 승인 대기, 오퍼레이션 경로 미확인 |
| L2 수동 DB (M02, M08) | `collectors/manual_db.py` + `data_manual/*.yaml` | 동작 (`--check`로 검증만) |

수동 DB는 YAML이 원본이다. 모든 항목에 `source_url`, `source_grade`, `as_of`, `review_by` 필수.
`v_manual_rule_active.usable` 이 `ok` 인 규칙만 계산에 쓴다 (`needs_official_source`·`stale`·`inactive` 는 보류).

## 거래내역 파이프라인 (`pipeline/`)
```
python -m pipeline.synthetic                       # 합성 CSV 4종 + 정답(truth.json) 생성 → data/synthetic/
python -m pipeline.ingest data/synthetic/*.csv --user u_demo --self-name 김하늘
python -m pipeline.merchants --user u_demo         # 가맹점 사전(M06) 적용
python -m pipeline.features --user u_demo          # 정기결제 탐지 + F01~F08
python -m unittest discover tests -v               # 정답 대비 테스트
```
- 인코딩 자동 판별(utf-8/cp949), 헤더 위 안내문 건너뛰기, 포맷 자동 인식 (`pipeline/formats.py`에 템플릿 추가)
- 거래 유형: spend / refund(음수) / income / transfer / card_settlement
- 이중 계산 방지: 은행의 카드대금 출금, 본인 계좌 이체는 지출 합계에서 제외 (`v_spending` 뷰)
- 중복: 같은 원본 행·같은 승인번호는 확정 중복(저장 안 함), 원천 ID 없는 (날짜·금액·가맹점) 일치는 후보로 표시만
- 저장: `data/user.db` (사용자 데이터는 공공 데이터 DB와 분리)
- 가맹점 사전: `data_manual/merchants.yaml` (브랜드·카테고리·태그, 위에서부터 먼저 맞는 것)
- 파생 지표 (`pipeline/features.py`, 규칙 기반, 버전 기록):

| ID | 지표 | 저장 |
|---|---|---|
| F01 | 배달앱 주간 건수·금액 | feature_weekly |
| F02 | 편의점 1만 원 미만 결제 | feature_weekly |
| F03 | 택시 건수·심야 비율 (시각 있는 거래만) | feature_weekly |
| F04 | 카페 브랜드별 결제, 탄소중립포인트 참여 브랜드 확인 전에는 혜택 미계산 | feature_weekly |
| F05 | 가맹점 내 결제액 분포 (5회 이상) | feature_period |
| F06 | 무료체험 → 유료 전환 | feature_period |
| F07 | 같은 구독 같은 달 중복 결제 | feature_period |
| F08 | 교통 패턴 변화 (최근 2개월 vs 이전, 광역 비중) | feature_weekly / feature_period |
| - | 정기결제 (금액 ±10%, 간격 25~35일 등, 가맹점 결제의 80% 이상이 주기적일 때) | recurring |

## 키가 별도인 외부 API
- 온통청년 청년정책(P14): `https://www.youthcenter.go.kr/go/ythip/getPlcy` — 온통청년 회원가입 후 마이페이지에서 인증키 신청, 담당자 심사 후 발급

## MCP Tool 서버 (`mcp_server.py`, `tools/`)
```
python mcp_server.py                                   # stdio 서버
claude mcp add spending -- python <프로젝트 경로>/mcp_server.py   # Claude Code에 등록
```
모든 Tool은 `{status, value, calc, sources, as_of, caveats}`를 돌려준다. `status`가 `needs_check`/`no_data`면 에이전트는 숫자를 말하지 않는다.

| Tool | 데이터 | 비고 |
|---|---|---|
| `detect_recurring` | 거래내역 | 정기결제 목록·다음 예상일 |
| `check_subscriptions` | 거래내역 + M03 | 연결제·하위 요금제(공식 출처일 때만 절감액), 중복 결제(F07), 무료체험 전환(F06) |
| `forecast_goal` | 거래내역 | 필요 월 저축액, 최근 3개월 여유자금, 기한 시점 예상액 |
| `compare_mobile_plan` | P01 (요청 시 호출, 14일 캐시) | 위약금·결합할인 해제 반영 12개월 순절감액. 약정 남으면 위약금 필수 |
| `calc_transit_refund` | M02 | 규칙이 공식 확인 전이면 needs_check |
| `get_price_baseline` | P02 (로컬 스냅샷) + P04 (요청 시 호출, 3일 캐시) | 기준선, 실시간 최저가 아님 |
| `calc_bulk_purchase` | 사용자 확인 가격 | 검색 결과 가격 거부 |
| `record_user_price` | 사용자 확인 가격 | user_verified / receipt_ocr / capture_ocr 만 저장 |
| `check_benefit_programs` | M08 | usable 상태 포함 |
| `get_source_info` | 출처 레지스트리 | |

API 응답 캐시는 `data/prices.db`의 `api_cache` 테이블 (인증키는 캐시 키·로그에 남기지 않음).
아직 없음: `match_strategies`, `evaluate_recommendation_outcome` (추천 이벤트 로깅 후).
