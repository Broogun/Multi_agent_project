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
