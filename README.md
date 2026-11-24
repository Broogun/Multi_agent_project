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
